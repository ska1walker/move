"""Konsistente Personen: dieselbe Figur, dieselben Eingaben.

WAS HIER GEPRUEFT WIRD, und warum es nicht selbstverstaendlich ist: fal.ai
erinnert sich zwischen zwei Aufrufen an nichts. Dass eine Person in acht
Einstellungen gleich aussieht, kann also nur daran liegen, dass alle acht
Aufrufe dieselben Eingaben tragen -- Beschreibung, Referenzbild und Seed.
Genau das messen diese Tests, gegen ein Doppel statt gegen fal: fal.ai ist
vom Egress-Proxy gesperrt, und ein echter Aufruf waere ausserdem bezahlt.

Der teuerste Fehler waere ein Zwischenspeicher, der zwei Figuren
zusammenwirft. Dann bekaeme Figur B still das Gesicht von Figur A, ohne dass
etwas scheitert. Dafuer gibt es hier einen eigenen Test.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from move_worker.assembler import RenderSettings
from move_worker.generierung import Anfrage, Generator
from move_worker.repository import Clip, Figur, Repository
from move_worker.runner import Worker
from move_worker.templates import TRANSITION_CUT, Cut, CutTemplate


class FalDoppel:
    """Zeichnet Aufrufe und Uploads auf."""

    def __init__(self, antwort):
        self.antwort = antwort
        self.aufrufe: list[tuple[str, dict]] = []
        self.uploads: list[str] = []

    def subscribe(self, application, arguments, **kwargs):
        self.aufrufe.append((application, dict(arguments)))
        return self.antwort

    def upload_file(self, path):
        self.uploads.append(path)
        return f"https://fal.example/{Path(path).name}"


def _template(shots: int = 3) -> CutTemplate:
    schritt = 2000
    return CutTemplate(
        id="t1",
        name="Test",
        source_label="test.mp4",
        duration_ms=schritt * shots,
        cuts=tuple(
            Cut(at_ms=i * schritt, transition=TRANSITION_CUT, shot_scale="medium")
            for i in range(shots)
        ),
        beat_grid=None,
        shot_count=shots,
    )


class TestSeedStabil(unittest.TestCase):
    def test_seed_aus_der_id_ist_stabil(self):
        a = Figur(id="abc", name="Mara")
        b = Figur(id="abc", name="Mara")
        self.assertEqual(a.wirksamer_seed(), b.wirksamer_seed())

    def test_verschiedene_figuren_verschiedene_seeds(self):
        a = Figur(id="abc", name="Mara")
        b = Figur(id="xyz", name="Jan")
        self.assertNotEqual(a.wirksamer_seed(), b.wirksamer_seed())

    def test_gesetzter_seed_gewinnt(self):
        self.assertEqual(Figur(id="abc", name="Mara", seed=42).wirksamer_seed(), 42)

    def test_seed_passt_in_31_bit(self):
        # Manche Modelle lehnen groessere Zahlen ab.
        for kennung in ("a", "bb", "ccc", "dddd", "eeeee"):
            self.assertLess(Figur(id=kennung, name="x").wirksamer_seed(), 2**31)


class TestZwischenspeicherTrenntFiguren(unittest.TestCase):
    """Der teuerste Fehler: zwei Figuren, ein Eintrag."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.bild_a = self.d / "a.png"
        self.bild_a.write_bytes(b"\x89PNG" + b"A" * 64)
        self.bild_b = self.d / "b.png"
        self.bild_b.write_bytes(b"\x89PNG" + b"B" * 64)

    def _anfrage(self, bild: Path | None, seed: int | None) -> Anfrage:
        return Anfrage(
            prompt="dieselbe Beschreibung",
            sekunden=2.0,
            breite=1920,
            height=1080,
            referenz_bild=bild,
            seed=seed,
        )

    def test_gleicher_prompt_anderes_bild_anderer_schluessel(self):
        self.assertNotEqual(
            self._anfrage(self.bild_a, 1).schluessel(),
            self._anfrage(self.bild_b, 1).schluessel(),
        )

    def test_gleicher_prompt_anderer_seed_anderer_schluessel(self):
        self.assertNotEqual(
            self._anfrage(self.bild_a, 1).schluessel(),
            self._anfrage(self.bild_a, 2).schluessel(),
        )

    def test_ohne_bild_anders_als_mit_bild(self):
        self.assertNotEqual(
            self._anfrage(None, 1).schluessel(),
            self._anfrage(self.bild_a, 1).schluessel(),
        )

    def test_gleiche_figur_gleicher_schluessel(self):
        self.assertEqual(
            self._anfrage(self.bild_a, 1).schluessel(),
            self._anfrage(self.bild_a, 1).schluessel(),
        )

    def test_bild_geht_ueber_den_inhalt_ein_nicht_ueber_den_pfad(self):
        # Zwei Dateien mit gleichem Inhalt an verschiedenen Pfaden sind
        # dasselbe Bild. Sonst kostete ein Umbenennen eine neue Generierung.
        kopie = self.d / "a-kopie.png"
        kopie.write_bytes(self.bild_a.read_bytes())
        self.assertEqual(
            self._anfrage(self.bild_a, 1).schluessel(),
            self._anfrage(kopie, 1).schluessel(),
        )

    def test_fehlendes_bild_faellt_beim_schluessel_auf(self):
        with self.assertRaises(Exception):
            self._anfrage(self.d / "gibtsnicht.png", 1).schluessel()


