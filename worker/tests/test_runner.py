"""Die Schleife des Containers, gegen eine echte Datenbank und echtes ffmpeg.

Braucht ffmpeg und ffprobe im PATH und wird sonst uebersprungen.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from move_worker.assembler import RenderSettings
from tests.test_pipeline import einfarbig

from move_worker.repository import STATUS_DONE, STATUS_FAILED, Clip, Repository
from move_worker.runner import Worker
from move_worker.templates import CutTemplate

HAT_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))

# Klein und kurz: zwei Einstellungen, 1,2 s. Testvideos bleiben unter 60 s.
TEMPLATE = {
    "id": "t1",
    "name": "Kurz",
    "duration_ms": 1200,
    "shot_count": 2,
    "cuts": [
        {"at_ms": 0, "transition": "cut"},
        {"at_ms": 600, "transition": "crossfade", "transition_ms": 200},
    ],
}


@unittest.skipUnless(HAT_FFMPEG, "ffmpeg oder ffprobe fehlt im PATH")
class TestWorker(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.daten = Path(self.tmp.name)
        # runner.data_dir() liest MOVE_DATA_DIR. Der Test setzt es auf ein
        # Wegwerf-Verzeichnis statt auf /app/data.
        self.env = mock.patch.dict(os.environ, {"MOVE_DATA_DIR": str(self.daten)})
        self.env.start()

        self.repo = Repository(self.daten / "db" / "move.sqlite3")
        self.repo.migrate()
        self.template = CutTemplate.from_json(TEMPLATE)
        self.template_id = self.repo.save_template(self.template)
        self.worker = Worker(
            repo=self.repo,
            settings=RenderSettings(width=160, height=90, fps=25, preset="ultrafast"),
            poll_interval_ms=10,
        )

    def tearDown(self):
        self.repo.close()
        self.env.stop()
        self.tmp.cleanup()

    def test_leere_warteschlange_meldet_nichts_zu_tun(self):
        self.assertFalse(self.worker.run_once())

    def test_platzhalter_job_laeuft_durch(self):
        job_id = self.repo.enqueue(
            self.template_id, [Clip(index=0), Clip(index=1)]
        )
        self.assertTrue(self.worker.run_once())

        job = self.repo.get_job(job_id)
        self.assertEqual(job.status, STATUS_DONE, job.error)
        self.assertEqual(job.output_uri, f"renders/{job_id}.mp4")
        self.assertIsNotNone(job.finished_at)

    def test_ergebnis_liegt_relativ_zum_datenverzeichnis(self):
        """Absolut waere der Pfad im Container richtig und auf dem Host falsch."""
        job_id = self.repo.enqueue(self.template_id, [Clip(index=0), Clip(index=1)])
        self.worker.run_once()
        job = self.repo.get_job(job_id)
        self.assertFalse(job.output_uri.startswith("/"))
        self.assertTrue((self.daten / job.output_uri).is_file())

    def test_fehlender_clip_index_scheitert_mit_zahlen(self):
        job_id = self.repo.enqueue(self.template_id, [Clip(index=0)])
        self.assertTrue(self.worker.run_once())
        job = self.repo.get_job(job_id)
        self.assertEqual(job.status, STATUS_FAILED)
        self.assertIn("[1]", job.error)

    def test_clipdatei_fehlt(self):
        job_id = self.repo.enqueue(
            self.template_id,
            [Clip(index=0, source="upload", uri="uploads/gibtsnicht.mp4"), Clip(index=1)],
        )
        self.assertTrue(self.worker.run_once())
        job = self.repo.get_job(job_id)
        self.assertEqual(job.status, STATUS_FAILED)
        self.assertIn("gibtsnicht.mp4", job.error)

    def test_unbekanntes_template(self):
        self.repo.connect().execute(
            "INSERT INTO cut_template (id, name, duration_ms, cuts, shot_count, created_at) "
            "VALUES ('leer', 'x', 1000, '[]', 0, 1)"
        )
        job_id = self.repo.enqueue("leer", [Clip(index=0)])
        self.assertTrue(self.worker.run_once())
        self.assertEqual(self.repo.get_job(job_id).status, STATUS_FAILED)

    def test_ein_fehler_haelt_die_schleife_nicht_an(self):
        kaputt = self.repo.enqueue(self.template_id, [Clip(index=0)])
        gut = self.repo.enqueue(self.template_id, [Clip(index=0), Clip(index=1)])

        erledigt = self.worker.run_forever(max_jobs=2)

        self.assertEqual(erledigt, 2)
        self.assertEqual(self.repo.get_job(kaputt).status, STATUS_FAILED)
        self.assertEqual(self.repo.get_job(gut).status, STATUS_DONE)

    def test_abbruchsignal_beendet_die_schleife(self):
        self.worker.request_stop()
        self.repo.enqueue(self.template_id, [Clip(index=0), Clip(index=1)])
        self.assertEqual(self.worker.run_forever(), 0)

    def test_fal_ohne_prompt_scheitert_mit_klartext(self):
        """Ohne Beschreibung kann nichts erzeugt werden -- und das soll
        auffallen, bevor ein Aufruf bezahlt wird."""
        job_id = self.repo.enqueue(
            self.template_id,
            [Clip(index=0, source="fal"), Clip(index=1)],
        )
        self.assertTrue(self.worker.run_once())
        job = self.repo.get_job(job_id)
        self.assertEqual(job.status, STATUS_FAILED)
        self.assertIn("prompt", job.error)

    def test_fal_clip_wird_erzeugt_und_verbaut(self):
        """Mit einem Doppel fuer fal -- der Netzaufruf ist hier nicht moeglich.

        Geprueft wird, was der Worker fragt: Modell, zusammengesetzter Prompt
        (mit der Bildgroesse aus dem Template) und aufgerundete Laenge.
        """
        from move_worker.assembler import FramePlan

        plan = FramePlan.build(self.template, 25)
        gefragt = {}

        class GeneratorDoppel:
            def __init__(self, cache_dir, **_):
                self.cache_dir = Path(cache_dir)

            def erzeuge(self, anfrage):
                gefragt.update(
                    model=anfrage.model,
                    prompt=anfrage.prompt,
                    sekunden=anfrage.sekunden,
                )
                ziel = self.cache_dir / f"{anfrage.schluessel()}.mp4"
                # Wie der echte Generator: Verzeichnis anlegen.
                ziel.parent.mkdir(parents=True, exist_ok=True)
                # Lang genug, damit der Assembler ihn annimmt.
                einfarbig(ziel, "red", plan.source[0] + 5)
                return ziel

        job_id = self.repo.enqueue(
            self.template_id,
            [
                Clip(index=0, source="fal", prompt="a wide desert at dawn", model="fal-ai/x"),
                Clip(index=1),
            ],
        )
        with mock.patch("move_worker.runner.Generator", GeneratorDoppel):
            self.assertTrue(self.worker.run_once())

        job = self.repo.get_job(job_id)
        self.assertEqual(job.status, STATUS_DONE, job.error)
        self.assertEqual(gefragt["model"], "fal-ai/x")
        # Die Bildgroesse des Templates steht mit in der Beschreibung.
        self.assertIn("a wide desert at dawn", gefragt["prompt"])
        self.assertIn("medium shot", gefragt["prompt"])
        # 25 Bilder bei 25 fps -> 1 Sekunde, aufgerundet.
        self.assertEqual(gefragt["sekunden"], float(-(-plan.source[0] // 25)))

    def test_prompt_ueberlebt_den_umlauf_durch_die_datenbank(self):
        job_id = self.repo.enqueue(
            self.template_id,
            [Clip(index=0, source="fal", prompt="ein Satz", model="fal-ai/y"), Clip(index=1)],
        )
        job = self.repo.get_job(job_id)
        self.assertEqual(job.clips[0].prompt, "ein Satz")
        self.assertEqual(job.clips[0].model, "fal-ai/y")

    def test_hochgeladener_clip_wird_genutzt(self):
        from move_worker.assembler import FramePlan

        plan = FramePlan.build(self.template, 25)
        quelle = self.daten / "uploads" / "rot.mp4"
        quelle.parent.mkdir(parents=True, exist_ok=True)
        einfarbig(quelle, "red", plan.source[0])

        job_id = self.repo.enqueue(
            self.template_id,
            [Clip(index=0, source="upload", uri="uploads/rot.mp4"), Clip(index=1)],
        )
        self.assertTrue(self.worker.run_once())
        job = self.repo.get_job(job_id)
        self.assertEqual(job.status, STATUS_DONE, job.error)


if __name__ == "__main__":
    unittest.main()
