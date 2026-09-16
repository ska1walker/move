"""Generierung bei fal.ai.

fal.ai und docs.fal.ai sind vom Egress-Proxy gesperrt, und ein Schluessel
liegt hier ohnehin nicht. Geprueft wird deshalb alles ausser dem Netzaufruf
selbst: die Zusammensetzung der Anfrage, der Zwischenspeicher, das Auslesen
der Antwort, der Download und das Verhalten der Job-Schleife.

Das fal-Doppel zeichnet auf, womit es gerufen wurde -- damit ist pruefbar,
dass ein zweiter Lauf NICHT noch einmal ruft. Genau daran haengt, dass ein
Pod-Neustart nicht doppelt abgerechnet wird.
"""

from __future__ import annotations

import http.server
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from move_worker.generierung import (
    DEFAULT_BILD_MODEL,
    DEFAULT_MODEL,
    Anfrage,
    FalFehler,
    Generator,
    _ohne_geheimnis,
    default_model,
    fal_key,
    herunterladen,
    modell_fuer,
    video_url_aus,
)


class FalDoppel:
    """Zeichnet Aufrufe auf und liefert eine vorgegebene Antwort."""

    def __init__(self, antwort, fehler: Exception | None = None):
        self.antwort = antwort
        self.fehler = fehler
        self.aufrufe: list[tuple[str, dict]] = []

    def subscribe(self, application, arguments, **kwargs):
        self.aufrufe.append((application, dict(arguments)))
        if self.fehler:
            raise self.fehler
        return self.antwort


class TestAnfrage(unittest.TestCase):
    def anfrage(self, **abweichung) -> Anfrage:
        basis = dict(
            prompt="a wide desert at dawn",
            sekunden=3.0,
            breite=1920,
            height=1080,
            model="fal-ai/testmodell",
        )
        basis.update(abweichung)
        return Anfrage(**basis)

    def test_argumente_enthalten_prompt_und_laenge(self):
        args = self.anfrage().argumente()
        self.assertEqual(args["prompt"], "a wide desert at dawn")
        self.assertEqual(args["duration"], 3)

    def test_dauer_ist_eine_ganze_zahl(self):
        """Nicht 3.0, sondern 3.

        assertEqual(3, 3.0) ist in Python wahr -- diese Zusicherung hier ist
        also der einzige Ort, an dem der TYP ueberhaupt geprueft wird. Er ist
        der Grund fuer diesen Test: in JSON wird aus 3.0 die Zahl "3.0", und
        ein Schema, das eine ganze Sekunde erwartet, lehnt sie ab.
        """
        args = self.anfrage().argumente()
        self.assertIsInstance(args["duration"], int)
        self.assertNotIsInstance(args["duration"], bool)

    def test_dauer_wird_aufgerundet_nie_ab(self):
        """Aufrunden ist hier kein Geschmack, sondern Geld.

        Der Assembler lehnt einen Clip ab, der kuerzer ist als seine
        Einstellung (check_clips). Ein abgerundeter Wert waere also bezahlt
        und unbrauchbar. 1.996 s ist der Fall, den `round(x, 2)` auf 2.0
        gebracht haette -- 4 ms zu wenig.
        """
        for sekunden, erwartet in ((1.996, 2), (2.0, 2), (2.001, 3), (0.2, 1), (8.0, 8)):
            with self.subTest(sekunden=sekunden):
                args = self.anfrage(sekunden=sekunden).argumente()
                self.assertEqual(args["duration"], erwartet)
                self.assertGreaterEqual(args["duration"], sekunden)

    def test_dauer_als_zeichenkette(self):
        """Fuer Modelle, die "8" wollen und nicht 8 -- gemessen bei seedance."""
        with mock.patch.dict(os.environ, {"MOVE_FAL_DAUER_TEXT": "1"}):
            args = self.anfrage().argumente()
        self.assertEqual(args["duration"], "3")

    def test_dauer_abschaltbar(self):
        """Modelle mit fester Laenge lehnen ein unbekanntes duration ab."""
        with mock.patch.dict(os.environ, {"MOVE_FAL_DAUER_AUS": "1"}):
            args = self.anfrage().argumente()
        self.assertNotIn("duration", args)
        self.assertEqual(args["prompt"], "a wide desert at dawn")

    def test_dauer_umbenennbar(self):
        with mock.patch.dict(os.environ, {"MOVE_FAL_DAUER_ARGUMENT": "num_seconds"}):
            args = self.anfrage().argumente()
        self.assertEqual(args["num_seconds"], 3)
        self.assertNotIn("duration", args)

    def test_extra_wird_durchgereicht(self):
        args = self.anfrage(extra=(("aspect_ratio", "16:9"),)).argumente()
        self.assertEqual(args["aspect_ratio"], "16:9")

    def test_schluessel_ist_stabil(self):
        self.assertEqual(self.anfrage().schluessel(), self.anfrage().schluessel())

    def test_schluessel_aendert_sich_mit_jedem_feld(self):
        basis = self.anfrage().schluessel()
        for abweichung in (
            {"prompt": "etwas anderes"},
            {"sekunden": 4.0},
            {"breite": 1280},
            {"height": 720},
            {"model": "fal-ai/anderes"},
            {"extra": (("seed", 7),)},
        ):
            self.assertNotEqual(
                basis, self.anfrage(**abweichung).schluessel(), f"gleich trotz {abweichung}"
            )


