"""Aus einem Video ein CutTemplate machen.

Schritt 2 bis 4 des Scope: Shot-Boundary-Erkennung, Beat-Erkennung, Ablage
als CutTemplate.

Warum ffmpeg und nicht TransNetV2
---------------------------------
CLAUDE.md nennt fuer Schritt 8 TransNetV2. Der Scope v0 verlangt aber, dass
die Pipeline **ohne einen einzigen KI-Aufruf** durchlaeuft, und TransNetV2 ist
ein Modell. ffmpegs `scdet` ist deterministisch, liegt schon im Image und
braucht keine Gewichte.

Gemessen an einem Video mit vier bekannten harten Schnitten (320x180, 25 fps,
Schnitte auf Bild 25, 63, 81, 126): `scdet` meldet genau diese vier Bilder.
Die Werte an den Schnitten lagen bei 25 bis 66, die an allen anderen Bildern
um 0,01 -- der Abstand traegt eine robuste Schwelle.

TransNetV2 bleibt der Ausbau, wenn die Qualitaet an echtem Material nicht
reicht. Dann kehrt allerdings auch die GPU-Frage zurueck, die das CPU-only-
Chart gerade gegenstandslos macht.

Was hier NICHT erkannt wird
---------------------------
CLAUDE.md nennt als Bestandteile eines gelernten Templates auch
Uebergangsarten und Bildgroessen. Beides liefert `scdet` nicht:

- **Uebergangsart**: erkannt werden Schnittzeitpunkte, nicht ob dort hart
  geschnitten oder geblendet wurde. Jeder gefundene Punkt wird als harter
  Schnitt abgelegt. Eine Blende im Quellmaterial erzeugt einen flachen
  Anstieg statt einer Spitze und wird je nach Schwelle gar nicht oder an
  einer beliebigen Stelle darin gefunden.
- **Bildgroesse** (`shot_scale`): braucht Bildinhalt-Verstaendnis. Steht auf
  `medium`, fuer alle Einstellungen.

Beides wird hier benannt und nicht geraten. Wer die Felder braucht, traegt sie
nach oder wartet auf ein Modell.
"""

from __future__ import annotations

import logging
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import ffmpeg
from .templates import TRANSITION_CUT, BeatGrid, Cut, CutTemplate

LOG = logging.getLogger(__name__)

# Gemessen: echte Schnitte lagen bei 25 bis 66, alle anderen Bilder um 0,01.
DEFAULT_SCHWELLE = 10.0

# Kuerzeste Einstellung. Zwei Erkennungen im Abstand eines Bildes ergaeben
# sonst eine Einstellung von 40 ms, die kein Schnitt ist, sondern ein Flackern.
DEFAULT_MIN_SHOT_MS = 250

# Fuer die Beat-Erkennung reicht Mono mit 22050 Hz. Weniger Daten, gleiches
# Ergebnis -- librosa rechnet ohnehin auf einer Huellkurve.
BEAT_RATE = 22050

_FRAME = re.compile(r"frame:(\d+)\s+pts:(-?\d+)\s+pts_time:([-\d.]+)")
_WERT = re.compile(r"lavfi\.scd\.(\w+)=([\d.]+)")


class ExtraktionsFehler(RuntimeError):
    """Aus diesem Video laesst sich kein Template gewinnen."""


@dataclass(frozen=True)
class Erkennung:
    """Ein gefundener Schnittzeitpunkt."""

    at_ms: int
    score: float


def _hat_spur(pfad: str | Path, art: str) -> bool:
    ergebnis = ffmpeg.probe(
        [
            "-v", "error",
            "-select_streams", f"{art}:0",
            "-show_entries", "stream=index",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(pfad),
        ]
    )
    return ergebnis.stdout.strip() != ""


def hat_tonspur(pfad: str | Path) -> bool:
    return _hat_spur(pfad, "a")


def hat_bildspur(pfad: str | Path) -> bool:
    return _hat_spur(pfad, "v")


