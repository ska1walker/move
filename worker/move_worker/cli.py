"""Kommandozeile fuer den Assembler.

Solange es die Job-Tabelle noch nicht gibt, ist das der Weg, die Pipeline zu
fahren und zu pruefen:

    python -m move_worker demo --template examples/beat-8s.json --out-dir /tmp/move

`demo` erzeugt die Platzhalter und rendert sie in einem Lauf -- die Schritte 5
und 6 aus dem Scope, ohne einen einzigen KI-Aufruf.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .assembler import AssemblyError, FramePlan, RenderSettings, assemble
from .ffmpeg import FfmpegError, FfmpegMissingError
from .placeholders import PlaceholderError, create_for_template
from .templates import CutTemplate, TemplateError


def _settings(args: argparse.Namespace) -> RenderSettings:
    return RenderSettings(width=args.width, height=args.height, fps=args.fps)


def _add_format_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--width", type=int, default=1920)
    p.add_argument("--height", type=int, default=1080)
    p.add_argument("--fps", type=int, default=25)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="move_worker")
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="ffmpeg-Aufrufe mitloggen"
    )
    sub = parser.add_subparsers(dest="befehl", required=True)

    p_ph = sub.add_parser("placeholders", help="Platzhalter-Clips zu einem Template erzeugen")
    p_ph.add_argument("--template", required=True)
    p_ph.add_argument("--out-dir", required=True)
    _add_format_args(p_ph)

    p_r = sub.add_parser("render", help="Template mit vorhandenen Clips rendern")
    p_r.add_argument("--template", required=True)
    p_r.add_argument("--out", required=True)
    p_r.add_argument(
        "--clip",
        action="append",
        default=[],
        help="ein Clip je Einstellung, in der Reihenfolge des Templates",
    )
    _add_format_args(p_r)

    p_d = sub.add_parser("demo", help="Platzhalter erzeugen und direkt rendern")
    p_d.add_argument("--template", required=True)
    p_d.add_argument("--out-dir", required=True)
    _add_format_args(p_d)

    p_s = sub.add_parser("show", help="Rechnung eines Templates anzeigen, ohne zu rendern")
    p_s.add_argument("--template", required=True)
    p_s.add_argument("--fps", type=int, default=25)

    return parser


def _show(template: CutTemplate, fps: int) -> None:
    sichtbar_ms = template.shot_durations_ms
    plan = FramePlan.build(template, fps)

    print(f"{template.name}  ({template.id})")
    print(f"  Quelle        {template.source_label or '-'}")
    print(f"  Laufzeit      {template.duration_ms} ms")
    print(f"  Einstellungen {template.shot_count}")
    if template.beat_grid:
        print(
            f"  Beat-Grid     {template.beat_grid.bpm} bpm, "
            f"{len(template.beat_grid.offsets_ms)} Beats"
        )
    print(f"  Plan bei      {fps} fps -> {plan.total} Bilder")
    print()
    print("   #   at_ms  sicht_ms | Bild  sicht  quelle  blende | Uebergang        Bildgroesse")
    for i, cut in enumerate(template.cuts):
        uebergang = cut.transition
        if cut.transition_ms:
            uebergang = f"{cut.transition} {cut.transition_ms}ms"
        print(
            f"  {i:2d}  {cut.at_ms:6d}  {sichtbar_ms[i]:8d} | "
            f"{plan.start[i]:4d}  {plan.visible[i]:5d}  {plan.source[i]:6d}  {plan.fade[i]:6d} | "
            f"{uebergang:16s} {cut.shot_scale}"
        )
    print()
    print(
        f"  Summe sichtbar: {sum(sichtbar_ms)} ms / {sum(plan.visible)} Bilder "
        f"(muss {template.duration_ms} ms / {plan.total} Bilder sein)"
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-7s %(name)s  %(message)s",
    )

    try:
        template = CutTemplate.from_file(args.template)

        if args.befehl == "show":
            _show(template, args.fps)
            return 0

        if args.befehl == "placeholders":
            pfade = create_for_template(template, args.out_dir, _settings(args))
            for p in pfade:
                print(p)
            return 0

        if args.befehl == "render":
            ziel = assemble(template, args.clip, args.out, _settings(args))
            print(ziel)
            return 0

        if args.befehl == "demo":
            out_dir = Path(args.out_dir)
            clips = create_for_template(template, out_dir / "clips", _settings(args))
            ziel = assemble(
                template, list(clips), out_dir / f"{template.id or 'render'}.mp4",
                _settings(args),
            )
            print(ziel)
            return 0

    except (TemplateError, AssemblyError, PlaceholderError) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 2
    except (FfmpegError, FfmpegMissingError) as exc:
        print(f"ffmpeg: {exc}", file=sys.stderr)
        return 3

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
