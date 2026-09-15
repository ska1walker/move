"""Die Schleife, die der Worker-Container faehrt.

Der Worker spricht nie HTTP. Der Envoy-Sidecar faengt jeden eingehenden
TCP-Verkehr ab, auch clusterinternen -- ein Aufruf vom eigenen Pod auf einen
Entrance-Service endet in 401. Deshalb laeuft die Verstaendigung zwischen Web
und Worker ausschliesslich ueber die Job-Tabelle. Das Polling ist die Folge
dieser Einschraenkung, nicht Bequemlichkeit.
"""

from __future__ import annotations

import logging
import math
import os
import signal
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from .assembler import FramePlan, RenderSettings, assemble
from .generierung import Anfrage, Generator, default_model
from .placeholders import create as create_placeholder
from .repository import Clip, RenderJob, Repository
from .templates import CutTemplate

LOG = logging.getLogger(__name__)

DEFAULT_DATA_DIR = "/app/data"
DEFAULT_POLL_INTERVAL_MS = 2000


def data_dir() -> Path:
    """Das Datenverzeichnis IM Container.

    Nicht zu verwechseln mit `.Values.userspace.appData` -- das ist der Pfad
    auf dem Host. Im Code gilt immer der Container-Pfad.
    """
    return Path(os.environ.get("MOVE_DATA_DIR", DEFAULT_DATA_DIR))


def db_path() -> Path:
    return data_dir() / "db" / "move.sqlite3"


