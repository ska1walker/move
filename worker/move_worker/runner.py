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
from .extraktion import template_aus_video
from .generierung import Anfrage, Generator, default_model
from .placeholders import create as create_placeholder
from .repository import Clip, ExtractJob, Figur, RenderJob, Repository
from .templates import CutTemplate

LOG = logging.getLogger(__name__)

DEFAULT_DATA_DIR = "/app/data"
DEFAULT_POLL_INTERVAL_MS = 2000

# Hoechstzahl bezahlter fal-Aufrufe je Job. Grosszuegig ueber dem
# Beispiel-Template mit acht Einstellungen, aber nicht offen.
DEFAULT_MAX_FAL_CLIPS = 12


def data_dir() -> Path:
    """Das Datenverzeichnis IM Container.

    Nicht zu verwechseln mit `.Values.userspace.appData` -- das ist der Pfad
    auf dem Host. Im Code gilt immer der Container-Pfad.
    """
    return Path(os.environ.get("MOVE_DATA_DIR", DEFAULT_DATA_DIR))


def db_path() -> Path:
    return data_dir() / "db" / "move.sqlite3"


def max_fal_clips() -> int:
    """Obergrenze fuer bezahlte Generierungen je Job.

    0 oder negativ schaltet die Generierung ganz ab -- brauchbar fuer eine
    Installation, die nur Platzhalter und Uploads zulassen soll.
    """
    roh = os.environ.get("MOVE_FAL_MAX_CLIPS", "").strip()
    if roh == "":
        return DEFAULT_MAX_FAL_CLIPS
    try:
        return int(roh)
    except ValueError as exc:
        # Nicht still auf die Vorgabe zurueckfallen: ein Tippfehler in der
        # Umgebung darf keine offene Grenze bedeuten.
        raise ValueError(
            f'MOVE_FAL_MAX_CLIPS="{roh}" ist keine ganze Zahl'
        ) from exc


# Wie weit ein Schnittzeitpunkt auf den naechsten Beat gezogen wird. 0 heisst
# gar nicht. 120 ms ist etwa ein Drittel eines Viertels bei 160 BPM -- weit
# genug, damit eine Erkennung, die knapp neben dem Beat liegt, einrastet, und
# eng genug, dass ein Schnitt, der bewusst zwischen den Beats sitzt, dort
# bleibt.
DEFAULT_SNAP_TOLERANZ_MS = 120


def snap_toleranz_ms() -> int:
    roh = os.environ.get("MOVE_SNAP_TOLERANZ_MS", "").strip()
    if roh == "":
        return DEFAULT_SNAP_TOLERANZ_MS
    try:
        wert = int(roh)
    except ValueError as exc:
        # Wie bei MOVE_FAL_MAX_CLIPS: ein Tippfehler faellt auf, statt still
        # eine andere Vorgabe zu bedeuten.
        raise ValueError(f'MOVE_SNAP_TOLERANZ_MS="{roh}" ist keine ganze Zahl') from exc
    if wert < 0:
        raise ValueError(f"MOVE_SNAP_TOLERANZ_MS darf nicht negativ sein, ist {wert}")
    return wert