def schnittzeitpunkte(
    pfad: str | Path,
    schwelle: float = DEFAULT_SCHWELLE,
    min_shot_ms: int = DEFAULT_MIN_SHOT_MS,
) -> list[Erkennung]:
    """Schnittzeitpunkte in Millisekunden.

    `scdet` schreibt fuer JEDES Bild einen Wert; seine eigene Schwelle
    beeinflusst nur eine Zusatzzeile. Gefiltert wird deshalb hier -- und so
    laesst sich die Verteilung der Werte auch mitloggen.
    """
    # Vorher pruefen, statt ffmpeg mit Code 234 scheitern zu lassen. Die
    # eigene Meldung sagt, was fehlt; ffmpegs stderr laesst es raten.
    if not hat_bildspur(pfad):
        raise ExtraktionsFehler(f"{pfad} hat keine Bildspur")

    ergebnis = ffmpeg.run(
        [
            "-hide_banner", "-nostdin",
            "-i", str(pfad),
            "-filter_complex", "scdet=threshold=1,metadata=print:file=-",
            "-an",
            "-f", "null", "-",
        ]
    )

    roh: list[tuple[int, float]] = []
    zeit_ms: int | None = None
    for zeile in ergebnis.stdout.splitlines():
        treffer = _FRAME.search(zeile)
        if treffer:
            # Sekunden nur an dieser Grenze; ab hier sind es Millisekunden.
            zeit_ms = round(float(treffer.group(3)) * 1000)
            continue
        treffer = _WERT.search(zeile)
        if treffer and zeit_ms is not None and treffer.group(1) == "score":
            roh.append((zeit_ms, float(treffer.group(2))))

    if not roh:
        raise ExtraktionsFehler(
            f"{pfad}: scdet lieferte keine Werte. Hat die Datei eine Bildspur?"
        )

    ueber = [Erkennung(at_ms=t, score=s) for t, s in roh if s >= schwelle and t > 0]

    # Zu dicht aufeinander: der erste gewinnt.
    behalten: list[Erkennung] = []
    verworfen = 0
    for e in ueber:
        if behalten and e.at_ms - behalten[-1].at_ms < min_shot_ms:
            verworfen += 1
            continue
        behalten.append(e)

    spitzen = sorted((s for _, s in roh), reverse=True)[:5]
    LOG.info(
        "Schnittzeitpunkte erkannt",
        extra={
            "datei": str(pfad),
            "bilder": len(roh),
            "ueber_schwelle": len(ueber),
            "behalten": len(behalten),
            "verworfen_zu_dicht": verworfen,
            "hoechste_werte": [round(s, 1) for s in spitzen],
            "schwelle": schwelle,
        },
    )
    return behalten


def beat_grid(pfad: str | Path) -> BeatGrid | None:
    """Beat-Grid aus der Tonspur, oder None wenn es keine gibt.

    Die Tonspur wird vorher mit ffmpeg nach WAV geholt, statt librosa die
    MP4-Datei zu geben: librosa braucht dafuer audioread und damit einen
    zweiten Weg zu ffmpeg. Ein Schritt weniger, der schiefgehen kann.

    Die BPM wird aus dem MITTEL der Beat-Abstaende gerechnet, nicht aus
    librosas Tempo-Ausgabe. Gemessen an einem Klick-Track mit exakt 120 bpm
    (Klick alle 500 ms):

        librosas tempo        117,5 bpm
        Median der Abstaende  117,6 bpm
        Mittel der Abstaende  120,2 bpm

    Der Median war also keinen Deut besser -- die Abstaende wechseln zwischen
    511 und 487 ms, und der Median greift dann einen der beiden Werte statt
    der Mitte. Das Mittel trifft.

    Ausreisser fallen vorher heraus: ein uebersehener Beat verdoppelt einen
    Abstand und wuerde das Mittel verziehen. Behalten wird, was zwischen der
    Haelfte und dem Eineinhalbfachen des Medians liegt -- dafuer ist der
    Median das richtige Werkzeug.
    """
    if not hat_tonspur(pfad):
        LOG.info("keine Tonspur, kein Beat-Grid", extra={"datei": str(pfad)})
        return None

    try:
        import librosa  # noqa: PLC0415 - absichtlich spaet
        import numpy
    except ImportError as exc:
        raise ExtraktionsFehler(
            "Fuer die Beat-Erkennung fehlt librosa. "
            "Installieren mit: pip install -r worker/requirements.txt"
        ) from exc

    with tempfile.TemporaryDirectory() as d:
        wav = Path(d) / "ton.wav"
        ffmpeg.run(
            [
                "-hide_banner", "-nostdin", "-y",
                "-i", str(pfad),
                "-vn",
                "-ac", "1",
                "-ar", str(BEAT_RATE),
                "-c:a", "pcm_s16le",
                str(wav),
            ]
        )
        y, sr = librosa.load(str(wav), sr=None, mono=True)

    _, beats = librosa.beat.beat_track(y=y, sr=sr, units="time")
    offsets = [int(round(float(b) * 1000)) for b in beats]

    if len(offsets) < 2:
        LOG.warning(
            "zu wenige Beats fuer ein Grid",
            extra={"datei": str(pfad), "beats": len(offsets)},
        )
        return None

    abstaende = numpy.diff(offsets)
    median_ms = float(numpy.median(abstaende))

    # Ausreisser weg, dann das Mittel. Begruendung im Docstring.
    behalten = abstaende[
        (abstaende >= 0.5 * median_ms) & (abstaende <= 1.5 * median_ms)
    ]
    if behalten.size == 0:
        behalten = abstaende
    mittel_ms = float(numpy.mean(behalten))
    bpm = round(60000.0 / mittel_ms, 1) if mittel_ms > 0 else 0.0

    LOG.info(
        "Beat-Grid erkannt",
        extra={
            "datei": str(pfad),
            "beats": len(offsets),
            "bpm": bpm,
            "mittlerer_abstand_ms": round(mittel_ms),
            "median_abstand_ms": round(median_ms),
            "ausreisser": int(abstaende.size - behalten.size),
        },
    )
    return BeatGrid(bpm=bpm, offsets_ms=tuple(offsets))