class TestFigurLandetImAufruf(unittest.TestCase):
    """Gegen das Doppel: kommt die Figur wirklich bei fal an?"""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        (self.d / "uploads").mkdir()
        self.bild = self.d / "uploads" / "mara.png"
        self.bild.write_bytes(b"\x89PNG" + b"M" * 64)

        self.repo = Repository(db_path=self.d / "db" / "move.sqlite3")
        self.repo.connect()
        self.repo.migrate()
        self.figur_id = self.repo.save_figur(
            Figur(
                id="",
                name="Mara",
                beschreibung="Frau, Ende dreissig, dunkle Locken",
                referenz_uri="uploads/mara.png",
            )
        )
        self.worker = Worker(repo=self.repo, settings=RenderSettings())

    def _erzeuge(self, clip: Clip, doppel: FalDoppel, index: int = 0):
        from move_worker.assembler import FramePlan

        vorlage = _template()
        plan = FramePlan.build(vorlage, self.worker.settings.fps)
        # Der Generator wird im Worker gebaut; hier wird er ersetzt, damit das
        # Doppel greift, ohne dass erzeuge_clip dafuer einen Parameter braucht.
        erzeugt: list[Anfrage] = []
        generator = Generator(cache_dir=self.d / "generated", client=doppel)

        echt = generator.erzeuge

        def aufzeichnen(anfrage: Anfrage):
            erzeugt.append(anfrage)
            return echt(anfrage)

        generator.erzeuge = aufzeichnen  # type: ignore[method-assign]

        import move_worker.runner as runner_modul

        alt = runner_modul.Generator
        runner_modul.Generator = lambda **kw: generator  # type: ignore[assignment]
        try:
            self.worker.erzeuge_clip(clip, vorlage, plan, index, self.d)
        finally:
            runner_modul.Generator = alt  # type: ignore[assignment]
        return erzeugt

    def test_beschreibung_steht_vor_dem_prompt(self):
        doppel = FalDoppel({"video": {"url": "https://fal.example/v.mp4"}})
        clip = Clip(index=0, source="fal", prompt="sie geht durch den Regen", figur_id=self.figur_id)
        try:
            erzeugt = self._erzeuge(clip, doppel)
        except Exception:
            # Der Download der Beispiel-URL scheitert ohne Netz; der Aufruf
            # an fal ist davor passiert und genau der wird geprueft.
            pass
        self.assertTrue(doppel.aufrufe, "es gab keinen fal-Aufruf")
        _, argumente = doppel.aufrufe[0]
        self.assertTrue(
            argumente["prompt"].startswith("Frau, Ende dreissig, dunkle Locken."),
            f"Beschreibung steht nicht vorn: {argumente['prompt']!r}",
        )
        self.assertIn("sie geht durch den Regen", argumente["prompt"])

    def test_referenzbild_und_seed_gehen_mit(self):
        doppel = FalDoppel({"video": {"url": "https://fal.example/v.mp4"}})
        clip = Clip(index=0, source="fal", prompt="p", figur_id=self.figur_id)
        try:
            self._erzeuge(clip, doppel)
        except Exception:
            pass
        _, argumente = doppel.aufrufe[0]
        self.assertEqual(argumente["image_url"], "https://fal.example/mara.png")
        self.assertEqual(
            argumente["seed"], self.repo.get_figur(self.figur_id).wirksamer_seed()
        )

    def test_ohne_figur_kein_bild_und_kein_seed(self):
        doppel = FalDoppel({"video": {"url": "https://fal.example/v.mp4"}})
        clip = Clip(index=0, source="fal", prompt="p")
        try:
            self._erzeuge(clip, doppel)
        except Exception:
            pass
        _, argumente = doppel.aufrufe[0]
        self.assertNotIn("image_url", argumente)
        self.assertNotIn("seed", argumente)

    def test_fehlendes_referenzbild_scheitert_statt_still_zu_erzeugen(self):
        kaputt = self.repo.save_figur(
            Figur(id="", name="Weg", beschreibung="x", referenz_uri="uploads/weg.png")
        )
        doppel = FalDoppel({"video": {"url": "https://fal.example/v.mp4"}})
        clip = Clip(index=0, source="fal", prompt="p", figur_id=kaputt)
        with self.assertRaises(FileNotFoundError):
            self._erzeuge(clip, doppel)
        # Und zwar BEVOR ein bezahlter Aufruf passiert ist.
        self.assertEqual(doppel.aufrufe, [])


