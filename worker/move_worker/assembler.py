"""Assembler: CutTemplate + N Clips -> fertiges MP4.

Der Schnitt ist Arithmetik. Aus den Zeitstempeln des Templates ergeben sich
Schnittlaengen und Blenden-Versaetze vollstaendig; es wird nichts geschaetzt
und nichts erkannt.

Gerechnet wird in Bildern, nicht in Sekunden
--------------------------------------------
Das Template haelt Millisekunden, weil es unabhaengig von der Aufloesung und
der Bildrate gilt. Geschnitten werden kann aber nur auf Bildgrenzen: bei
25 fps sind 500 ms genau 12,5 Bilder. Wer ffmpeg Sekunden uebergibt, laesst
ffmpeg diese Rundung still erledigen -- gemessen wurde ein Platzhalter von
520 ms, wo 500 ms verlangt waren.

Deshalb wird aus (Template, fps) zuerst ein `FramePlan` gerechnet: ganze
Bildnummern, kaufmaennisch gerundet. Die sichtbaren Laengen ergeben sich als
Differenz der gerundeten Startbilder, nicht durch Runden der Laengen selbst.
Damit ist die Summe **immer** genau `total` -- die Rundung kann sich nicht
aufsummieren.

Die Ueberlappung
----------------
Bei einer Blende sind zwei Einstellungen gleichzeitig zu sehen. Die vorherige
muss deshalb laenger geschnitten werden als sie allein zu sehen ist:

    source[i] = visible[i] + fade[i+1]

Nach Einstellung i ist der Strom `start[i] + source[i]` Bilder lang, also
genau `start[i+1] + fade[i+1]`. Genau so lang muss er sein, damit `xfade` mit
`offset = start[i+1]` und `duration = fade[i+1]` bis zum Ende Material hat.
Am Schluss steht exakt `total`.

Ein harter Schnitt ist keine Blende der Laenge 0 -- die kennt xfade nicht --
sondern ein `concat`. Beide fuegen sich in dieselbe Rechnung.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from . import ffmpeg
from .templates import TRANSITION_CROSSFADE, CutTemplate

LOG = logging.getLogger(__name__)


class AssemblyError(RuntimeError):
    """Template, Bildrate und Clips passen nicht zusammen."""


@dataclass(frozen=True)
class RenderSettings:
    """Format des Ergebnisses.

    Bewusst nicht Teil des Templates: das Template beschreibt den Schnitt,
    nicht die Aufloesung, in der er ausgegeben wird.
    """

    width: int = 1920
    height: int = 1080
    fps: int = 25
    crf: int = 20
    preset: str = "medium"
    # Hanseatenblau. Fuellfarbe, wenn ein Clip ein anderes Seitenverhaeltnis hat.
    background: str = "0x051729"


def ms_to_frames(ms: int, fps: int) -> int:
    """Millisekunden in ganze Bilder, bei .5 aufwaerts gerundet.

    Nur Integer-Arithmetik. `round()` waere hier falsch: Python rundet
    kaufmaennisch zur geraden Zahl, round(12.5) ist 12 und round(13.5) ist 14 --
    eine Rundung, die von der Nachbarschaft abhaengt, hat in einem Schnittplan
    nichts zu suchen.
    """
    if ms < 0:
        raise AssemblyError(f"Negative Zeit ({ms} ms) laesst sich nicht in Bilder rechnen")
    if fps <= 0:
        raise AssemblyError(f"Bildrate muss groesser als 0 sein, ist {fps}")
    return (ms * fps + 500) // 1000


@dataclass(frozen=True)
class FramePlan:
    """Der Schnittplan in ganzen Bildern."""

    fps: int
    total: int
    start: tuple[int, ...]
    visible: tuple[int, ...]
    fade: tuple[int, ...]
    source: tuple[int, ...]

    @classmethod
    def build(cls, template: CutTemplate, fps: int) -> "FramePlan":
        anzahl = len(template.cuts)
        total = ms_to_frames(template.duration_ms, fps)
        start = tuple(ms_to_frames(c.at_ms, fps) for c in template.cuts)

        grenzen = start + (total,)
        visible = tuple(grenzen[i + 1] - grenzen[i] for i in range(anzahl))

        # Eine Blende, die auf unter ein Bild rundet, waere fuer xfade eine
        # Dauer von 0 -- das lehnt der Filter ab. Ein Bild ist das Minimum.
        fade = tuple(
            max(1, ms_to_frames(c.transition_ms, fps))
            if c.transition == TRANSITION_CROSSFADE
            else 0
            for c in template.cuts
        )

        source = tuple(
            visible[i] + (fade[i + 1] if i + 1 < anzahl else 0) for i in range(anzahl)
        )

        plan = cls(fps=fps, total=total, start=start, visible=visible, fade=fade, source=source)
        plan.validate()
        return plan

    def validate(self) -> None:
        if self.total <= 0:
            raise AssemblyError(
                f"Bei {self.fps} fps bleibt von der Laufzeit kein ganzes Bild uebrig"
            )
        if self.start[0] != 0:
            raise AssemblyError(f"Die erste Einstellung beginnt bei Bild {self.start[0]}, nicht 0")

        for i in range(1, len(self.start)):
            if self.start[i] <= self.start[i - 1]:
                raise AssemblyError(
                    f"Bei {self.fps} fps fallen die Einstellungen {i - 1} und {i} auf dasselbe "
                    f"Bild ({self.start[i]}). Der Schnitt ist fuer diese Bildrate zu fein -- "
                    f"hoehere Bildrate wählen oder das Template anpassen."
                )

        for i, laenge in enumerate(self.visible):
            if laenge <= 0:
                raise AssemblyError(
                    f"Einstellung {i} ist bei {self.fps} fps {laenge} Bilder lang"
                )

        for i, blende in enumerate(self.fade):
            if blende == 0:
                continue
            grenze = min(self.visible[i - 1], self.visible[i])
            if blende > grenze:
                raise AssemblyError(
                    f"Einstellung {i}: die Blende ist bei {self.fps} fps {blende} Bilder lang, "
                    f"die kuerzere der beiden Einstellungen aber nur {grenze}"
                )

    # -- Umrechnungen an der Grenze zu ffmpeg -----------------------------

    def seconds(self, frames: int) -> str:
        """Bildnummer als Sekundenangabe fuer ffmpeg."""
        return f"{frames / self.fps:.6f}"

    def source_ms(self, index: int) -> int:
        """Laenge, die ein Clip mindestens mitbringen muss, in Millisekunden.

        Aufgerundet: eine Datei, die eine halbe Millisekunde zu kurz ist,
        liefert das letzte Bild nicht mehr.
        """
        return -(-self.source[index] * 1000 // self.fps)

    @property
    def total_ms(self) -> int:
        return -(-self.total * 1000 // self.fps)


def build_filter_graph(plan: FramePlan, template: CutTemplate, settings: RenderSettings) -> tuple[str, str]:
    """Baut den filter_complex und gibt ihn mit dem Label des Ergebnisses zurueck.

    Rein rechnend, ohne ffmpeg-Aufruf -- damit die Rechnung pruefbar ist, ohne
    etwas zu rendern.
    """
    teile: list[str] = []

    # 1. Jede Einstellung auf ihre Bildzahl schneiden und auf ein einheitliches
    #    Format bringen. xfade und concat verlangen auf beiden Seiten gleiche
    #    Groesse, Bildrate, Pixelformat UND Zeitbasis.
    #
    #    `fps` steht vor `trim`, damit `end_frame` Ausgabebilder zaehlt und
    #    nicht Bilder der Quelle -- sonst haengt der Schnitt an der Bildrate
    #    des Materials.
    for i in range(len(template.cuts)):
        teile.append(
            f"[{i}:v]"
            f"fps={settings.fps},"
            f"trim=end_frame={plan.source[i]},"
            f"setpts=PTS-STARTPTS,"
            f"scale=w={settings.width}:h={settings.height}"
            f":force_original_aspect_ratio=decrease,"
            f"pad=w={settings.width}:h={settings.height}"
            f":x=(ow-iw)/2:y=(oh-ih)/2:color={settings.background},"
            f"setsar=1,"
            f"format=yuv420p,"
            f"settb=1/{settings.fps}"
            f"[s{i}]"
        )

    # 2. Paarweise zusammenfuegen.
    #
    #    `trim=end_frame` nach jedem Schritt schreibt die Laenge des Stroms
    #    fest, statt sie dem Filter zu ueberlassen. Gemessen: ffmpeg 5.1
    #    (Debian bookworm, also das Worker-Image) liefert aus derselben
    #    xfade-Kette ein Bild MEHR als 6.1.1, und damit lag jeder folgende
    #    Schnitt ein Bild zu spaet -- bei gleicher Gesamtlaenge, weil
    #    `-frames:v` am Ende abschneidet. Der Fehler war also nur an der
    #    Grenze zu sehen, nicht an der Laufzeit.
    #
    #    Hier ist der Plan die Wahrheit, nicht die Buchhaltung des Filters.
    #    Liefert xfade zu WENIGE Bilder, kann trim das nicht auffuellen --
    #    dann ist das Ergebnis zu kurz und die Laengenpruefung in `assemble`
    #    schlaegt an.
    #
    #    `settb` ist noetig, weil `concat` die Zeitbasis auf 1/1000000 setzt
    #    und xfade danach mit "timebase do not match" abbricht.
    aktuell = "s0"
    for i in range(1, len(template.cuts)):
        ziel = f"x{i}"
        if template.cuts[i].transition == TRANSITION_CROSSFADE:
            verbindung = (
                f"xfade=transition=fade"
                f":duration={plan.seconds(plan.fade[i])}"
                f":offset={plan.seconds(plan.start[i])}"
            )
        else:
            verbindung = "concat=n=2:v=1:a=0"

        laenge = plan.start[i] + plan.source[i]
        teile.append(
            f"[{aktuell}][s{i}]{verbindung},"
            f"trim=end_frame={laenge},"
            f"settb=1/{settings.fps}"
            f"[{ziel}]"
        )
        aktuell = ziel

    return ";".join(teile), aktuell


def build_args(
    template: CutTemplate,
    clips: list[str | Path],
    output: str | Path,
    settings: RenderSettings | None = None,
) -> list[str]:
    """Der vollstaendige ffmpeg-Aufruf als Argument-Array."""
    settings = settings or RenderSettings()

    if len(clips) != len(template.cuts):
        raise AssemblyError(
            f"Das Template hat {len(template.cuts)} Einstellungen, "
            f"uebergeben wurden aber {len(clips)} Clips"
        )

    plan = FramePlan.build(template, settings.fps)
    graph, ergebnis = build_filter_graph(plan, template, settings)

    args = ["-hide_banner", "-nostdin", "-y"]
    for clip in clips:
        args += ["-i", str(clip)]
    args += [
        "-filter_complex", graph,
        "-map", f"[{ergebnis}]",
        # v0 hat keine Tonspur. Das Beat-Grid richtet den Schnitt aus, es
        # vertont nicht.
        "-an",
        "-c:v", "libx264",
        "-preset", settings.preset,
        "-crf", str(settings.crf),
        "-pix_fmt", "yuv420p",
        "-r", str(settings.fps),
        "-frames:v", str(plan.total),
        "-movflags", "+faststart",
        # Ohne bitexact schreibt der Encoder seine Version in den Datenstrom.
        # Dieselbe Vorlage mit denselben Clips soll dieselbe Datei ergeben.
        "-fflags", "+bitexact",
        "-flags:v", "+bitexact",
        "-map_metadata", "-1",
        str(output),
    ]
    return args


def check_clips(plan: FramePlan, clips: list[str | Path]) -> None:
    """Prueft vor dem Render, ob jeder Clip lang genug ist.

    Zu kurzes Material laesst ffmpeg eine Einstellung frueher enden; das
    Ergebnis waere still zu kurz, ein Fehler der erst beim Ansehen auffaellt.
    Deshalb vorher messen und mit Zahlen scheitern.
    """
    zu_kurz = []
    for i, clip in enumerate(clips):
        noetig = plan.source_ms(i)
        vorhanden = ffmpeg.duration_ms(str(clip))
        if vorhanden < noetig:
            zu_kurz.append(
                f"  Clip {i} ({clip}): {vorhanden} ms vorhanden, {noetig} ms noetig "
                f"({plan.source[i]} Bilder bei {plan.fps} fps, fehlen {noetig - vorhanden} ms)"
            )
    if zu_kurz:
        raise AssemblyError(
            "Mindestens ein Clip ist kuerzer als die Einstellung, die er fuellen soll:\n"
            + "\n".join(zu_kurz)
        )


def assemble(
    template: CutTemplate,
    clips: list[str | Path],
    output: str | Path,
    settings: RenderSettings | None = None,
    *,
    verify: bool = True,
) -> Path:
    """Rendert das Template mit den Clips nach `output`."""
    settings = settings or RenderSettings()
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    plan = FramePlan.build(template, settings.fps)
    check_clips(plan, clips)

    LOG.info(
        "rendere Template",
        extra={
            "template_id": template.id,
            "shots": len(template.cuts),
            "duration_ms": template.duration_ms,
            "frames": plan.total,
            "fps": plan.fps,
            "output": str(output),
        },
    )
    ffmpeg.run(build_args(template, clips, output, settings))

    if verify:
        gemessen = ffmpeg.duration_ms(str(output))
        toleranz = -(-1000 // settings.fps)  # ein Bild
        if abs(gemessen - plan.total_ms) > toleranz:
            raise AssemblyError(
                f"Das Ergebnis ist {gemessen} ms lang, der Plan verlangt "
                f"{plan.total_ms} ms ({plan.total} Bilder bei {plan.fps} fps, "
                f"Toleranz {toleranz} ms). Datei: {output}"
            )
        LOG.info("Render fertig", extra={"output": str(output), "duration_ms": gemessen})

    return output