@dataclass
class Worker:
    repo: Repository
    settings: RenderSettings = field(default_factory=RenderSettings)
    poll_interval_ms: int = DEFAULT_POLL_INTERVAL_MS
    # Gehoert NICHT in RenderSettings: die Klasse beschreibt laut ihrer
    # eigenen Beschreibung das Ausgabeformat, nicht den Schnitt. Die Toleranz
    # entscheidet ueber Schnittzeitpunkte und damit ueber das Template.
    snap_toleranz_ms: int = DEFAULT_SNAP_TOLERANZ_MS
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
        self.repo.reset_stale_extract()

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
        """Holt hoechstens einen Job. True, wenn einer bearbeitet wurde.

        EXTRAKTION GEHT VOR, und das ist eine Entscheidung. Sie dauert
        Sekunden, rechnet auf der CPU und kostet nichts; ein Render mit
        fal-Clips dauert Minuten und kostet Geld. Liefe es umgekehrt, wartete
        jemand, der gerade ein Video hochgeladen hat, hinter einer
        Generierung -- und ohne Template kann er gar keinen Job anlegen. Bei
        Concurrency 1 ist die Reihenfolge deshalb die ganze Priorisierung,
        die es hier gibt.
        """
        if self.extract_once():
            return True

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

    # -- Extraktion -------------------------------------------------------

    def extract_once(self) -> bool:
        """Holt hoechstens eine Extraktion. True, wenn eine bearbeitet wurde.

        Das ist der Weg, der in der Oberflaeche gefehlt hat: ohne ihn musste
        jemand `move_worker extract` per `kubectl exec` im Pod tippen, um
        ueberhaupt ein Template zu bekommen -- und ohne Template zeigt die
        Seite nur einen Hinweistext.
        """
        auftrag = self.repo.claim_next_extract()
        if auftrag is None:
            return False

        LOG.info(
            "Extraktion uebernommen",
            extra={"job_id": auftrag.id, "upload_id": auftrag.upload_id},
        )
        try:
            template_id = self.extrahiere(auftrag)
        except Exception as exc:  # noqa: BLE001 - jeder Fehler gehoert in die Zeile
            LOG.exception("Extraktion gescheitert", extra={"job_id": auftrag.id})
            self.repo.mark_extract_failed(
                auftrag.id, f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
            )
            return True

        self.repo.mark_extract_done(auftrag.id, template_id)
        return True

    def extrahiere(self, auftrag: ExtractJob) -> str:
        """Gewinnt ein CutTemplate aus dem hochgeladenen Video."""
        basis = data_dir()
        upload = self.repo.get_upload(auftrag.upload_id)
        if upload.art != "video":
            raise RuntimeError(
                f"upload {upload.id} ist art={upload.art!r}; extrahieren geht nur aus einem Video"
            )

        video = basis / upload.uri
        if not video.is_file():
            raise FileNotFoundError(
                f"Die hochgeladene Datei fehlt: {upload.uri} (erwartet unter {basis})"
            )

        # snap_toleranz_ms > 0 zieht Schnitte auf den naechsten Beat, wenn
        # einer in Reichweite liegt. Genau dafuer gibt es das Beat-Grid; ohne
        # Toleranz waere es Zierde.
        template = template_aus_video(
            video,
            name=auftrag.name or upload.name,
            snap_toleranz_ms=self.snap_toleranz_ms,
            # Aus der Oberflaeche: Schnitte sind das Produkt, das Beat-Grid
            # ist die Verbesserung. Siehe extraktion.template_aus_video.
            beat_pflicht=False,
        )
        return self.repo.save_template(template)

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

        # Obergrenze fuer bezahlte Aufrufe, BEVOR der erste laeuft.
        #
        # fal ist eine Entwicklerplattform und rechnet pro Aufruf ab. Ein
        # Template mit vielen Einstellungen ist damit ein Job mit vielen
        # Rechnungspositionen -- und in einer Kundenversion loest das ein
        # Fremder aus, waehrend der Schluessel dem Betreiber gehoert.
        # Lieber hier scheitern als hinterher zahlen.
        zu_erzeugen = sum(1 for c in nach_index.values() if c.source == "fal")
        grenze = max_fal_clips()
        if zu_erzeugen > grenze:
            raise ValueError(
                f"Der Job wuerde {zu_erzeugen} Clips bei fal erzeugen, erlaubt sind "
                f"{grenze}. Jeder Aufruf wird abgerechnet. Grenze ueber "
                f"MOVE_FAL_MAX_CLIPS anheben, wenn das so gewollt ist."
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

        # KONSISTENTE PERSON. Nennt der Clip eine Figur, wandern deren
        # Beschreibung, Referenzbild und Seed in DIESE Einstellung ein -- und
        # in jede andere, die dieselbe Figur nennt. Das ist der ganze
        # Mechanismus: nicht ein Modell, das sich erinnert, sondern dieselben
        # Eingaben, jedes Mal.
        #
        # Die Beschreibung steht VOR dem Prompt der Einstellung. Modelle
        # gewichten den Anfang staerker, und wer die Figur schreibt, meint sie
        # als Subjekt und nicht als Zusatz.
        referenz_bild: Path | None = None
        seed: int | None = None
        if clip.figur_id:
            figur = self.repo.get_figur(clip.figur_id)
            if figur.beschreibung.strip():
                prompt = f"{figur.beschreibung.strip()}. {prompt}"
            seed = figur.wirksamer_seed()
            if figur.referenz_uri:
                referenz_bild = basis / figur.referenz_uri
                if not referenz_bild.is_file():
                    # Nicht still ohne Bild weitermachen: der Aufruf wuerde
                    # bezahlt und die Figur saehe anders aus als gewollt.
                    raise FileNotFoundError(
                        f"Figur {figur.name!r} verweist auf das Referenzbild "
                        f"{figur.referenz_uri}, dort liegt keine Datei. Ohne das Bild "
                        f"waere die Einstellung bezahlt und inkonsistent."
                    )

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
            referenz_bild=referenz_bild,
            seed=seed,
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
        snap_toleranz_ms=snap_toleranz_ms(),
    )