class TestAufrufform(unittest.TestCase):
    """`arguments` geht als Schluesselwort raus, nicht als zweites Argument.

    Das Doppel hier nimmt es NUR als Schluesselwort an. Damit schlaegt der
    Test fehl, wenn der Aufruf je wieder positionell wird -- was fuer sich
    genommen funktioniert, aber jede Verwechslung von Modell und Argumenten
    still durchlaesst.
    """

    class StrengesDoppel:
        def __init__(self):
            self.gesehen: list[dict] = []

        def subscribe(self, application, *, arguments, **kwargs):
            self.gesehen.append({"model": application, "args": dict(arguments), **kwargs})
            return {"video": {"url": "https://x/a.mp4"}}

        def upload_file(self, path):
            return f"https://fal.example/{Path(path).name}"

    def test_generator_ruft_mit_schluesselwort(self):
        doppel = self.StrengesDoppel()
        with tempfile.TemporaryDirectory() as tmp:
            generator = Generator(cache_dir=Path(tmp), client=doppel, timeout_s=42)
            anfrage = Anfrage(
                prompt="p", sekunden=2.0, breite=1280, height=720, model="fal-ai/x"
            )
            with mock.patch("move_worker.generierung.herunterladen", return_value=17):
                generator.erzeuge(anfrage)

        self.assertEqual(len(doppel.gesehen), 1)
        aufruf = doppel.gesehen[0]
        self.assertEqual(aufruf["model"], "fal-ai/x")
        self.assertEqual(aufruf["args"]["prompt"], "p")
        self.assertEqual(aufruf["client_timeout"], 42)


class TestUrlAuslesen(unittest.TestCase):
    """Verschiedene fal-Modelle legen das Ergebnis verschieden ab."""

    def test_verschachtelt(self):
        self.assertEqual(video_url_aus({"video": {"url": "https://x/a.mp4"}}), "https://x/a.mp4")

    def test_direkt(self):
        self.assertEqual(video_url_aus({"video": "https://x/b.mp4"}), "https://x/b.mp4")

    def test_liste(self):
        self.assertEqual(
            video_url_aus({"output": [{"url": "https://x/c.mp4"}]}), "https://x/c.mp4"
        )

    def test_url_auf_oberster_ebene(self):
        self.assertEqual(video_url_aus({"url": "https://x/d.mp4"}), "https://x/d.mp4")

    def test_nichts_gefunden_nennt_die_antwort(self):
        with self.assertRaises(FalFehler) as ctx:
            video_url_aus({"unerwartet": 1})
        self.assertIn("unerwartet", str(ctx.exception))

    def test_kein_file_schema(self):
        """Eine Antwort von aussen darf nicht auf das eigene Dateisystem zeigen."""
        with self.assertRaises(FalFehler):
            video_url_aus({"video": {"url": "file:///etc/passwd"}})


OHNE_SCHLUESSEL = {"FAL_KEY": "", "FAL_KEY_ID": "", "FAL_KEY_SECRET": ""}