def auf_beat(at_ms: int, grid: BeatGrid, toleranz_ms: int) -> int:
    """Zieht einen Zeitpunkt auf den naechsten Beat, wenn er nah genug liegt."""
    if not grid.offsets_ms:
        return at_ms
    naechster = min(grid.offsets_ms, key=lambda b: abs(b - at_ms))
    return naechster if abs(naechster - at_ms) <= toleranz_ms else at_ms


def template_aus_video(
    pfad: str | Path,
    name: str = "",
    *,
    schwelle: float = DEFAULT_SCHWELLE,
    min_shot_ms: int = DEFAULT_MIN_SHOT_MS,
    snap_toleranz_ms: int = 0,
    template_id: str = "",
    beat_pflicht: bool = True,
) -> CutTemplate:
    """Baut ein CutTemplate aus einem Video.

    `snap_toleranz_ms` groesser 0 zieht jeden Schnittzeitpunkt auf den
    naechsten Beat, wenn er innerhalb der Toleranz liegt. Aus gemessenen Daten
    werden damit veraenderte -- deshalb steht es auf 0 und muss verlangt
    werden.
    """
    pfad = Path(pfad)
    if not pfad.is_file():
        raise ExtraktionsFehler(f"{pfad} gibt es nicht")

    dauer = ffmpeg.duration_ms(str(pfad))
    if dauer <= 0:
        raise ExtraktionsFehler(f"{pfad}: Laufzeit ist {dauer} ms")

    # BEAT-GRID OPTIONAL MACHEN, wenn der Aufrufer es erlaubt.
    #
    # Das Produkt sind die SCHNITTE. Das Beat-Grid verbessert sie nur: es
    # entscheidet, ob ein Schnitt auf den naechsten Beat gezogen wird. Ohne
    # Grid entsteht ein vollstaendig brauchbares Template.
    #
    # Fuer die CLI bleibt es streng (`beat_pflicht=True`): wer `extract`
    # aufruft, hat ein Beat-Grid verlangt, und ein fehlendes librosa ist ein
    # kaputtes Image und kein Grund fuer ein halbes Ergebnis.
    #
    # Fuer die Extraktion aus der Oberflaeche ist es das Gegenteil. Dort hat
    # jemand ein Video hochgeladen und will ein Template; scheitert die
    # Tonanalyse an dieser einen Datei, waere es falsch, deshalb auch die
    # Schnitte wegzuwerfen. Still passiert das NICHT: der Grund steht als
    # Warnung im Log und im source_label des Templates, und das zeigt die
    # Oberflaeche in der Spalte "Quelle" an.
    grid_fehler = ""
    try:
        grid = beat_grid(pfad)
    except ExtraktionsFehler as exc:
        if beat_pflicht:
            raise
        grid = None
        grid_fehler = str(exc).split("\n")[0]
        LOG.warning(
            "Beat-Grid nicht ermittelbar, Template entsteht ohne",
            extra={"datei": str(pfad), "grund": grid_fehler},
        )

    erkennungen = schnittzeitpunkte(pfad, schwelle=schwelle, min_shot_ms=min_shot_ms)

    zeiten = [e.at_ms for e in erkennungen]
    if snap_toleranz_ms > 0 and grid is not None:
        vorher = list(zeiten)
        zeiten = [auf_beat(t, grid, snap_toleranz_ms) for t in zeiten]
        verschoben = sum(1 for a, b in zip(vorher, zeiten) if a != b)
        LOG.info(
            "Schnitte auf Beats gezogen",
            extra={"verschoben": verschoben, "von": len(zeiten)},
        )

    # cuts[0] ist immer der Anfang. Die Erkennungen sind die Anfaenge der
    # Einstellungen 1 bis n -- nicht die Grenzen davor; siehe templates.py.
    starts = [0]
    for t in zeiten:
        if t >= dauer:
            LOG.warning(
                "Schnitt hinter der Laufzeit verworfen",
                extra={"at_ms": t, "duration_ms": dauer},
            )
            continue
        if t - starts[-1] < min_shot_ms:
            continue
        starts.append(t)

    # Die letzte Einstellung braucht ebenfalls ihre Mindestlaenge.
    if len(starts) > 1 and dauer - starts[-1] < min_shot_ms:
        starts.pop()

    vorlage = CutTemplate(
        id=template_id or pfad.stem,
        name=name or pfad.stem,
        source_label=f"{pfad.name} (ohne Beat-Grid: {grid_fehler})" if grid_fehler else pfad.name,
        duration_ms=dauer,
        cuts=tuple(
            # Uebergangsart und Bildgroesse sind NICHT erkannt, siehe
            # Modulkopf. Hier stehen bewusst Vorgaben und keine Schaetzung.
            Cut(at_ms=s, transition=TRANSITION_CUT, shot_scale="medium")
            for s in starts
        ),
        beat_grid=grid,
        shot_count=len(starts),
    )
    vorlage.validate()
    LOG.info(
        "Template gebaut",
        extra={
            "id": vorlage.id,
            "shots": vorlage.shot_count,
            "duration_ms": vorlage.duration_ms,
            "bpm": grid.bpm if grid else None,
        },
    )
    return vorlage
