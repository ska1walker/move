"""Die Mustertemplates und ihr Einsetzen.

WARUM DAS GEPRUEFT WIRD: die Muster sind das erste, was jemand sieht, der die
App oeffnet. Ein ungueltiges Muster faellt sonst erst dort auf -- und dann in
einer Fehlermeldung statt in einem Beispiel.

Der teuerste Fehler waere ein Seed, der ueberschreibt. Wer ein Muster umbaut,
soll seine Fassung behalten; ein Seed, der bei jedem Worker-Start zurueckschlaegt,
nimmt sie weg, ohne dass etwas scheitert. Dafuer gibt es hier einen eigenen
Test.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from move_worker.assembler import FramePlan, RenderSettings
from move_worker.repository import Repository
from move_worker.runner import Worker, muster_ordner
from move_worker.templates import TRANSITION_CROSSFADE, CutTemplate


def _dateien() -> list[Path]:
    ordner = muster_ordner()
    assert ordner is not None, "kein examples-Ordner gefunden"
    return sorted(ordner.glob("muster-*.json"))


class TestMusterDateien(unittest.TestCase):
    def test_es_gibt_drei(self):
        self.assertEqual(len(_dateien()), 3, [p.name for p in _dateien()])

    def test_jedes_ist_gueltig(self):
        for datei in _dateien():
            with self.subTest(datei=datei.name):
                vorlage = CutTemplate.from_json(json.loads(datei.read_text(encoding="utf-8")))
                # from_json validiert schon; der zweite Aufruf ist die Zusage,
                # dass validate() keine Seiteneffekte hat.
                vorlage.validate()

    def test_jedes_hat_eine_feste_id(self):
        # Ohne feste id koennte der Seed nicht erkennen, was schon da ist --
        # und legte bei jedem Start ein weiteres Exemplar an.
        for datei in _dateien():
            with self.subTest(datei=datei.name):
                roh = json.loads(datei.read_text(encoding="utf-8"))
                self.assertTrue(roh.get("id"), "id fehlt")
                self.assertEqual(roh["id"], datei.stem, "id sollte dem Dateinamen entsprechen")

    def test_ids_sind_eindeutig(self):
        ids = [json.loads(d.read_text(encoding="utf-8"))["id"] for d in _dateien()]
        self.assertEqual(len(ids), len(set(ids)))

    def test_quelle_sagt_dass_es_handgeschrieben_ist(self):
        # source_label landet in der Oberflaeche in der Spalte "Quelle". Ein
        # Muster darf dort nicht wie ein extrahiertes Template aussehen.
        for datei in _dateien():
            with self.subTest(datei=datei.name):
                roh = json.loads(datei.read_text(encoding="utf-8"))
                self.assertIn("handgeschrieben", roh["source_label"])

    def test_zeitstempel_sind_ganze_millisekunden(self):
        # CLAUDE.md: "Zeitstempel immer Millisekunden als Integer. Keine
        # Float-Sekunden." Ein Float rutscht in JSON leicht durch.
        for datei in _dateien():
            roh = json.loads(datei.read_text(encoding="utf-8"))
            with self.subTest(datei=datei.name):
                self.assertIsInstance(roh["duration_ms"], int)
                for schnitt in roh["cuts"]:
                    self.assertIsInstance(schnitt["at_ms"], int)
                    self.assertIsInstance(schnitt["transition_ms"], int)
                for versatz in roh["beat_grid"]["offsets_ms"]:
                    self.assertIsInstance(versatz, int)

    def test_jedes_ergibt_einen_plan_der_aufgeht(self):
        # Der Plan ist die eigentliche Zusage des Templates: die Summe der
        # sichtbaren Bilder muss die Laufzeit treffen, sonst waere der Schnitt
        # keine Arithmetik.
        einstellungen = RenderSettings()
        for datei in _dateien():
            with self.subTest(datei=datei.name):
                vorlage = CutTemplate.from_json(json.loads(datei.read_text(encoding="utf-8")))
                plan = FramePlan.build(vorlage, einstellungen.fps)
                erwartet = (vorlage.duration_ms * einstellungen.fps + 500) // 1000
                self.assertEqual(plan.total, erwartet)

    def test_die_drei_zeigen_verschiedene_schnittgedanken(self):
        """Drei Muster, die sich nur in Zahlen unterscheiden, lehren nichts."""
        nach_id = {
            json.loads(d.read_text(encoding="utf-8"))["id"]: CutTemplate.from_json(
                json.loads(d.read_text(encoding="utf-8"))
            )
            for d in _dateien()
        }

        schnell = nach_id["muster-schnelle-montage"]
        trailer = nach_id["muster-trailer-aufbau"]
        ruhig = nach_id["muster-ruhige-sequenz"]

        # Schnelle Montage: nur harte Schnitte, gleichmaessiges Raster.
        self.assertTrue(all(c.transition != TRANSITION_CROSSFADE for c in schnell.cuts))
        laengen = schnell.shot_durations_ms
        self.assertEqual(len(set(laengen)), 1, f"sollten alle gleich lang sein: {laengen}")

        # Trailer-Aufbau: beschleunigt, und endet dann auf einer GEHALTENEN
        # Einstellung.
        #
        # Die erste Fassung dieses Tests behauptete, die letzte Einstellung
        # sei die kuerzeste -- und scheiterte an (3600, 3000, 2400, 1800,
        # 1200, 600, 600, 1800). Das Template hat recht, nicht der Test: nach
        # dem Stakkato steht im Trailer eine gehaltene Schlusseinstellung, die
        # Titelkarte oder das Heldenbild. Ohne sie endet der Schnitt mitten
        # im Lauf.
        t_laengen = trailer.shot_durations_ms
        # Es beschleunigt: bis zum Stakkato wird keine Einstellung laenger.
        bis_stakkato = t_laengen[:-1]
        self.assertEqual(
            list(bis_stakkato),
            sorted(bis_stakkato, reverse=True),
            f"sollte durchweg kuerzer werden: {t_laengen}",
        )
        # Und deutlich: die kuerzeste ist hoechstens ein Viertel der ersten.
        self.assertLessEqual(min(t_laengen), t_laengen[0] / 4, t_laengen)
        # Die Schlusseinstellung wird wieder gehalten.
        self.assertGreater(t_laengen[-1], min(t_laengen), t_laengen)

        # Ruhige Sequenz: wenige, lange Einstellungen, ueberwiegend Blenden.
        self.assertLessEqual(ruhig.shot_count, 4)
        blenden = sum(1 for c in ruhig.cuts if c.transition == TRANSITION_CROSSFADE)
        self.assertGreaterEqual(blenden, 3)
        self.assertTrue(all(l >= 3000 for l in ruhig.shot_durations_ms), ruhig.shot_durations_ms)


class TestSeed(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.repo = Repository(db_path=self.d / "db" / "move.sqlite3")
        self.repo.connect()
        self.repo.migrate()
        self.worker = Worker(repo=self.repo)

    def test_erster_lauf_legt_drei_an(self):
        self.assertEqual(self.worker.seed_muster(), 3)

    def test_zweiter_lauf_legt_nichts_an(self):
        self.worker.seed_muster()
        self.assertEqual(self.worker.seed_muster(), 0)

    def test_seed_ueberschreibt_keine_aenderung(self):
        """Der teuerste Fehler: der Seed nimmt dem Nutzer seine Fassung weg."""
        self.worker.seed_muster()
        vorlage = self.repo.get_template("muster-schnelle-montage")

        geaendert = CutTemplate(
            id=vorlage.id,
            name="Von Hand umgebaut",
            source_label=vorlage.source_label,
            duration_ms=vorlage.duration_ms,
            cuts=vorlage.cuts,
            beat_grid=vorlage.beat_grid,
            shot_count=vorlage.shot_count,
        )
        self.repo.save_template(geaendert)

        # Ein Worker-Neustart, also ein weiterer Seed-Lauf.
        self.assertEqual(self.worker.seed_muster(), 0)
        self.assertEqual(
            self.repo.get_template("muster-schnelle-montage").name,
            "Von Hand umgebaut",
        )

    def test_seed_laesst_eigene_templates_unberuehrt(self):
        eigen = CutTemplate(
            id="eigenes",
            name="Aus einem Video",
            source_label="trailer.mp4",
            duration_ms=4000,
            cuts=vorlage_cuts(),
            beat_grid=None,
            shot_count=2,
        )
        self.repo.save_template(eigen)
        self.worker.seed_muster()
        self.assertEqual(self.repo.get_template("eigenes").name, "Aus einem Video")

    def test_ein_kaputtes_muster_haelt_den_worker_nicht_auf(self):
        """Die Job-Schleife ist wichtiger als ein Beispiel."""
        ordner = self.d / "muster"
        ordner.mkdir()
        (ordner / "muster-kaputt.json").write_text("{ kein json", encoding="utf-8")
        gut = json.loads((muster_ordner() / "muster-ruhige-sequenz.json").read_text())
        (ordner / "muster-gut.json").write_text(json.dumps(gut), encoding="utf-8")

        import os

        alt = os.environ.get("MOVE_MUSTER_DIR")
        os.environ["MOVE_MUSTER_DIR"] = str(ordner)
        try:
            # Das gute wird angelegt, das kaputte uebersprungen -- ohne
            # Ausnahme nach oben.
            self.assertEqual(self.worker.seed_muster(), 1)
        finally:
            if alt is None:
                del os.environ["MOVE_MUSTER_DIR"]
            else:
                os.environ["MOVE_MUSTER_DIR"] = alt


def vorlage_cuts():
    from move_worker.templates import TRANSITION_CUT, Cut

    return (
        Cut(at_ms=0, transition=TRANSITION_CUT, shot_scale="wide"),
        Cut(at_ms=2000, transition=TRANSITION_CUT, shot_scale="close"),
    )


if __name__ == "__main__":
    unittest.main()