class TestGeheimnis(unittest.TestCase):
    def test_schluessel_wird_aus_meldungen_geraeumt(self):
        with mock.patch.dict(os.environ, {"FAL_KEY": "geheim-123"}):
            self.assertNotIn("geheim-123", _ohne_geheimnis("Fehler mit geheim-123 drin"))
            self.assertIn("<FAL_KEY>", _ohne_geheimnis("Fehler mit geheim-123 drin"))

    def test_auch_das_paar_wird_geraeumt(self):
        """Seit fal_key das Paar akzeptiert, muss es hier mitgehen.

        Sonst hat die Funktion eine Luecke genau in dem Pfad, den sie
        absichert: die Meldung landet in render_job.error und damit in der
        Oberflaeche.
        """
        with mock.patch.dict(
            os.environ, {"FAL_KEY": "", "FAL_KEY_ID": "kennung", "FAL_KEY_SECRET": "sehr-geheim"}
        ):
            geraeumt = _ohne_geheimnis("401 fuer kennung:sehr-geheim")
        self.assertNotIn("sehr-geheim", geraeumt)
        self.assertIn("<FAL_KEY_SECRET>", geraeumt)

    def test_fehlender_schluessel_sagt_wo_er_hingehoert(self):
        with mock.patch.dict(os.environ, OHNE_SCHLUESSEL):
            with self.assertRaises(FalFehler) as ctx:
                fal_key()
        self.assertIn("olaresEnv", str(ctx.exception))

    def test_paar_zaehlt_als_schluessel(self):
        """fal-client akzeptiert FAL_KEY_ID + FAL_KEY_SECRET (gemessen in
        _resolve_env_or_colab_auth). Wer das gesetzt hat, darf hier nicht
        "kein Schluessel" lesen."""
        with mock.patch.dict(
            os.environ, {"FAL_KEY": "", "FAL_KEY_ID": "kennung", "FAL_KEY_SECRET": "geheim"}
        ):
            self.assertEqual(fal_key(), "kennung:geheim")

    def test_halbes_paar_zaehlt_nicht(self):
        with mock.patch.dict(
            os.environ, {"FAL_KEY": "", "FAL_KEY_ID": "kennung", "FAL_KEY_SECRET": ""}
        ):
            with self.assertRaises(FalFehler):
                fal_key()

    def test_modell_aus_der_umgebung(self):
        with mock.patch.dict(os.environ, {"MOVE_FAL_MODEL": "fal-ai/eigen"}):
            self.assertEqual(default_model(), "fal-ai/eigen")


class TestModellwahl(unittest.TestCase):
    """Text- und Bildpfad brauchen verschiedene Endpunkte.

    fal-ai/ltx-video existiert, ist aber TEXT-zu-Video und kennt kein
    image_url. Eine Figur mit Referenzbild an diesem Endpunkt waere bezahlt
    und wirkungslos -- das ist der Fehler, den diese vier Tests festnageln.
    """

    def test_ohne_bild_das_textmodell(self):
        with mock.patch.dict(os.environ, {"MOVE_FAL_MODEL": "", "MOVE_FAL_BILD_MODEL": ""}):
            self.assertEqual(modell_fuer(False), DEFAULT_MODEL)

    def test_mit_bild_das_bildmodell(self):
        with mock.patch.dict(os.environ, {"MOVE_FAL_MODEL": "", "MOVE_FAL_BILD_MODEL": ""}):
            self.assertEqual(modell_fuer(True), DEFAULT_BILD_MODEL)
            self.assertNotEqual(DEFAULT_BILD_MODEL, DEFAULT_MODEL)

    def test_move_fal_model_greift_nicht_in_den_bildpfad(self):
        """Absichtlich. Sonst entwertet ein Textmodell in MOVE_FAL_MODEL
        still jede Figur mit Referenzbild."""
        with mock.patch.dict(
            os.environ, {"MOVE_FAL_MODEL": "fal-ai/nur-text", "MOVE_FAL_BILD_MODEL": ""}
        ):
            self.assertEqual(modell_fuer(False), "fal-ai/nur-text")
            self.assertEqual(modell_fuer(True), DEFAULT_BILD_MODEL)

    def test_bildmodell_aus_der_umgebung(self):
        with mock.patch.dict(os.environ, {"MOVE_FAL_BILD_MODEL": "fal-ai/eigenes-bild"}):
            self.assertEqual(modell_fuer(True), "fal-ai/eigenes-bild")


