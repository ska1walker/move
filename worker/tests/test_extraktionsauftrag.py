"""Der Weg vom Upload zum Template, ueber die Warteschlange.

Das ist der Schritt, der in der Oberflaeche gefehlt hat: ohne ihn musste
jemand `move_worker extract` per `kubectl exec` im Worker-Pod tippen, um
ueberhaupt ein Template zu bekommen -- und ohne Template zeigt die Seite nur
einen Hinweistext.

Geprueft wird die MECHANIK, nicht die Qualitaet der Erkennung: dass ein
Auftrag genau einmal gezogen wird, dass ein Pod-Neustart ihn nicht verliert,
dass ein Fehler in der Zeile landet statt still zu verschwinden, und dass
Extraktion vor Rendern kommt.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from move_worker.assembler import RenderSettings
from move_worker.repository import Clip, Repository
from move_worker.runner import Worker
from move_worker.templates import TRANSITION_CUT, Cut, CutTemplate


def _repo(d: Path) -> Repository:
    r = Repository(db_path=d / "db" / "move.sqlite3")
    r.connect()
    r.migrate()
    return r


class TestWarteschlange(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.repo = _repo(self.d)
        self.upload_id = self.repo.save_upload("t.mp4", "uploads/t.mp4", "video", 10)

    def test_auftrag_wird_genau_einmal_gezogen(self):
        self.repo.enqueue_extract(self.upload_id, "A")
        self.assertIsNotNone(self.repo.claim_next_extract())
        self.assertIsNone(self.repo.claim_next_extract())

    def test_aeltester_zuerst(self):
        erste = self.repo.enqueue_extract(self.upload_id, "erste")
        # created_at ist Millisekunden; ohne Abstand waere die Reihenfolge
        # innerhalb derselben Millisekunde nicht festgelegt.
        import time

        time.sleep(0.002)
        self.repo.enqueue_extract(self.upload_id, "zweite")
        geholt = self.repo.claim_next_extract()
        self.assertEqual(geholt.id, erste)

    def test_neustart_gibt_haengende_zurueck(self):
        self.repo.enqueue_extract(self.upload_id, "A")
        self.repo.claim_next_extract()
        self.assertEqual(self.repo.reset_stale_extract(), 1)
        self.assertIsNotNone(self.repo.claim_next_extract())

    def test_fehler_landet_in_der_zeile(self):
        job_id = self.repo.enqueue_extract(self.upload_id, "A")
        self.repo.claim_next_extract()
        self.repo.mark_extract_failed(job_id, "so nicht")
        zeile = self.repo.connect().execute(
            "SELECT status, error FROM extract_job WHERE id = ?", (job_id,)
        ).fetchone()
        self.assertEqual(zeile["status"], "failed")
        self.assertIn("so nicht", zeile["error"])

    def test_bild_als_quelle_wird_abgelehnt(self):
        bild = self.repo.save_upload("m.png", "uploads/m.png", "bild", 5)
        job_id = self.repo.enqueue_extract(bild, "A")
        worker = Worker(repo=self.repo, settings=RenderSettings())
        # Der Auftrag wird bearbeitet (True), scheitert aber mit Klartext.
        self.assertTrue(worker.extract_once())
        zeile = self.repo.connect().execute(
            "SELECT status, error FROM extract_job WHERE id = ?", (job_id,)
        ).fetchone()
        self.assertEqual(zeile["status"], "failed")
        # Die Meldung muss sagen, WARUM -- sonst sucht jemand im Video.
        self.assertIn("bild", zeile["error"])
        self.assertIn("extrahieren geht nur aus einem Video", zeile["error"])

    def test_fehlende_datei_nennt_den_pfad(self):
        job_id = self.repo.enqueue_extract(self.upload_id, "A")
        worker = Worker(repo=self.repo, settings=RenderSettings())
        self.assertTrue(worker.extract_once())
        zeile = self.repo.connect().execute(
            "SELECT status, error FROM extract_job WHERE id = ?", (job_id,)
        ).fetchone()
        self.assertEqual(zeile["status"], "failed")
        self.assertIn("uploads/t.mp4", zeile["error"])


class TestExtraktionGehtVor(unittest.TestCase):
    """Reihenfolge ist bei Concurrency 1 die ganze Priorisierung."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.repo = _repo(self.d)
        vorlage = CutTemplate(
            id="t1",
            name="T",
            source_label="",
            duration_ms=2000,
            cuts=(Cut(at_ms=0, transition=TRANSITION_CUT, shot_scale="medium"),),
            beat_grid=None,
            shot_count=1,
        )
        self.repo.save_template(vorlage)

    def test_extraktion_vor_render(self):
        # Render zuerst einreihen, Extraktion danach -- trotzdem muss die
        # Extraktion zuerst gezogen werden.
        self.repo.enqueue("t1", [Clip(index=0)])
        upload_id = self.repo.save_upload("t.mp4", "uploads/fehlt.mp4", "video", 1)
        extrakt = self.repo.enqueue_extract(upload_id, "A")

        worker = Worker(repo=self.repo, settings=RenderSettings())
        worker.run_once()

        zeile = self.repo.connect().execute(
            "SELECT status FROM extract_job WHERE id = ?", (extrakt,)
        ).fetchone()
        # Angefasst (hier: gescheitert, weil die Datei fehlt) -- der
        # Render-Job steht dagegen noch in der Warteschlange.
        self.assertNotEqual(zeile["status"], "queued")
        self.assertEqual(self.repo.count_by_status().get("queued"), 1)


if __name__ == "__main__":
    unittest.main()
