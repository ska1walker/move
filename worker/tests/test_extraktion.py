"""Extraktion gegen Material mit bekannten Schnitten und bekanntem Takt.

Die ffmpeg-Tests ueberspringen sich ohne ffmpeg, die Beat-Tests zusaetzlich
ohne librosa -- der Kern des Workers braucht librosa nicht.
"""

from __future__ import annotations

import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path

from move_worker import ffmpeg
from move_worker.assembler import FramePlan
from move_worker.extraktion import (
    ExtraktionsFehler,
    auf_beat,
    beat_grid,
    hat_tonspur,
    schnittzeitpunkte,
    template_aus_video,
)
from move_worker.templates import BeatGrid

HAT_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
HAT_LIBROSA = importlib.util.find_spec("librosa") is not None

BREITE, HOEHE, FPS = 320, 180, 25

# Fuenf Einstellungen, Laengen in Bildern. Bei 25 fps liegen die Schnitte
# damit auf 1000, 2520, 3240 und 5040 ms.
SZENEN = [("red", 25), ("lime", 38), ("blue", 18), ("yellow", 45), ("magenta", 49)]
ERWARTETE_SCHNITTE_MS = [1000, 2520, 3240, 5040]


def einfarbig(ziel: Path, farbe: str, frames: int) -> None:
    ffmpeg.run(
        [
            "-hide_banner", "-nostdin", "-y",
            "-f", "lavfi",
            "-i", f"color=c={farbe}:s={BREITE}x{HOEHE}:r={FPS}",
            "-frames:v", str(frames),
            "-an",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12",
            "-pix_fmt", "yuv420p",
            str(ziel),
        ]
    )


def szenenvideo(ordner: Path, mit_ton: bool = False) -> Path:
    """Video mit genau den Schnitten aus SZENEN."""
    teile = []
    for farbe, frames in SZENEN:
        p = ordner / f"teil-{farbe}.mp4"
        einfarbig(p, farbe, frames)
        teile.append(p)

    liste = ordner / "liste.txt"
    liste.write_text("".join(f"file '{p}'\n" for p in teile), encoding="utf-8")

    stumm = ordner / "stumm.mp4"
    ffmpeg.run(
        [
            "-hide_banner", "-nostdin", "-y",
            "-f", "concat", "-safe", "0", "-i", str(liste),
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12",
            "-pix_fmt", "yuv420p", "-r", str(FPS),
            str(stumm),
        ]
    )
    if not mit_ton:
        return stumm

    # Klick alle 500 ms -- exakt 120 bpm.
    klick = ordner / "klick.wav"
    ffmpeg.run(
        [
            "-hide_banner", "-nostdin", "-y",
            "-f", "lavfi",
            "-i", "aevalsrc='0.9*sin(2*PI*1000*t)*lt(mod(t\\,0.5),0.03)':s=22050:d=20",
            "-c:a", "pcm_s16le",
            str(klick),
        ]
    )
    mit = ordner / "mitton.mp4"
    ffmpeg.run(
        [
            "-hide_banner", "-nostdin", "-y",
            "-i", str(stumm), "-i", str(klick),
            "-c:v", "copy", "-c:a", "aac", "-shortest",
            str(mit),
        ]
    )
    return mit


class TestAufBeat(unittest.TestCase):
    """Reine Arithmetik, braucht kein ffmpeg."""

    def setUp(self):
        self.grid = BeatGrid(bpm=120.0, offsets_ms=(0, 500, 1000, 1500, 2000))

    def test_zieht_auf_den_naechsten(self):
        self.assertEqual(auf_beat(1020, self.grid, 60), 1000)
        self.assertEqual(auf_beat(1470, self.grid, 60), 1500)

    def test_laesst_liegen_wenn_zu_weit(self):
        self.assertEqual(auf_beat(1250, self.grid, 60), 1250)

    def test_genau_auf_der_toleranz_zieht_noch(self):
        self.assertEqual(auf_beat(1060, self.grid, 60), 1000)

    def test_leeres_grid(self):
        self.assertEqual(auf_beat(1234, BeatGrid(bpm=0.0, offsets_ms=()), 60), 1234)