class TestClientSignatur(unittest.TestCase):
    """Gegen den echten fal-client, wenn er installiert ist.

    Das ist der einzige Test hier, der etwas ueber die Bibliothek selbst
    behauptet, und er prueft genau die drei Dinge, auf die der Aufruf in
    `Generator.erzeuge` baut. Ohne installierten fal-client uebersprungen --
    der Kern des Workers laeuft ohne ihn, genau wie ohne librosa.
    """

    def setUp(self):
        try:
            import fal_client  # noqa: PLC0415
        except ImportError:
            self.skipTest("fal-client ist nicht installiert")
        self.fal_client = fal_client

    def test_arguments_ist_als_schluesselwort_erlaubt(self):
        import inspect  # noqa: PLC0415

        parameter = inspect.signature(self.fal_client.subscribe).parameters
        self.assertIn("arguments", parameter)
        self.assertEqual(
            parameter["arguments"].kind, inspect.Parameter.POSITIONAL_OR_KEYWORD
        )

    def test_client_timeout_existiert_und_ist_schluesselwort(self):
        import inspect  # noqa: PLC0415

        parameter = inspect.signature(self.fal_client.subscribe).parameters
        self.assertIn("client_timeout", parameter)
        self.assertEqual(parameter["client_timeout"].kind, inspect.Parameter.KEYWORD_ONLY)

    def test_import_braucht_keinen_schluessel(self):
        """Der Client holt seine Anmeldedaten lazy (cached_property _auth).

        Wichtig, weil FAL_KEY im Manifest optional ist: eine Installation
        ohne Schluessel darf nicht am Import scheitern.
        """
        with mock.patch.dict(os.environ, OHNE_SCHLUESSEL):
            self.assertTrue(hasattr(self.fal_client, "subscribe"))
            self.assertTrue(hasattr(self.fal_client, "upload_file"))


