#!/usr/bin/env python3
"""Misst, wo die Schnitte im Ergebnis WIRKLICH liegen.

Nicht Teil des Images. Gedacht fuer den Fall, dass ein Ergebnis die richtige
Laenge hat und trotzdem falsch ist -- dann sagt die Laufzeit nichts und man
braucht die Grenzen Bild fuer Bild.

Jede Einstellung bekommt eine eigene Farbe. Danach wird jedes Bild des
Ergebnisses flaechengewichtet auf einen Pixel gemittelt und der naechsten
Farbe zugeordnet; was zu keiner passt, ist eine Blende. Aus der Folge ergeben
sich die tatsaechlichen Grenzen, die gegen den FramePlan gestellt werden.

    python3 tools/schnittgrenzen.py --template examples/beat-8s.json
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from move_worker import ffmpeg  # noqa: E402
from move_worker.assembler import FramePlan, RenderSettings, assemble  # noqa: E402
from move_worker.templates import CutTemplate  # noqa: E402

# Die Palette ist so gewaehlt, dass keine Mischung zweier Farben eine dritte
# Palettenfarbe trifft -- sonst zaehlt ein Bild mitten in der Blende als
# reine Einstellung und die gemessene Grenze wandert. Deshalb kein Grau und
# kein Orange unter den ersten acht: Weiss nach Grau ist eine Graustufen-
# blende, und Rot nach Gelb mischt zu Orange.
FARBEN = [
    ("red", (255, 0, 0)),
    ("lime", (0, 255, 0)),
    ("blue", (0, 0, 255)),
    ("yellow", (255, 255, 0)),
    ("magenta", (255, 0, 255)),
    ("cyan", (0, 255, 255)),
    ("white", (255, 255, 255)),
    ("black", (0, 0, 0)),
]

BREITE, HOEHE = 160, 90
TOLERANZ = 10


def einfarbig(ziel: Path, farbe: str, frames: int, fps: int) -> None:
    ffmpeg.run(
        [
            "-hide_banner", "-nostdin", "-y",
            "-f", "lavfi",
            "-i", f"color=c={farbe}:s={BREITE}x{HOEHE}:r={fps}",
            "-frames:v", str(frames),
            "-an",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12",
            "-pix_fmt", "yuv420p",
            str(ziel),
        ]
    )


def alle_farben(video: Path, anzahl: int, ordner: Path) -> list[tuple[int, int, int]]:
    """Mittlere Farbe jedes Bildes, in einem einzigen ffmpeg-Aufruf."""
    roh = ordner / "alle.raw"
    ffmpeg.run(
        [
            "-hide_banner", "-nostdin", "-y", "-v", "error",
            "-i", str(video),
            "-vf", "scale=w=1:h=1:flags=area",
            "-f", "rawvideo", "-pix_fmt", "rgb24",
            str(roh),
        ]
    )
    daten = roh.read_bytes()
    return [tuple(daten[i : i + 3]) for i in range(0, min(len(daten), anzahl * 3), 3)]


def zuordnen(farbe: tuple[int, int, int]) -> int | None:
    for i, (_, soll) in enumerate(FARBEN):
        if all(abs(a - b) <= TOLERANZ for a, b in zip(farbe, soll)):
            return i
    return None


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--template", required=True)
    p.add_argument("--fps", type=int, default=25)
    args = p.parse_args()

    template = CutTemplate.from_file(args.template)
    if len(template.cuts) > len(FARBEN):
        print(f"Nur {len(FARBEN)} Farben vorhanden", file=sys.stderr)
        return 2

    settings = RenderSettings(width=BREITE, height=HOEHE, fps=args.fps, preset="ultrafast")
    plan = FramePlan.build(template, args.fps)

    print(ffmpeg.run(["-hide_banner", "-version"]).stdout.splitlines()[0])
    print(f"Plan: {plan.total} Bilder bei {args.fps} fps\n")

    with tempfile.TemporaryDirectory() as d:
        ordner = Path(d)
        clips = []
        for i in range(len(template.cuts)):
            ziel = ordner / f"clip-{i:02d}.mp4"
            einfarbig(ziel, FARBEN[i][0], plan.source[i], args.fps)
            clips.append(ziel)

        ergebnis = ordner / "out.mp4"
        assemble(template, clips, ergebnis, settings, verify=False)
        farben = alle_farben(ergebnis, plan.total, ordner)

        print(f"Bilder im Ergebnis: {len(farben)} (Plan {plan.total})\n")

        zuordnung = [zuordnen(f) for f in farben]

        print("  #  Plan-Start  erstes reines Bild  Abweichung")
        fehler = 0
        for i in range(len(template.cuts)):
            # Erstes Bild, ab dem nur noch diese Einstellung zu sehen ist.
            erstes = next((n for n, z in enumerate(zuordnung) if z == i), None)
            soll = plan.start[i] + plan.fade[i]
            if erstes is None:
                print(f" {i:2d}  {soll:10d}  {'nie':>18}  --")
                fehler += 1
                continue
            abweichung = erstes - soll
            if abweichung:
                fehler += 1
            print(f" {i:2d}  {soll:10d}  {erstes:18d}  {abweichung:+d}")

        print("\nBildfolge (Zahl = Einstellung, '.' = Blende oder unklar):")
        zeile = "".join("." if z is None else str(z % 10) for z in zuordnung)
        for start in range(0, len(zeile), 100):
            print(f"  {start:4d} {zeile[start : start + 100]}")

        if fehler:
            print(f"\n{fehler} Einstellung(en) liegen nicht auf dem Plan")
            return 1
        print("\nalle Einstellungen liegen auf dem Plan")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
