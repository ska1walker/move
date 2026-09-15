import json
import tempfile
import unittest
from pathlib import Path

from move_worker.templates import BeatGrid, Cut, CutTemplate, TemplateError


def vorlage(**abweichungen):
    """Ein gueltiges Minimal-Template, das einzelne Felder ueberschreiben laesst."""
    basis = {
        "id": "t",
        "name": "Test",
        "duration_ms": 4000,
        "shot_count": 3,
        "cuts": [
            {"at_ms": 0, "transition": "cut", "shot_scale": "wide"},
            {"at_ms": 1000, "transition": "crossfade", "transition_ms": 200},
            {"at_ms": 3000, "transition": "cut"},
        ],
    }
    basis.update(abweichungen)
    return basis


class TestGueltig(unittest.TestCase):
    def test_laedt(self):
        t = CutTemplate.from_json(vorlage())
        self.assertEqual(t.duration_ms, 4000)
        self.assertEqual(len(t.cuts), 3)

    def test_sichtbare_laengen_summieren_auf_die_laufzeit(self):
        t = CutTemplate.from_json(vorlage())
        self.assertEqual(t.shot_durations_ms, (1000, 2000, 1000))
        self.assertEqual(sum(t.shot_durations_ms), t.duration_ms)

    def test_json_umlauf(self):
        t = CutTemplate.from_json(vorlage())
        wieder = CutTemplate.from_json(t.to_json())
        self.assertEqual(t, wieder)

    def test_aus_datei(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.json"
            p.write_text(json.dumps(vorlage()), encoding="utf-8")
            self.assertEqual(CutTemplate.from_file(p).id, "t")

    def test_transition_ms_ist_optional(self):
        """Templates ohne das Feld bleiben lesbar -- es kam nachtraeglich dazu."""
        roh = vorlage()
        roh["cuts"] = [
            {"at_ms": 0, "transition": "cut"},
            {"at_ms": 1000, "transition": "cut"},
            {"at_ms": 3000, "transition": "cut"},
        ]
        t = CutTemplate.from_json(roh)
        self.assertEqual([c.transition_ms for c in t.cuts], [0, 0, 0])

    def test_beat_grid(self):
        roh = vorlage(beat_grid={"bpm": 120.0, "offsets_ms": [0, 500, 1000]})
        t = CutTemplate.from_json(roh)
        self.assertEqual(t.beat_grid, BeatGrid(bpm=120.0, offsets_ms=(0, 500, 1000)))


class TestZurueckgewiesen(unittest.TestCase):
    def pruefe(self, roh, text):
        with self.assertRaises(TemplateError) as ctx:
            CutTemplate.from_json(roh)
        self.assertIn(text, str(ctx.exception))

    def test_float_sekunden(self):
        roh = vorlage()
        roh["cuts"][1]["at_ms"] = 1000.5
        self.pruefe(roh, "ganze Zahl in Millisekunden")

    def test_float_auch_wenn_ganzzahlig(self):
        """1000.0 waere harmlos, 1000.4 nicht -- die Grenze zieht der Typ."""
        roh = vorlage()
        roh["cuts"][1]["at_ms"] = 1000.0
        self.pruefe(roh, "ganze Zahl in Millisekunden")

    def test_bool_ist_keine_zahl(self):
        roh = vorlage()
        roh["duration_ms"] = True
        self.pruefe(roh, "ganze Zahl in Millisekunden")

    def test_laufzeit_null(self):
        self.pruefe(vorlage(duration_ms=0), "duration_ms muss groesser als 0")

    def test_keine_cuts(self):
        self.pruefe(vorlage(cuts=[], shot_count=0), "cuts ist leer")

    def test_shot_count_passt_nicht(self):
        self.pruefe(vorlage(shot_count=99), "shot_count")

    def test_erster_cut_nicht_bei_null(self):
        roh = vorlage()
        roh["cuts"][0]["at_ms"] = 40
        self.pruefe(roh, "cuts[0].at_ms muss 0 sein")

    def test_erster_cut_mit_blende(self):
        roh = vorlage()
        roh["cuts"][0]["transition"] = "crossfade"
        roh["cuts"][0]["transition_ms"] = 100
        self.pruefe(roh, "cuts[0].transition muss")

    def test_at_ms_steigt_nicht(self):
        roh = vorlage()
        roh["cuts"][2]["at_ms"] = 1000
        self.pruefe(roh, "muss streng steigen")

    def test_at_ms_hinter_der_laufzeit(self):
        roh = vorlage()
        roh["cuts"][2]["at_ms"] = 4000
        self.pruefe(roh, "liegt nicht vor duration_ms")

    def test_unbekannter_uebergang(self):
        roh = vorlage()
        roh["cuts"][1]["transition"] = "wischblende"
        self.pruefe(roh, "ist unbekannt")

    def test_harter_schnitt_mit_laenge(self):
        roh = vorlage()
        roh["cuts"][2]["transition_ms"] = 200
        self.pruefe(roh, "harter Schnitt hat keine Laenge")

    def test_blende_ohne_laenge(self):
        roh = vorlage()
        roh["cuts"][1]["transition_ms"] = 0
        self.pruefe(roh, "braucht ein transition_ms groesser als 0")

    def test_blende_laenger_als_die_einstellung(self):
        roh = vorlage()
        # Einstellung 0 ist 1000 ms lang, die Blende soll 1200 ms dauern.
        roh["cuts"][1]["transition_ms"] = 1200
        self.pruefe(roh, "laenger als die")

    def test_unbekanntes_feld(self):
        roh = vorlage()
        roh["cuts"][1]["tempo"] = 3
        self.pruefe(roh, "unbekannte Felder")

    def test_cuts_ist_keine_liste(self):
        self.pruefe(vorlage(cuts={}), "keine Liste")


class TestCut(unittest.TestCase):
    def test_vorgaben(self):
        c = Cut(at_ms=0)
        self.assertEqual(c.transition, "cut")
        self.assertEqual(c.transition_ms, 0)


if __name__ == "__main__":
    unittest.main()
