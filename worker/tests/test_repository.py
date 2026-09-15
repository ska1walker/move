import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from move_worker import repository
from move_worker.repository import (
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_RUNNING,
    Clip,
    Repository,
    RepositoryError,
)
from move_worker.templates import CutTemplate

TEMPLATE = {
    "id": "t1",
    "name": "Test",
    "source_label": "hand",
    "duration_ms": 4000,
    "shot_count": 2,
    "cuts": [
        {"at_ms": 0, "transition": "cut", "shot_scale": "wide"},
        {"at_ms": 2000, "transition": "crossfade", "transition_ms": 200},
    ],
    "beat_grid": {"bpm": 120.0, "offsets_ms": [0, 500]},
}


class RepoFall(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.tmp.name) / "db" / "move.sqlite3")
        self.repo.migrate()
        self.template = CutTemplate.from_json(TEMPLATE)

    def tearDown(self):
        self.repo.close()
        self.tmp.cleanup()


class TestSchema(RepoFall):
    def test_migrate_ist_mehrfach_aufrufbar(self):
        self.repo.migrate()
        self.repo.migrate()

    def test_wal_ist_an(self):
        modus = self.repo.connect().execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(modus.lower(), "wal")

    def test_datei_wird_angelegt(self):
        self.assertTrue(self.repo.db_path.is_file())

    def test_status_ist_eingeschraenkt(self):
        """Ein Tippfehler im Status soll die Zeile nicht stillschweigend annehmen."""
        template_id = self.repo.save_template(self.template)
        with self.assertRaises(sqlite3.IntegrityError):
            self.repo.connect().execute(
                "INSERT INTO render_job (id, template_id, status, clips, created_at) "
                "VALUES ('x', ?, 'runnning', '[]', 1)",
                (template_id,),
            )

    def test_job_ohne_template_wird_abgelehnt(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.repo.connect().execute(
                "INSERT INTO render_job (id, template_id, status, clips, created_at) "
                "VALUES ('x', 'gibtsnicht', 'queued', '[]', 1)"
            )


class TestTemplates(RepoFall):
    def test_umlauf(self):
        kennung = self.repo.save_template(self.template)
        self.assertEqual(kennung, "t1")
        wieder = self.repo.get_template("t1")
        self.assertEqual(wieder.duration_ms, 4000)
        self.assertEqual(wieder.cuts, self.template.cuts)
        self.assertEqual(wieder.beat_grid, self.template.beat_grid)

    def test_unbekanntes_template(self):
        with self.assertRaises(RepositoryError):
            self.repo.get_template("gibtsnicht")

    def test_erneutes_speichern_ueberschreibt(self):
        self.repo.save_template(self.template)
        self.repo.save_template(self.template)
        anzahl = self.repo.connect().execute(
            "SELECT COUNT(*) FROM cut_template"
        ).fetchone()[0]
        self.assertEqual(anzahl, 1)


class TestWarteschlange(RepoFall):
    def setUp(self):
        super().setUp()
        self.template_id = self.repo.save_template(self.template)
        self.clips = [Clip(index=0), Clip(index=1)]

    def test_leere_warteschlange(self):
        self.assertIsNone(self.repo.claim_next())

    def test_ziehen_setzt_running_und_startzeit(self):
        job_id = self.repo.enqueue(self.template_id, self.clips)
        job = self.repo.claim_next()
        self.assertIsNotNone(job)
        self.assertEqual(job.id, job_id)
        self.assertEqual(job.status, STATUS_RUNNING)
        self.assertIsNotNone(job.started_at)
        self.assertEqual(self.repo.get_job(job_id).status, STATUS_RUNNING)

    def test_ein_job_wird_nur_einmal_gezogen(self):
        self.repo.enqueue(self.template_id, self.clips)
        self.assertIsNotNone(self.repo.claim_next())
        self.assertIsNone(self.repo.claim_next())

    def test_aeltester_zuerst(self):
        with mock.patch.object(repository, "now_ms", side_effect=[300, 100, 200]):
            spaet = self.repo.enqueue(self.template_id, self.clips)
            frueh = self.repo.enqueue(self.template_id, self.clips)
            mittel = self.repo.enqueue(self.template_id, self.clips)

        self.assertEqual(self.repo.claim_next().id, frueh)
        self.assertEqual(self.repo.claim_next().id, mittel)
        self.assertEqual(self.repo.claim_next().id, spaet)

    def test_clips_ueberleben_den_umlauf(self):
        clips = [Clip(index=0, source="upload", uri="uploads/a.mp4"), Clip(index=1)]
        self.repo.enqueue(self.template_id, clips)
        job = self.repo.claim_next()
        self.assertEqual(job.clips[0].source, "upload")
        self.assertEqual(job.clips[0].uri, "uploads/a.mp4")
        self.assertEqual(job.clips[1].source, "placeholder")
        self.assertFalse(job.nur_platzhalter)

    def test_fertig(self):
        job_id = self.repo.enqueue(self.template_id, self.clips)
        self.repo.claim_next()
        self.repo.mark_done(job_id, "renders/x.mp4")
        job = self.repo.get_job(job_id)
        self.assertEqual(job.status, STATUS_DONE)
        self.assertEqual(job.output_uri, "renders/x.mp4")
        self.assertIsNotNone(job.finished_at)
        self.assertIsNone(job.error)

    def test_gescheitert(self):
        job_id = self.repo.enqueue(self.template_id, self.clips)
        self.repo.claim_next()
        self.repo.mark_failed(job_id, "ffmpeg endete mit Code 1")
        job = self.repo.get_job(job_id)
        self.assertEqual(job.status, STATUS_FAILED)
        self.assertIn("Code 1", job.error)

    def test_zaehlung(self):
        fertig = self.repo.enqueue(self.template_id, self.clips)
        self.repo.enqueue(self.template_id, self.clips)
        self.repo.claim_next()
        self.repo.mark_done(fertig, "x")
        self.assertEqual(self.repo.count_by_status(), {STATUS_QUEUED: 1, STATUS_DONE: 1})

    def test_haengende_jobs_kommen_zurueck(self):
        """Recreate beendet den Pod mitten im Render -- sonst blieben sie liegen."""
        job_id = self.repo.enqueue(self.template_id, self.clips)
        self.repo.claim_next()
        self.assertEqual(self.repo.reset_stale_running(), 1)
        self.assertEqual(self.repo.get_job(job_id).status, STATUS_QUEUED)
        self.assertEqual(self.repo.claim_next().id, job_id)

    def test_fertige_jobs_werden_nicht_zurueckgesetzt(self):
        job_id = self.repo.enqueue(self.template_id, self.clips)
        self.repo.claim_next()
        self.repo.mark_done(job_id, "x")
        self.assertEqual(self.repo.reset_stale_running(), 0)
        self.assertEqual(self.repo.get_job(job_id).status, STATUS_DONE)


class TestZweiVerbindungen(RepoFall):
    def test_zwei_worker_ziehen_nicht_denselben_job(self):
        """BEGIN IMMEDIATE nimmt die Schreibsperre sofort, nicht erst beim UPDATE."""
        template_id = self.repo.save_template(self.template)
        job_id = self.repo.enqueue(template_id, [Clip(index=0), Clip(index=1)])

        zweiter = Repository(self.repo.db_path)
        try:
            gezogen = [self.repo.claim_next(), zweiter.claim_next()]
        finally:
            zweiter.close()

        treffer = [j for j in gezogen if j is not None]
        self.assertEqual(len(treffer), 1, "der Job wurde doppelt gezogen")
        self.assertEqual(treffer[0].id, job_id)


if __name__ == "__main__":
    unittest.main()