@unittest.skipUnless(HAT_FFMPEG, "ffmpeg oder ffprobe fehlt im PATH")
class TestSchnittzeitpunkte(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.ordner = Path(cls.tmp.name)
        cls.video = szenenvideo(cls.ordner)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_findet_genau_die_bekannten_schnitte(self):
        gefunden = [e.at_ms for e in schnittzeitpunkte(self.video)]
        self.assertEqual(gefunden, ERWARTETE_SCHNITTE_MS)

    def test_werte_an_den_schnitten_liegen_weit_ueber_der_schwelle(self):
        """Der Abstand zwischen Schnitt und Nicht-Schnitt traegt die Schwelle."""
        erkennungen = schnittzeitpunkte(self.video, schwelle=1.0)
        self.assertTrue(all(e.score > 20 for e in erkennungen), [e.score for e in erkennungen])

    def test_min_shot_ms_verwirft_zu_dichte(self):
        # Die kuerzeste echte Einstellung ist 720 ms (2520 -> 3240).
        gefunden = [e.at_ms for e in schnittzeitpunkte(self.video, min_shot_ms=1000)]
        self.assertNotIn(3240, gefunden)
        self.assertIn(2520, gefunden)

    def test_hohe_schwelle_findet_nichts(self):
        self.assertEqual(schnittzeitpunkte(self.video, schwelle=1000.0), [])

    def test_datei_ohne_bildspur(self):
        nur_ton = self.ordner / "nurton.wav"
        ffmpeg.run(
            [
                "-hide_banner", "-nostdin", "-y",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                str(nur_ton),
            ]
        )
        with self.assertRaises(ExtraktionsFehler):
            schnittzeitpunkte(nur_ton)


@unittest.skipUnless(HAT_FFMPEG, "ffmpeg oder ffprobe fehlt im PATH")
class TestTonspur(unittest.TestCase):
    def test_erkennt_fehlende_und_vorhandene_tonspur(self):
        with tempfile.TemporaryDirectory() as d:
            ordner = Path(d)
            stumm = szenenvideo(ordner, mit_ton=False)
            self.assertFalse(hat_tonspur(stumm))

    def test_ohne_tonspur_kein_beat_grid(self):
        with tempfile.TemporaryDirectory() as d:
            stumm = szenenvideo(Path(d), mit_ton=False)
            self.assertIsNone(beat_grid(stumm))


@unittest.skipUnless(HAT_FFMPEG and HAT_LIBROSA, "ffmpeg oder librosa fehlt")
class TestBeatGrid(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.video = szenenvideo(Path(cls.tmp.name), mit_ton=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_findet_den_bekannten_takt(self):
        """Klick alle 500 ms, also exakt 120 bpm.

        Eine halbe Schlagzahl Toleranz. librosas eigene Tempo-Ausgabe lag bei
        117,5 und der Median der Abstaende bei 117,6 -- deshalb rechnet das
        Modul mit dem Mittel.
        """
        grid = beat_grid(self.video)
        self.assertIsNotNone(grid)
        self.assertAlmostEqual(grid.bpm, 120.0, delta=0.5)

    def test_beats_liegen_gleichmaessig(self):
        grid = beat_grid(self.video)
        abstaende = [b - a for a, b in zip(grid.offsets_ms, grid.offsets_ms[1:])]
        self.assertTrue(abstaende)
        for a in abstaende:
            self.assertAlmostEqual(a, 500, delta=40)

    def test_zeitstempel_sind_ganzzahlig(self):
        grid = beat_grid(self.video)
        self.assertTrue(all(isinstance(b, int) for b in grid.offsets_ms))


@unittest.skipUnless(HAT_FFMPEG, "ffmpeg oder ffprobe fehlt im PATH")
class TestTemplateAusVideo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.ordner = Path(cls.tmp.name)
        cls.video = szenenvideo(cls.ordner)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_gibt_ein_gueltiges_template(self):
        vorlage = template_aus_video(self.video, name="Test")
        vorlage.validate()
        self.assertEqual(vorlage.shot_count, len(SZENEN))
        self.assertEqual(vorlage.cuts[0].at_ms, 0)
        self.assertEqual(
            [c.at_ms for c in vorlage.cuts], [0] + ERWARTETE_SCHNITTE_MS
        )

    def test_laufzeit_und_summe_passen(self):
        vorlage = template_aus_video(self.video)
        self.assertEqual(sum(vorlage.shot_durations_ms), vorlage.duration_ms)

    def test_die_einstellungen_haben_die_laengen_der_quelle(self):
        """Der Plan muss die Bildzahlen ergeben, aus denen das Video gebaut ist."""
        vorlage = template_aus_video(self.video)
        plan = FramePlan.build(vorlage, FPS)
        self.assertEqual(list(plan.visible), [n for _, n in SZENEN])

    def test_uebergangsart_und_bildgroesse_sind_nicht_geraten(self):
        """scdet liefert beides nicht -- es stehen Vorgaben da, keine Schaetzung."""
        vorlage = template_aus_video(self.video)
        self.assertTrue(all(c.transition == "cut" for c in vorlage.cuts))
        self.assertTrue(all(c.shot_scale == "medium" for c in vorlage.cuts))

    def test_fehlende_datei(self):
        with self.assertRaises(ExtraktionsFehler):
            template_aus_video(self.ordner / "gibtsnicht.mp4")

    def test_ohne_schnitte_bleibt_eine_einstellung(self):
        eine = self.ordner / "eine.mp4"
        einfarbig(eine, "red", 50)
        vorlage = template_aus_video(eine)
        self.assertEqual(vorlage.shot_count, 1)
        self.assertEqual(vorlage.cuts[0].at_ms, 0)


if __name__ == "__main__":
    unittest.main()
