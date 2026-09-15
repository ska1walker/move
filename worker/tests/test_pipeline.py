"""Ende-zu-Ende: Platzhalter erzeugen, rendern, Ergebnis messen.

Braucht ffmpeg und ffprobe im PATH und wird sonst uebersprungen.

Die Schnittgrenzen werden mit einfarbigen Clips geprueft, nicht mit den
Text-Platzhaltern: eine Farbe laesst sich an einer Bildnummer auslesen und
vergleichen, ein gezeichneter Index nicht. Der Text-Platzhalter wird separat
auf seine Bildzahl geprueft.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from move_worker import ffmpeg
from move_worker.assembler import AssemblyError, FramePlan, RenderSettings, assemble
from move_worker.placeholders import create_for_template
from move_worker.templates import CutTemplate

HAT_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))

BREITE, HOEHE, FPS = 160, 90, 25

# Acht deutlich verschiedene Farben, eine je Einstellung.
FARBEN = [
    ("red", (255, 0, 0)),
    ("lime", (0, 255, 0)),
    ("blue", (0, 0, 255)),
    ("yellow", (255, 255, 0)),
    ("magenta", (255, 0, 255)),
    ("cyan", (0, 255, 255)),
    ("white", (255, 255, 255)),
    ("gray", (128, 128, 128)),
]

TEMPLATE = {
    "id": "beat-8s",
    "name": "Beat 8s",
    "duration_ms": 8000,
    "shot_count": 8,
    "cuts": [
        {"at_ms": 0, "transition": "cut"},
        {"at_ms": 2000, "transition": "crossfade", "transition_ms": 400},
        {"at_ms": 3500, "transition": "crossfade", "transition_ms": 300},
        {"at_ms": 4500, "transition": "cut"},
        {"at_ms": 5000, "transition": "cut"},
        {"at_ms": 5500, "transition": "cut"},
        {"at_ms": 6000, "transition": "cut"},
        {"at_ms": 6500, "transition": "crossfade", "transition_ms": 500},
    ],
}


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


def bildfarbe(video: Path, n: int, arbeitsordner: Path) -> tuple[int, int, int]:
    """Mittlere Farbe von Bild `n`.

    Das ganze Bild wird flaechengewichtet auf einen Pixel gemittelt. Bei den
    einfarbigen Testclips ist das genau ihre Farbe, waehrend einer Blende
    genau die Mischung -- und robuster als ein einzelner Bildpunkt.

    Der Umweg ueber eine Datei ist Absicht: rohe Bilddaten ueber stdout zu
    holen wuerde an der Textdekodierung scheitern.
    """
    roh = arbeitsordner / f"pixel-{n}.raw"
    ffmpeg.run(
        [
            "-hide_banner", "-nostdin", "-y", "-v", "error",
            "-i", str(video),
            "-vf", f"select=eq(n\\,{n}),scale=w=1:h=1:flags=area",
            "-frames:v", "1",
            "-f", "rawvideo", "-pix_fmt", "rgb24",
            str(roh),
        ]
    )
    b = roh.read_bytes()
    return (b[0], b[1], b[2])


def nahe(ist: tuple[int, int, int], soll: tuple[int, int, int], toleranz: int = 20) -> bool:
    return all(abs(a - b) <= toleranz for a, b in zip(ist, soll))


def bildzahl(video: Path) -> int:
    result = ffmpeg.probe(
        [
            "-v", "error",
            "-count_frames", "-select_streams", "v:0",
            "-show_entries", "stream=nb_read_frames",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(video),
        ]
    )
    return int(result.stdout.strip())


@unittest.skipUnless(HAT_FFMPEG, "ffmpeg oder ffprobe fehlt im PATH")
class TestSchnittgrenzen(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        ordner = Path(cls.tmp.name)
        cls.template = CutTemplate.from_json(TEMPLATE)
        cls.settings = RenderSettings(width=BREITE, height=HOEHE, fps=FPS, preset="ultrafast")
        cls.plan = FramePlan.build(cls.template, FPS)

        cls.clips = []
        for i, (name, _) in enumerate(FARBEN):
            ziel = ordner / f"clip-{i:02d}.mp4"
            einfarbig(ziel, name, cls.plan.source[i])
            cls.clips.append(ziel)

        cls.ausgabe = ordner / "out.mp4"
        assemble(cls.template, list(cls.clips), cls.ausgabe, cls.settings)
        cls.ordner = ordner

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_bildzahl_entspricht_dem_plan(self):
        self.assertEqual(bildzahl(self.ausgabe), self.plan.total)
        self.assertEqual(self.plan.total, 200)

    def test_laufzeit(self):
        self.assertEqual(ffmpeg.duration_ms(str(self.ausgabe)), 8000)

    def test_clips_haben_genau_die_geplanten_bilder(self):
        for i, clip in enumerate(self.clips):
            self.assertEqual(bildzahl(clip), self.plan.source[i], f"Clip {i}")

    def test_harter_schnitt_liegt_auf_dem_geplanten_bild(self):
        """Bild 112 ist noch Einstellung 2, Bild 113 schon Einstellung 3."""
        vorher = bildfarbe(self.ausgabe, 112, self.ordner)
        nachher = bildfarbe(self.ausgabe, 113, self.ordner)
        self.assertTrue(nahe(vorher, FARBEN[2][1]), f"Bild 112 ist {vorher}")
        self.assertTrue(nahe(nachher, FARBEN[3][1]), f"Bild 113 ist {nachher}")
        self.assertEqual(self.plan.start[3], 113)

    def test_jede_einstellung_ist_in_ihrer_mitte_zu_sehen(self):
        for i in range(len(FARBEN)):
            start = self.plan.start[i] + self.plan.fade[i]
            ende = (
                self.plan.start[i + 1] if i + 1 < len(FARBEN) else self.plan.total
            )
            mitte = (start + ende) // 2
            ist = bildfarbe(self.ausgabe, mitte, self.ordner)
            self.assertTrue(
                nahe(ist, FARBEN[i][1]),
                f"Einstellung {i} bei Bild {mitte}: {ist}, erwartet {FARBEN[i][1]}",
            )

    def test_blende_endet_auf_dem_geplanten_bild(self):
        """Ab start[1] + fade[1] ist Einstellung 1 allein zu sehen."""
        ende = self.plan.start[1] + self.plan.fade[1]
        self.assertEqual(ende, 60)
        self.assertTrue(nahe(bildfarbe(self.ausgabe, ende, self.ordner), FARBEN[1][1]))
        # Ein Bild vor dem Ende mischt noch.
        mitte = bildfarbe(self.ausgabe, ende - 5, self.ordner)
        self.assertFalse(nahe(mitte, FARBEN[0][1]), f"Bild {ende - 5} ist reines Rot")
        self.assertFalse(nahe(mitte, FARBEN[1][1]), f"Bild {ende - 5} ist reines Gruen")

    def test_letztes_bild_vor_der_blende_ist_noch_unvermischt(self):
        self.assertTrue(
            nahe(bildfarbe(self.ausgabe, self.plan.start[1] - 1, self.ordner), FARBEN[0][1])
        )

    def test_gleiche_eingabe_ergibt_dieselbe_datei(self):
        zweite = self.ordner / "out2.mp4"
        assemble(self.template, list(self.clips), zweite, self.settings)
        self.assertEqual(
            self.ausgabe.read_bytes(), zweite.read_bytes(), "Render ist nicht reproduzierbar"
        )

    def test_zu_kurzer_clip_scheitert_mit_zahlen(self):
        kurz = self.ordner / "kurz.mp4"
        einfarbig(kurz, "red", 5)
        clips = [kurz] + list(self.clips[1:])
        with self.assertRaises(AssemblyError) as ctx:
            assemble(self.template, clips, self.ordner / "egal.mp4", self.settings)
        text = str(ctx.exception)
        self.assertIn("Clip 0", text)
        self.assertIn("fehlen", text)


@unittest.skipUnless(HAT_FFMPEG, "ffmpeg oder ffprobe fehlt im PATH")
class TestPlatzhalter(unittest.TestCase):
    def test_platzhalter_haben_die_geplanten_bilder_und_text(self):
        template = CutTemplate.from_json(TEMPLATE)
        settings = RenderSettings(width=320, height=180, fps=FPS, preset="ultrafast")
        plan = FramePlan.build(template, FPS)
        with tempfile.TemporaryDirectory() as d:
            # create_for_template scheitert von selbst, wenn drawtext den Text
            # nicht zeichnet -- ffmpeg endet in dem Fall mit Code 0.
            pfade = create_for_template(template, d, settings)
            self.assertEqual(len(pfade), 8)
            for i, p in enumerate(pfade):
                self.assertEqual(bildzahl(p), plan.source[i], f"Platzhalter {i}")


if __name__ == "__main__":
    unittest.main()
