"""Platzhalter-Clips aus ffmpeg-lavfi.

In v0 wird kein Video erzeugt. Damit die Pipeline trotzdem ohne einen
einzigen KI-Aufruf durchlaeuft, liefert dieses Modul die N Clips: eine Flaeche
in Hanseatenblau mit dem Index der Einstellung und einem mitlaufenden
Timecode in Gold.

Der Timecode ist kein Schmuck. Er ist das Messwerkzeug: ob der Assembler an
der richtigen Millisekunde geschnitten hat, sieht man im Ergebnis nur daran,
wo der Timecode springt.

Das Worker-Image muss ein ffmpeg mit `drawtext` mitbringen (libfreetype) und
eine Schriftdatei. Fehlt eines von beidem, scheitert dieses Modul mit einer
Meldung, die sagt welches -- ein Platzhalter ohne Index und Timecode waere
zum Pruefen wertlos.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from . import ffmpeg
from .assembler import FramePlan, RenderSettings
from .templates import CutTemplate

LOG = logging.getLogger(__name__)

TEXTFARBE = "0xcaa960"  # Gold

# Laufende Zeit im Clip. Bewusst `pts:hms` statt der `timecode`-Option:
#
#   - `timecode` zaehlt Bilder (SMPTE). Das ganze Datenmodell rechnet in
#     Millisekunden; eine Bildnummer muesste man erst zurueckrechnen.
#   - `pts:hms` zeigt 0:00:02.400 und damit genau die Einheit, in der das
#     CutTemplate den Schnitt beschreibt.
#   - Die `timecode`-Option scheitert in ffmpeg 6.1.1 ohnehin mit der irrefuehrenden
#     Meldung "Both text and text file provided", auch ohne gesetztes text.
TIMECODE_TEXT = "%{pts:hms}"

# drawtext meldet einen kaputten Textausdruck nur als Warnung -- ffmpeg endet
# mit Code 0 und schreibt eine Datei mit falschem oder fehlendem Text. Ein
# Platzhalter ohne Index und Zeit ist zum Pruefen wertlos, deshalb wird
# stderr nachgesehen.
_DRAWTEXT_MECKER = ("Parsed_drawtext", "Unterminated", "Invalid chars")

# Reihenfolge der Suche nach einer Schriftdatei. MOVE_FONT_FILE gewinnt immer.
FONT_KANDIDATEN = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeMonoBold.ttf",
)


class PlaceholderError(RuntimeError):
    """Platzhalter koennen nicht erzeugt werden."""


def font_file() -> str:
    gesetzt = os.environ.get("MOVE_FONT_FILE")
    if gesetzt:
        if not Path(gesetzt).is_file():
            raise PlaceholderError(
                f"MOVE_FONT_FILE zeigt auf {gesetzt}, dort liegt keine Datei."
            )
        return gesetzt

    for kandidat in FONT_KANDIDATEN:
        if Path(kandidat).is_file():
            return kandidat

    raise PlaceholderError(
        "Keine Schriftdatei gefunden. Das Worker-Image muss eine mitbringen, "
        "oder MOVE_FONT_FILE muss auf eine zeigen. Gesucht wurde in: "
        + ", ".join(FONT_KANDIDATEN)
    )


# Maskierung fuer Werte in einer ffmpeg-Filteroption.
#
# ffmpeg entpackt zweistufig: erst wird der Graph an ',' in Filter zerlegt,
# dann jeder Filter an ':' in Optionen. Ein ':' muss deshalb BEIDE Durchgaenge
# ueberleben und braucht zwei Backslashes, ein ',' nur einen.
#
# Gemessen mit ffmpeg 6.1.1, nicht aus der Dokumentation abgeleitet:
#   text=%{pts:hms}     -> "Unterminated %{} near '{pts'"
#   text=%{pts\:hms}    -> "Unterminated %{} near '{pts'"
#   text=%{pts\\:hms}   -> sauber
#   text=A,B            -> Graph bricht
#   text=A\,B           -> sauber
#
# Der Backslash selbst muss ebenfalls beide Durchgaenge ueberstehen und steht
# zuerst in der Tabelle, damit die spaeter eingefuegten Backslashes nicht noch
# einmal behandelt werden.
_ESCAPE = (
    ("\\", "\\\\\\\\"),
    (":", "\\\\:"),
    (",", "\\,"),
    (";", "\\;"),
    ("[", "\\["),
    ("]", "\\]"),
    ("'", "\\'"),
)


def escape(wert: str) -> str:
    """Entschaerft einen Wert fuer eine ffmpeg-Filteroption.

    Behandelt wird nur der Wert. Der Aufruf bleibt ein Argument-Array; hier
    geht es um die Syntax innerhalb des Filtergraphen, nicht um eine Shell.
    """
    for zeichen, ersatz in _ESCAPE:
        wert = wert.replace(zeichen, ersatz)
    return wert


def build_args(
    index: int,
    frames: int,
    output: str | Path,
    settings: RenderSettings | None = None,
    *,
    label: str | None = None,
) -> list[str]:
    """ffmpeg-Aufruf fuer einen Platzhalter, als Argument-Array.

    Die Laenge wird in Bildern angegeben, nicht in Sekunden. `color` ist eine
    endlose Quelle; `-frames:v` schneidet sie auf genau die verlangte Zahl.
    Mit `d=<Sekunden>` rundete ffmpeg selbst: bei 25 fps wurden aus 500 ms
    13 Bilder, also 520 ms -- gemessen.
    """
    settings = settings or RenderSettings()
    font = escape(font_file())
    text = label if label is not None else f"CLIP {index:02d}"

    gross = max(24, settings.height // 8)
    klein = max(16, settings.height // 20)

    filter_kette = ",".join(
        [
            (
                f"drawtext=fontfile={font}"
                f":text={escape(text)}"
                f":fontcolor={TEXTFARBE}"
                f":fontsize={gross}"
                f":x=(w-text_w)/2"
                f":y=(h-text_h)/2-{klein}"
            ),
            (
                f"drawtext=fontfile={font}"
                f":text={escape(TIMECODE_TEXT)}"
                f":fontcolor={TEXTFARBE}"
                f":fontsize={klein}"
                f":x=(w-text_w)/2"
                f":y=(h-text_h)/2+{gross}"
            ),
        ]
    )

    return [
        "-hide_banner", "-nostdin", "-y",
        "-f", "lavfi",
        "-i",
        f"color=c={settings.background}"
        f":s={settings.width}x{settings.height}"
        f":r={settings.fps}",
        "-vf", filter_kette,
        "-frames:v", str(frames),
        "-an",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "24",
        "-pix_fmt", "yuv420p",
        "-fflags", "+bitexact",
        "-flags:v", "+bitexact",
        "-map_metadata", "-1",
        str(output),
    ]


def _run_checked(args: list[str]) -> None:
    """Fuehrt den Aufruf aus und behandelt drawtext-Warnungen als Fehler."""
    result = ffmpeg.run(args)
    mecker = [
        zeile
        for zeile in result.stderr.splitlines()
        if any(m in zeile for m in _DRAWTEXT_MECKER)
    ]
    if mecker:
        raise PlaceholderError(
            "drawtext hat den Text nicht gezeichnet, ffmpeg endete aber mit 0. "
            "Der Platzhalter waere ohne Index oder Zeit:\n  "
            + "\n  ".join(mecker)
        )


def create(
    index: int,
    frames: int,
    output: str | Path,
    settings: RenderSettings | None = None,
    *,
    label: str | None = None,
) -> Path:
    """Erzeugt einen einzelnen Platzhalter-Clip mit genau `frames` Bildern."""
    ffmpeg.require_filter("drawtext")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    _run_checked(build_args(index, frames, output, settings, label=label))
    return output


def create_for_template(
    template: CutTemplate,
    out_dir: str | Path,
    settings: RenderSettings | None = None,
) -> list[Path]:
    """Erzeugt fuer jede Einstellung des Templates einen Platzhalter.

    Jeder Clip ist genau so lang, wie der Assembler ihn braucht -- inklusive
    der Ueberlappung fuer eine folgende Blende.
    """
    ffmpeg.require_filter("drawtext")
    settings = settings or RenderSettings()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    plan = FramePlan.build(template, settings.fps)
    pfade: list[Path] = []
    for i, cut in enumerate(template.cuts):
        ziel = out_dir / f"clip-{i:02d}.mp4"
        LOG.info(
            "erzeuge Platzhalter",
            extra={"index": i, "frames": plan.source[i], "shot_scale": cut.shot_scale},
        )
        _run_checked(build_args(i, plan.source[i], ziel, settings))
        pfade.append(ziel)
    return pfade
