"""ffmpeg- und ffprobe-Aufrufe.

Zwei Regeln, die hier durchgesetzt werden:

- Aufrufe sind **Argument-Arrays**, nie Shell-Strings. Es gibt in diesem Modul
  kein `shell=True`. Ein Dateiname mit Leerzeichen oder Anfuehrungszeichen darf
  nichts kaputt machen koennen.
- Fehler werden nicht still geschluckt. Ein fehlgeschlagener Aufruf traegt
  Exit-Code, die vollstaendige Kommandozeile und das Ende von stderr mit sich.
  Zwei Fehler liegen oft uebereinander; wer nur "ffmpeg failed" loggt, sieht
  den zweiten nie.
"""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass

LOG = logging.getLogger(__name__)

# Standard-Zeitlimit. Ein Render von wenigen Sekunden Material braucht keine
# Minuten; laeuft es laenger, stimmt etwas nicht und der Job soll scheitern,
# statt den Worker zu blockieren.
DEFAULT_TIMEOUT_S = 600

STDERR_TAIL_LINES = 40


class FfmpegError(RuntimeError):
    """Ein ffmpeg- oder ffprobe-Aufruf ist gescheitert."""

    def __init__(self, binary: str, argv: list[str], returncode: int, stderr: str):
        self.binary = binary
        self.argv = list(argv)
        self.returncode = returncode
        self.stderr = stderr

        tail = "\n".join(stderr.strip().splitlines()[-STDERR_TAIL_LINES:])
        super().__init__(
            f"{binary} endete mit Code {returncode}\n"
            f"Aufruf: {shlex.join([binary, *argv])}\n"
            f"stderr (letzte {STDERR_TAIL_LINES} Zeilen):\n{tail}"
        )


class FfmpegMissingError(RuntimeError):
    """Das Binary fehlt oder kann die benoetigten Filter nicht."""


@dataclass(frozen=True)
class Result:
    argv: list[str]
    stdout: str
    stderr: str


def ffmpeg_binary() -> str:
    return os.environ.get("MOVE_FFMPEG", "ffmpeg")


def ffprobe_binary() -> str:
    return os.environ.get("MOVE_FFPROBE", "ffprobe")


def _run(binary: str, args: list[str], timeout_s: int) -> Result:
    if shutil.which(binary) is None and not os.path.isfile(binary):
        raise FfmpegMissingError(
            f"{binary} nicht gefunden. Pfad ueber MOVE_FFMPEG bzw. MOVE_FFPROBE setzen."
        )

    argv = [binary, *args]
    LOG.debug("starte %s", shlex.join(argv))
    try:
        proc = subprocess.run(  # noqa: S603 - Argument-Array, kein shell=True
            argv,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise FfmpegError(
            binary, args, -1, f"Zeitlimit von {timeout_s}s ueberschritten\n{exc.stderr or ''}"
        ) from exc

    if proc.returncode != 0:
        LOG.error(
            "ffmpeg-Aufruf gescheitert",
            extra={"binary": binary, "returncode": proc.returncode, "argv": argv},
        )
        raise FfmpegError(binary, args, proc.returncode, proc.stderr)

    return Result(argv=argv, stdout=proc.stdout, stderr=proc.stderr)


def run(args: list[str], *, timeout_s: int = DEFAULT_TIMEOUT_S) -> Result:
    """Fuehrt ffmpeg mit dem uebergebenen Argument-Array aus."""
    return _run(ffmpeg_binary(), args, timeout_s)


def probe(args: list[str], *, timeout_s: int = 60) -> Result:
    """Fuehrt ffprobe mit dem uebergebenen Argument-Array aus."""
    return _run(ffprobe_binary(), args, timeout_s)


def has_filter(name: str) -> bool:
    """Prueft, ob der ffmpeg-Build einen Filter mitbringt.

    drawtext braucht libfreetype und fehlt in schlanken Builds. Lieber hier
    merken als mitten im Render.
    """
    try:
        result = run(["-hide_banner", "-filters"], timeout_s=30)
    except (FfmpegError, FfmpegMissingError):
        return False
    return any(
        line.split()[1] == name
        for line in result.stdout.splitlines()
        if len(line.split()) > 1
    )


def require_filter(name: str) -> None:
    if not has_filter(name):
        raise FfmpegMissingError(
            f"Der ffmpeg-Build kennt den Filter '{name}' nicht. "
            f"Das Worker-Image muss ein ffmpeg mit diesem Filter mitbringen "
            f"(drawtext benoetigt libfreetype)."
        )


def duration_ms(path: str) -> int:
    """Laufzeit einer Datei in ganzen Millisekunden.

    Rundet auf die naechste Millisekunde. Millisekunden als Integer sind im
    ganzen Projekt die Einheit; Float-Sekunden verlassen dieses Modul nicht.
    """
    result = probe(
        [
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path,
        ]
    )
    roh = result.stdout.strip()
    try:
        return round(float(roh) * 1000)
    except ValueError as exc:
        raise FfmpegError(
            ffprobe_binary(), [path], 0, f"ffprobe lieferte keine Laufzeit: {roh!r}"
        ) from exc