class TestDownload(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ordner = Path(self.tmp.name)
        (self.ordner / "quelle.mp4").write_bytes(b"x" * 4096)

        klasse = http.server.SimpleHTTPRequestHandler
        ordner = str(self.ordner)

        class Handler(klasse):
            def __init__(self, *a, **k):
                super().__init__(*a, directory=ordner, **k)

            def log_message(self, *a):
                pass

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.tmp.cleanup()

    def test_laedt_und_benennt_um(self):
        ziel = self.ordner / "ziel.mp4"
        bytes_gesamt = herunterladen(f"http://127.0.0.1:{self.port}/quelle.mp4", ziel)
        self.assertEqual(bytes_gesamt, 4096)
        self.assertTrue(ziel.is_file())
        self.assertFalse(ziel.with_suffix(".mp4.teil").exists())

    def test_fehlschlag_laesst_keine_halbe_datei(self):
        ziel = self.ordner / "fehlt.mp4"
        with self.assertRaises(FalFehler):
            herunterladen(f"http://127.0.0.1:{self.port}/gibtsnicht.mp4", ziel)
        self.assertFalse(ziel.exists())
        self.assertFalse(ziel.with_suffix(".mp4.teil").exists())


class TestGenerator(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ordner = Path(self.tmp.name)
        (self.ordner / "erzeugt.mp4").write_bytes(b"v" * 2048)

        ordner = str(self.ordner)

        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *a, **k):
                super().__init__(*a, directory=ordner, **k)

            def log_message(self, *a):
                pass

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

        self.antwort = {"video": {"url": f"http://127.0.0.1:{self.port}/erzeugt.mp4"}}
        self.anfrage = Anfrage(
            prompt="a wide desert at dawn",
            sekunden=3.0,
            breite=640,
            height=360,
            model="fal-ai/testmodell",
        )

    def tearDown(self):
        self.server.shutdown()
        self.tmp.cleanup()

    def generator(self, doppel) -> Generator:
        return Generator(cache_dir=self.ordner / "cache", client=doppel)

    def test_erzeugt_und_legt_ab(self):
        doppel = FalDoppel(self.antwort)
        pfad = self.generator(doppel).erzeuge(self.anfrage)
        self.assertTrue(pfad.is_file())
        self.assertEqual(pfad.read_bytes(), b"v" * 2048)
        self.assertEqual(len(doppel.aufrufe), 1)
        self.assertEqual(doppel.aufrufe[0][0], "fal-ai/testmodell")
        self.assertEqual(doppel.aufrufe[0][1]["prompt"], "a wide desert at dawn")

    def test_zweiter_lauf_ruft_fal_NICHT_erneut(self):
        """Der Punkt, an dem Geld haengt.

        reset_stale_running legt einen Job nach einem Pod-Neustart zurueck in
        die Warteschlange. Ohne Zwischenspeicher wuerde jede Einstellung neu
        erzeugt -- und neu abgerechnet.
        """
        doppel = FalDoppel(self.antwort)
        gen = self.generator(doppel)
        erst = gen.erzeuge(self.anfrage)
        zweit = gen.erzeuge(self.anfrage)
        self.assertEqual(erst, zweit)
        self.assertEqual(len(doppel.aufrufe), 1, "fal wurde ein zweites Mal gerufen")

    def test_andere_anfrage_ruft_erneut(self):
        doppel = FalDoppel(self.antwort)
        gen = self.generator(doppel)
        gen.erzeuge(self.anfrage)
        from dataclasses import replace

        gen.erzeuge(replace(self.anfrage, prompt="etwas ganz anderes"))
        self.assertEqual(len(doppel.aufrufe), 2)

    def test_fehler_von_fal_wird_zu_FalFehler(self):
        doppel = FalDoppel(None, fehler=RuntimeError("upstream kaputt"))
        with self.assertRaises(FalFehler) as ctx:
            self.generator(doppel).erzeuge(self.anfrage)
        self.assertIn("upstream kaputt", str(ctx.exception))

    def test_schluessel_steht_nicht_in_der_fehlermeldung(self):
        with mock.patch.dict(os.environ, {"FAL_KEY": "geheim-abc"}):
            doppel = FalDoppel(None, fehler=RuntimeError("401 fuer geheim-abc"))
            with self.assertRaises(FalFehler) as ctx:
                self.generator(doppel).erzeuge(self.anfrage)
        self.assertNotIn("geheim-abc", str(ctx.exception))
        self.assertIn("<FAL_KEY>", str(ctx.exception))

    def test_leere_antwort_scheitert(self):
        doppel = FalDoppel({"kein_video": True})
        with self.assertRaises(FalFehler):
            self.generator(doppel).erzeuge(self.anfrage)
        self.assertFalse(self.generator(doppel).pfad_fuer(self.anfrage).exists())


if __name__ == "__main__":
    unittest.main()


class TestFingerabdruck(unittest.TestCase):
    """Nach docs/olares-learnings.md 13: erkennbar, aber nicht verraetrisch."""

    def test_form_wie_die_anbieter_sie_verwenden(self):
        from move_worker.generierung import fingerabdruck

        # 16 Zeichen oder mehr: Anfang 6, Ende 4.
        self.assertEqual(fingerabdruck("abcdefghijklmnopqrst"), "abcdef...qrst")

    def test_verraet_die_mitte_nicht(self):
        from move_worker.generierung import fingerabdruck

        schluessel = "fal-geheimnisvollundlang-ENDE"
        abdruck = fingerabdruck(schluessel)
        self.assertNotIn("geheimnisvoll", abdruck)
        self.assertLess(len(abdruck), len(schluessel))

    def test_kurz_zeigt_nur_die_laenge(self):
        from move_worker.generierung import fingerabdruck

        self.assertEqual(fingerabdruck("kurz"), "(4 Zeichen)")

    def test_leer_bleibt_erkennbar_leer(self):
        from move_worker.generierung import fingerabdruck

        # "kein Schluessel" und "falscher Schluessel" muessen sich
        # unterscheiden lassen -- das ist der ganze Zweck.
        self.assertEqual(fingerabdruck("   "), "(leer)")
