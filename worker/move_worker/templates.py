"""CutTemplate: der Schnitt als Liste von Zeitstempeln.

Ein Schnitt-Template ist keine KI. Es ist eine Liste von Zeitpunkten, und
dieses Modul ist die einzige Stelle, an der ihre Bedeutung festgelegt wird.

Festlegung
----------
`cuts[i]` beschreibt **die i-te Einstellung**, nicht die Grenze davor:

    at_ms         Zeitpunkt auf der fertigen Zeitachse, an dem diese
                  Einstellung beginnt. cuts[0].at_ms ist immer 0.
    transition    wie in diese Einstellung hineingeschnitten wird
    shot_scale    Bildgroesse der Einstellung (wide, medium, close, ...)
    transition_ms Laenge des Uebergangs in diese Einstellung

Damit gehoeren `transition` und `shot_scale` zu einer Einstellung, nicht zu
einer Grenze -- eine Bildgroesse an einem Schnittpunkt waere sinnlos.

Die Laenge der Einstellung i ergibt sich aus der naechsten:
`at_ms[i+1] - at_ms[i]`, fuer die letzte aus `duration_ms - at_ms[-1]`. Die
Summe ist immer genau `duration_ms`.

`transition_ms` steht so nicht im Datenmodell in CLAUDE.md. Ohne Laenge laesst
sich eine Blende aber nicht anwenden, und die Laenge ist Teil dessen, was aus
einem Trailer gelernt wird -- nicht eine Einstellung des Renderers. Das Feld
ist optional und faellt bei fehlender Angabe auf 0 zurueck, aeltere Templates
bleiben also lesbar.

Zeitstempel sind ueberall ganze Millisekunden. Float-Sekunden werden
zurueckgewiesen, nicht gerundet.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

TRANSITION_CUT = "cut"
TRANSITION_CROSSFADE = "crossfade"
TRANSITIONS = frozenset({TRANSITION_CUT, TRANSITION_CROSSFADE})


class TemplateError(ValueError):
    """Ein CutTemplate ist in sich nicht schluessig."""


def _as_int_ms(wert: Any, feld: str) -> int:
    """Nimmt nur echte Ganzzahlen an.

    Ein `bool` ist in Python ein `int` -- hier ist er trotzdem ein Fehler.
    Ein `float` wird nicht stillschweigend gerundet: 1500.0 waere harmlos,
    1500.4 nicht, und die Unterscheidung gehoert nicht in den Renderer.
    """
    if isinstance(wert, bool) or not isinstance(wert, int):
        raise TemplateError(
            f"{feld} muss eine ganze Zahl in Millisekunden sein, nicht {type(wert).__name__} "
            f"({wert!r}). Float-Sekunden gibt es im Datenmodell nicht."
        )
    return wert


@dataclass(frozen=True)
class Cut:
    at_ms: int
    transition: str = TRANSITION_CUT
    shot_scale: str = "medium"
    transition_ms: int = 0

    @classmethod
    def from_json(cls, roh: dict[str, Any], index: int) -> "Cut":
        unbekannt = set(roh) - {"at_ms", "transition", "shot_scale", "transition_ms"}
        if unbekannt:
            raise TemplateError(
                f"cuts[{index}] hat unbekannte Felder: {sorted(unbekannt)}"
            )
        return cls(
            at_ms=_as_int_ms(roh.get("at_ms"), f"cuts[{index}].at_ms"),
            transition=roh.get("transition", TRANSITION_CUT),
            shot_scale=roh.get("shot_scale", "medium"),
            transition_ms=_as_int_ms(
                roh.get("transition_ms", 0), f"cuts[{index}].transition_ms"
            ),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "at_ms": self.at_ms,
            "transition": self.transition,
            "shot_scale": self.shot_scale,
            "transition_ms": self.transition_ms,
        }


@dataclass(frozen=True)
class BeatGrid:
    bpm: float
    offsets_ms: tuple[int, ...] = ()

    @classmethod
    def from_json(cls, roh: dict[str, Any]) -> "BeatGrid":
        offsets = roh.get("offsets_ms", [])
        return cls(
            bpm=float(roh.get("bpm", 0.0)),
            offsets_ms=tuple(
                _as_int_ms(o, f"beat_grid.offsets_ms[{i}]") for i, o in enumerate(offsets)
            ),
        )

    def to_json(self) -> dict[str, Any]:
        return {"bpm": self.bpm, "offsets_ms": list(self.offsets_ms)}


@dataclass(frozen=True)
class CutTemplate:
    id: str
    name: str
    duration_ms: int
    cuts: tuple[Cut, ...]
    source_label: str = ""
    beat_grid: BeatGrid | None = None
    created_at: str = ""
    shot_count: int = field(default=0)

    # -- Ableitungen ------------------------------------------------------

    @property
    def shot_durations_ms(self) -> tuple[int, ...]:
        """Sichtbare Laenge jeder Einstellung auf der Zeitachse."""
        grenzen = [c.at_ms for c in self.cuts] + [self.duration_ms]
        return tuple(grenzen[i + 1] - grenzen[i] for i in range(len(self.cuts)))

    # Die Laenge, die aus jedem Clip geschnitten werden muss, haengt an der
    # Bildrate des Renders und steht deshalb bewusst nicht hier, sondern in
    # assembler.FramePlan. Eine zweite Rechnung in Millisekunden waere eine
    # zweite Quelle der Wahrheit.

    # -- JSON -------------------------------------------------------------

    @classmethod
    def from_json(cls, roh: dict[str, Any]) -> "CutTemplate":
        if not isinstance(roh, dict):
            raise TemplateError("Ein CutTemplate muss ein JSON-Objekt sein")

        cuts_roh = roh.get("cuts")
        if not isinstance(cuts_roh, list):
            raise TemplateError("cuts fehlt oder ist keine Liste")

        beat_roh = roh.get("beat_grid")
        vorlage = cls(
            id=str(roh.get("id", "")),
            name=str(roh.get("name", "")),
            source_label=str(roh.get("source_label", "")),
            duration_ms=_as_int_ms(roh.get("duration_ms"), "duration_ms"),
            cuts=tuple(Cut.from_json(c, i) for i, c in enumerate(cuts_roh)),
            beat_grid=BeatGrid.from_json(beat_roh) if isinstance(beat_roh, dict) else None,
            created_at=str(roh.get("created_at", "")),
            shot_count=int(roh.get("shot_count", len(cuts_roh))),
        )
        vorlage.validate()
        return vorlage

    @classmethod
    def from_file(cls, pfad: str | Path) -> "CutTemplate":
        pfad = Path(pfad)
        try:
            roh = json.loads(pfad.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise TemplateError(f"{pfad}: kein gueltiges JSON -- {exc}") from exc
        try:
            return cls.from_json(roh)
        except TemplateError as exc:
            raise TemplateError(f"{pfad}: {exc}") from exc

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "source_label": self.source_label,
            "duration_ms": self.duration_ms,
            "cuts": [c.to_json() for c in self.cuts],
            "beat_grid": self.beat_grid.to_json() if self.beat_grid else None,
            "shot_count": self.shot_count,
            "created_at": self.created_at,
        }

    # -- Pruefung ---------------------------------------------------------

    def validate(self) -> None:
        if self.duration_ms <= 0:
            raise TemplateError(f"duration_ms muss groesser als 0 sein, ist {self.duration_ms}")

        if not self.cuts:
            raise TemplateError("cuts ist leer -- ein Template ohne Einstellung ergibt kein Video")

        if self.shot_count != len(self.cuts):
            raise TemplateError(
                f"shot_count ({self.shot_count}) passt nicht zur Anzahl der cuts ({len(self.cuts)})"
            )

        if self.cuts[0].at_ms != 0:
            raise TemplateError(
                f"cuts[0].at_ms muss 0 sein, ist {self.cuts[0].at_ms} -- "
                f"at_ms ist der Beginn der Einstellung, nicht der Schnittpunkt davor"
            )

        if self.cuts[0].transition != TRANSITION_CUT:
            raise TemplateError(
                f"cuts[0].transition muss '{TRANSITION_CUT}' sein "
                f"-- vor der ersten Einstellung liegt nichts, in das geblendet werden koennte"
            )

        for i, cut in enumerate(self.cuts):
            if cut.transition not in TRANSITIONS:
                raise TemplateError(
                    f"cuts[{i}].transition '{cut.transition}' ist unbekannt, "
                    f"erlaubt sind {sorted(TRANSITIONS)}"
                )
            if cut.at_ms < 0:
                raise TemplateError(f"cuts[{i}].at_ms ist negativ ({cut.at_ms})")
            if cut.at_ms >= self.duration_ms:
                raise TemplateError(
                    f"cuts[{i}].at_ms ({cut.at_ms}) liegt nicht vor duration_ms ({self.duration_ms})"
                )
            if i > 0 and cut.at_ms <= self.cuts[i - 1].at_ms:
                raise TemplateError(
                    f"cuts[{i}].at_ms ({cut.at_ms}) ist nicht groesser als "
                    f"cuts[{i - 1}].at_ms ({self.cuts[i - 1].at_ms}) -- "
                    f"at_ms muss streng steigen"
                )

            if cut.transition == TRANSITION_CUT and cut.transition_ms != 0:
                raise TemplateError(
                    f"cuts[{i}]: ein harter Schnitt hat keine Laenge, "
                    f"transition_ms ist aber {cut.transition_ms}"
                )
            if cut.transition == TRANSITION_CROSSFADE and cut.transition_ms <= 0:
                raise TemplateError(
                    f"cuts[{i}]: crossfade braucht ein transition_ms groesser als 0"
                )

        # Eine Blende kann nicht laenger sein als eine der beiden Einstellungen,
        # die sie verbindet -- sonst waere eine davon nie allein zu sehen.
        sichtbar = self.shot_durations_ms
        for i, cut in enumerate(self.cuts):
            if cut.transition != TRANSITION_CROSSFADE:
                continue
            grenze = min(sichtbar[i - 1], sichtbar[i])
            if cut.transition_ms > grenze:
                raise TemplateError(
                    f"cuts[{i}]: Blende von {cut.transition_ms}ms ist laenger als die "
                    f"kuerzere der beiden Einstellungen ({grenze}ms)"
                )

        for i, laenge in enumerate(sichtbar):
            if laenge <= 0:
                raise TemplateError(f"Einstellung {i} hat die Laenge {laenge}ms")
