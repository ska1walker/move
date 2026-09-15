"""Kommandozeile des Workers.

Im Container laeuft `work` -- die Polling-Schleife auf der Job-Tabelle. Alles
andere ist zum Pruefen und Nachsehen von Hand:

    python3 -m move_worker show     --template examples/beat-8s.json
    python3 -m move_worker demo     --template examples/beat-8s.json --out-dir /tmp/move
    python3 -m move_worker enqueue  --template examples/beat-8s.json
    python3 -m move_worker work     --max-jobs 1
    python3 -m move_worker jobs
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .assembler import AssemblyError, FramePlan, RenderSettings, assemble
from .ffmpeg import FfmpegError, FfmpegMissingError
from .placeholders import PlaceholderError, create_for_template
from .repository import Clip, Repository, RepositoryError
from .runner import build_worker, db_path
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

    p_w = sub.add_parser("work", help="Job-Tabelle pollen und rendern (Container-Befehl)")
    p_w.add_argument(
        "--max-jobs",
        type=int,
        default=None,
        help="nach so vielen Jobs beenden; ohne Angabe endlos",
    )
    _add_format_args(p_w)

    p_e = sub.add_parser("enqueue", help="Template ablegen und einen Job einreihen")
    p_e.add_argument("--template", required=True)
    p_e.add_argument(
        "--clip",
        action="append",
        default=[],
        help="Datei fuer eine Einstellung, in der Reihenfolge des Templates. "
        "Ohne Angabe werden Platzhalter erzeugt.",
    )

    sub.add_parser("jobs", help="Stand der Job-Tabelle anzeigen")

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


def _enqueue(args: argparse.Namespace) -> int:
    template = CutTemplate.from_file(args.template)
    if args.clip and len(args.clip) != len(template.cuts):
        print(
            f"Fehler: Das Template hat {len(template.cuts)} Einstellungen, "
            f"uebergeben wurden {len(args.clip)} Clips",
            file=sys.stderr,
        )
        return 2

    if args.clip:
        clips = [Clip(index=i, source="upload", uri=p) for i, p in enumerate(args.clip)]
    else:
        clips = [Clip(index=i, source="placeholder") for i in range(len(template.cuts))]

    with Repository(db_path()) as repo:
        repo.migrate()
        template_id = repo.save_template(template)
        job_id = repo.enqueue(template_id, clips)
    print(job_id)
    return 0


def _jobs() -> int:
    with Repository(db_path()) as repo:
        repo.migrate()
        stand = repo.count_by_status()
        print(f"Datenbank: {repo.db_path}")
        if not stand:
            print("  keine Jobs")
            return 0
        for status in ("queued", "running", "done", "failed"):
            if status in stand:
                print(f"  {status:8s} {stand[status]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-7s %(name)s  %(message)s",
    )

    try:
        if args.befehl == "work":
            worker = build_worker(settings=_settings(args))
            worker.install_signal_handlers()
            worker.run_forever(max_jobs=args.max_jobs)
            return 0

        if args.befehl == "enqueue":
            return _enqueue(args)

        if args.befehl == "jobs":
            return _jobs()

        template = CutTemplate.from_file(args.template)

        if args.befehl == "show":
            _show(template, args.fps)
            return 0

        if args.befehl == "placeholders":
            for p in create_for_template(template, args.out_dir, _settings(args)):
                print(p)
            return 0

        if args.befehl == "render":
            print(assemble(template, args.clip, args.out, _settings(args)))
            return 0

        if args.befehl == "demo":
            out_dir = Path(args.out_dir)
            clips = create_for_template(template, out_dir / "clips", _settings(args))
            print(
                assemble(
                    template,
                    list(clips),
                    out_dir / f"{template.id or 'render'}.mp4",
                    _settings(args),
                )
            )
            return 0

    except (TemplateError, AssemblyError, PlaceholderError, RepositoryError) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 2
    except (FfmpegError, FfmpegMissingError) as exc:
        print(f"ffmpeg: {exc}", file=sys.stderr)
        return 3

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