@dataclass
class Worker:
    repo: Repository
    settings: RenderSettings = field(default_factory=RenderSettings)
    poll_interval_ms: int = DEFAULT_POLL_INTERVAL_MS
    _stop: bool = field(default=False, repr=False)

    # -- Lebenszyklus -----------------------------------------------------

    def request_stop(self, *_) -> None:
        LOG.info("Abbruch angefordert, beende nach dem laufenden Job")
        self._stop = True

    def install_signal_handlers(self) -> None:
        """Bei Recreate beendet Kubernetes den Pod mit SIGTERM.

        Ohne Handler stuerbe der Prozess mitten im Render und der Job bliebe
        auf running stehen.
        """
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, self.request_stop)

    def run_forever(self, max_jobs: int | None = None) -> int:
        """Pollt, bis ein Signal kommt oder `max_jobs` erledigt sind."""
        self.repo.migrate()
        self.repo.reset_stale_running()

        erledigt = 0
        LOG.info(
            "Worker bereit",
            extra={"db": str(self.repo.db_path), "poll_ms": self.poll_interval_ms},
        )
        while not self._stop:
            if self.run_once():
                erledigt += 1
                if max_jobs is not None and erledigt >= max_jobs:
                    break
                # Direkt weiter: solange Arbeit da ist, nicht schlafen.
                continue
            if max_jobs is not None and erledigt >= max_jobs:
                break
            time.sleep(self.poll_interval_ms / 1000)
        LOG.info("Worker beendet", extra={"jobs": erledigt})
        return erledigt

    def run_once(self) -> bool:
        """Holt hoechstens einen Job. True, wenn einer bearbeitet wurde."""
        job = self.repo.claim_next()
        if job is None:
            return False

        LOG.info("Job uebernommen", extra={"job_id": job.id, "template_id": job.template_id})
        try:
            ziel = self.process(job)
        except Exception as exc:  # noqa: BLE001 - jeder Fehler gehoert in die Zeile
            # Mit Metadaten und Traceback. Ein still gescheiterter Job
            # verbirgt oft einen Plattformfehler, und zwei Fehler liegen
            # haeufig uebereinander.
            LOG.exception("Render gescheitert", extra={"job_id": job.id})
            self.repo.mark_failed(job.id, f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")
            return True

        self.repo.mark_done(job.id, ziel)
        return True

    # -- Arbeit -----------------------------------------------------------

    def process(self, job: RenderJob) -> str:
        """Rendert einen Job und gibt den Ort des Ergebnisses zurueck.

        Der Rueckgabewert ist **relativ zum Datenverzeichnis**. Absolut waere
        er im Container richtig und auf dem Host falsch.
        """
        basis = data_dir()
        template = self.repo.get_template(job.template_id)
        clips = self.resolve_clips(job, template, basis)

        ziel = basis / "renders" / f"{job.id}.mp4"
        assemble(template, clips, ziel, self.settings)
        return str(ziel.relative_to(basis))

    def resolve_clips(
        self, job: RenderJob, template: CutTemplate, basis: Path
    ) -> list[Path]:
        """Bringt die Clips in die Reihenfolge des Templates.

        Platzhalter werden erzeugt, alles andere muss als Datei dasein.
        """
        anzahl = len(template.cuts)
        nach_index = {c.index: c for c in job.clips}

        fehlend = sorted(set(range(anzahl)) - set(nach_index))
        if fehlend:
            raise ValueError(
                f"Das Template hat {anzahl} Einstellungen, "
                f"fuer diese fehlt ein Clip: {fehlend}"
            )
        ueberzaehlig = sorted(set(nach_index) - set(range(anzahl)))
        if ueberzaehlig:
            raise ValueError(
                f"Clips mit einem Index ausserhalb des Templates ({anzahl} "
                f"Einstellungen): {ueberzaehlig}"
            )

        plan = FramePlan.build(template, self.settings.fps)
        arbeitsordner = basis / "work" / job.id
        pfade: list[Path] = []

        for i in range(anzahl):
            clip = nach_index[i]
            if clip.source == "placeholder":
                ziel = arbeitsordner / f"clip-{i:02d}.mp4"
                create_placeholder(i, plan.source[i], ziel, self.settings)
                pfade.append(ziel)
                continue

            if clip.source == "fal":
                pfade.append(self.erzeuge_clip(clip, template, plan, i, basis))
                continue

            quelle = Path(clip.uri)
            if not quelle.is_absolute():
                quelle = basis / clip.uri
            if not quelle.is_file():
                raise FileNotFoundError(
                    f"Clip {i} ({clip.source}): {quelle} gibt es nicht"
                )
            pfade.append(quelle)

        return pfade

    def erzeuge_clip(
        self,
        clip: Clip,
        template: CutTemplate,
        plan: FramePlan,
        index: int,
        basis: Path,
    ) -> Path:
        """Laesst eine Einstellung bei fal.ai erzeugen.

        Ausserhalb des v0-Scope, auf ausdrueckliche Ansage gebaut. Der Schnitt
        bleibt Arithmetik; generiert wird nur der Inhalt der Einstellung.
        """
        if not clip.prompt.strip():
            raise ValueError(
                f"clips[{index}] hat source 'fal', aber keinen prompt. "
                f"Ohne Beschreibung kann nichts erzeugt werden."
            )

        # Die Bildgroesse aus dem Template gehoert in die Beschreibung -- sie
        # ist Teil dessen, was ein Template ueber den Schnitt aussagt. Die
        # Zusammensetzung steht im Log, damit sichtbar ist, was gefragt wurde.
        bildgroesse = template.cuts[index].shot_scale.strip()
        prompt = f"{clip.prompt.strip()}, {bildgroesse} shot" if bildgroesse else clip.prompt.strip()

        # Aufgerundet auf ganze Sekunden: Modelle nehmen meist ganzzahlige
        # Laengen, und zu kurz waere teuer -- der Assembler lehnt den Clip
        # dann ab und die Generierung waere bezahlt und unbrauchbar.
        sekunden = math.ceil(plan.source[index] / plan.fps)

        anfrage = Anfrage(
            prompt=prompt,
            sekunden=float(sekunden),
            breite=self.settings.width,
            height=self.settings.height,
            model=clip.model.strip() or default_model(),
        )
        generator = Generator(cache_dir=basis / "generated")
        return generator.erzeuge(anfrage)


def build_worker(
    settings: RenderSettings | None = None,
    poll_interval_ms: int | None = None,
) -> Worker:
    if poll_interval_ms is None:
        poll_interval_ms = int(
            os.environ.get("MOVE_POLL_INTERVAL_MS", DEFAULT_POLL_INTERVAL_MS)
        )
    return Worker(
        repo=Repository(db_path()),
        settings=settings or RenderSettings(),
        poll_interval_ms=poll_interval_ms,
    )