class TestBildProEinstellung(unittest.TestCase):
    """Ein Bild fuer GENAU DIESE Einstellung, nicht fuer die Figur.

    Die beiden Wege meinen Verschiedenes: `figur_id` ist ein Bild fuer viele
    Einstellungen (Konsistenz), `bild_uri` ist ein Bild fuer eine (Abwechslung).
    Zusammen gewinnt das Spezifischere -- das Bild der Einstellung -- waehrend
    Beschreibung und Seed der Figur in Kraft bleiben. Genau diese Aufteilung
    steht hier.
    """

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        (self.d / "uploads").mkdir()
        (self.d / "uploads" / "figur.png").write_bytes(b"\x89PNG" + b"F" * 64)
        (self.d / "uploads" / "szene.png").write_bytes(b"\x89PNG" + b"S" * 64)

        self.repo = Repository(db_path=self.d / "db" / "move.sqlite3")
        self.repo.connect()
        self.repo.migrate()
        self.figur_id = self.repo.save_figur(
            Figur(
                id="",
                name="Mara",
                beschreibung="Frau, Ende dreissig",
                referenz_uri="uploads/figur.png",
            )
        )
        self.worker = Worker(repo=self.repo, settings=RenderSettings())

    def _argumente(self, clip: Clip) -> dict:
        helfer = TestFigurLandetImAufruf._erzeuge
        doppel = FalDoppel({"video": {"url": "https://fal.example/v.mp4"}})
        try:
            helfer(self, clip, doppel)
        except Exception:
            # Der Download der Beispiel-URL scheitert ohne Netz. Der Aufruf an
            # fal liegt davor, und der ist hier die Messung.
            pass
        self.assertTrue(doppel.aufrufe, "es gab keinen fal-Aufruf")
        return doppel.aufrufe[0][1]

    def _modell(self, clip: Clip) -> str:
        helfer = TestFigurLandetImAufruf._erzeuge
        doppel = FalDoppel({"video": {"url": "https://fal.example/v.mp4"}})
        try:
            helfer(self, clip, doppel)
        except Exception:
            pass
        return doppel.aufrufe[0][0]

    def test_bild_der_einstellung_geht_mit(self):
        argumente = self._argumente(
            Clip(index=0, source="fal", prompt="p", bild_uri="uploads/szene.png")
        )
        self.assertEqual(argumente["image_url"], "https://fal.example/szene.png")

    def test_bild_der_einstellung_gewinnt_gegen_das_der_figur(self):
        argumente = self._argumente(
            Clip(
                index=0,
                source="fal",
                prompt="p",
                figur_id=self.figur_id,
                bild_uri="uploads/szene.png",
            )
        )
        self.assertEqual(argumente["image_url"], "https://fal.example/szene.png")

    def test_beschreibung_und_seed_der_figur_bleiben(self):
        """Das Bild wechselt, die Person nicht."""
        argumente = self._argumente(
            Clip(
                index=0,
                source="fal",
                prompt="sie geht weiter",
                figur_id=self.figur_id,
                bild_uri="uploads/szene.png",
            )
        )
        self.assertTrue(argumente["prompt"].startswith("Frau, Ende dreissig."))
        self.assertEqual(
            argumente["seed"], self.repo.get_figur(self.figur_id).wirksamer_seed()
        )

    def test_bild_der_einstellung_waehlt_das_bildmodell(self):
        """Sonst ginge es an einen Text-zu-Video-Endpunkt: bezahlt und wirkungslos."""
        from move_worker.generierung import DEFAULT_BILD_MODEL, DEFAULT_MODEL

        mit = self._modell(Clip(index=0, source="fal", prompt="p", bild_uri="uploads/szene.png"))
        ohne = self._modell(Clip(index=0, source="fal", prompt="p"))
        self.assertEqual(mit, DEFAULT_BILD_MODEL)
        self.assertEqual(ohne, DEFAULT_MODEL)

    def test_fehlendes_bild_scheitert_vor_dem_bezahlten_aufruf(self):
        doppel = FalDoppel({"video": {"url": "https://fal.example/v.mp4"}})
        clip = Clip(index=0, source="fal", prompt="p", bild_uri="uploads/gibtsnicht.png")
        with self.assertRaises(FileNotFoundError):
            TestFigurLandetImAufruf._erzeuge(self, clip, doppel)
        self.assertEqual(doppel.aufrufe, [])

    def test_bild_kommt_nicht_aus_dem_datenverzeichnis_heraus(self):
        """Der Worker liest aus der Datenbank, nicht aus einem HTTP-Aufruf.

        Was in einer Zeile steht, hat er nicht selbst geschrieben. Ohne diese
        Grenze waere `../` eine beliebige Datei, die als Referenzbild zu fal
        hochgeladen wird -- also nach draussen geht.
        """
        (self.d.parent / "geheim.png").write_bytes(b"\x89PNG" + b"X" * 32)
        doppel = FalDoppel({"video": {"url": "https://fal.example/v.mp4"}})
        clip = Clip(index=0, source="fal", prompt="p", bild_uri="../geheim.png")
        with self.assertRaises(ValueError) as fehler:
            TestFigurLandetImAufruf._erzeuge(self, clip, doppel)
        self.assertIn("Datenverzeichnis", str(fehler.exception))
        self.assertEqual(doppel.aufrufe, [])

    def test_absoluter_pfad_wird_abgelehnt(self):
        doppel = FalDoppel({"video": {"url": "https://fal.example/v.mp4"}})
        clip = Clip(index=0, source="fal", prompt="p", bild_uri="/etc/passwd")
        with self.assertRaises(ValueError):
            TestFigurLandetImAufruf._erzeuge(self, clip, doppel)
        self.assertEqual(doppel.aufrufe, [])


class TestBildUploadNurEinmal(unittest.TestCase):
    def test_dasselbe_bild_wird_einmal_hochgeladen(self):
        d = Path(tempfile.mkdtemp())
        bild = d / "m.png"
        bild.write_bytes(b"\x89PNG" + b"M" * 32)
        doppel = FalDoppel({"video": {"url": "https://fal.example/v.mp4"}})
        generator = Generator(cache_dir=d / "cache", client=doppel)

        erste = generator.bild_url(bild)
        zweite = generator.bild_url(bild)

        self.assertEqual(erste, zweite)
        self.assertEqual(
            len(doppel.uploads),
            1,
            "acht Einstellungen mit derselben Figur duerfen nicht acht Uploads kosten",
        )

    def test_fehlendes_bild_meldet_den_pfad(self):
        d = Path(tempfile.mkdtemp())
        doppel = FalDoppel({})
        generator = Generator(cache_dir=d / "cache", client=doppel)
        with self.assertRaises(Exception) as fehler:
            generator.bild_url(d / "gibtsnicht.png")
        self.assertIn("gibtsnicht.png", str(fehler.exception))


if __name__ == "__main__":
    unittest.main()
