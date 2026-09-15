import unittest

from move_worker.assembler import (
    AssemblyError,
    FramePlan,
    RenderSettings,
    build_args,
    build_filter_graph,
    ms_to_frames,
)
from move_worker.placeholders import escape
from move_worker.templates import CutTemplate

BEISPIEL = {
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


class TestMsToFrames(unittest.TestCase):
    def test_glatt(self):
        self.assertEqual(ms_to_frames(1000, 25), 25)
        self.assertEqual(ms_to_frames(0, 25), 0)

    def test_halbe_bilder_gehen_aufwaerts(self):
        # 500 ms sind bei 25 fps 12,5 Bilder. Pythons round() liefert hier 12,
        # weil es zur geraden Zahl rundet -- das waere von der Nachbarschaft
        # abhaengig und ist fuer einen Schnittplan unbrauchbar.
        self.assertEqual(ms_to_frames(500, 25), 13)
        self.assertEqual(ms_to_frames(540, 25), 14)  # 13,5 -> 14
        self.assertEqual(round(12.5), 12)  # das, was wir nicht wollen

    def test_negative_zeit(self):
        with self.assertRaises(AssemblyError):
            ms_to_frames(-1, 25)

    def test_bildrate_null(self):
        with self.assertRaises(AssemblyError):
            ms_to_frames(1000, 0)


class TestFramePlan(unittest.TestCase):
    def setUp(self):
        self.template = CutTemplate.from_json(BEISPIEL)

    def test_gemessener_plan_bei_25fps(self):
        plan = FramePlan.build(self.template, 25)
        self.assertEqual(plan.total, 200)
        self.assertEqual(plan.start, (0, 50, 88, 113, 125, 138, 150, 163))
        self.assertEqual(plan.visible, (50, 38, 25, 12, 13, 12, 13, 37))
        self.assertEqual(plan.fade, (0, 10, 8, 0, 0, 0, 0, 13))
        self.assertEqual(plan.source, (60, 46, 25, 12, 13, 12, 26, 37))

    def test_sichtbare_bilder_summieren_immer_auf_total(self):
        """Die entscheidende Eigenschaft: die Rundung darf sich nicht aufsummieren."""
        for fps in (12, 24, 25, 30, 48, 50, 60):
            plan = FramePlan.build(self.template, fps)
            self.assertEqual(
                sum(plan.visible), plan.total, f"Summe weicht ab bei {fps} fps"
            )

    def test_stromlaenge_passt_zum_naechsten_xfade(self):
        """Nach Einstellung i muss der Strom start[i+1] + fade[i+1] lang sein.

        Sonst hat xfade am Blendenende kein Material mehr -- der Grund, warum
        source[] ueberhaupt laenger ist als visible[].
        """
        for fps in (24, 25, 30, 50):
            plan = FramePlan.build(self.template, fps)
            for i in range(len(plan.start) - 1):
                self.assertEqual(
                    plan.start[i] + plan.source[i],
                    plan.start[i + 1] + plan.fade[i + 1],
                    f"Bruch bei Einstellung {i}, {fps} fps",
                )
            letzte = len(plan.start) - 1
            self.assertEqual(plan.start[letzte] + plan.source[letzte], plan.total)

    def test_zu_feiner_schnitt_scheitert_mit_bildrate_im_text(self):
        # Zwei Schnitte 10 ms auseinander fallen bei 25 fps auf dasselbe Bild.
        roh = dict(BEISPIEL)
        roh["duration_ms"] = 4000
        roh["shot_count"] = 3
        roh["cuts"] = [
            {"at_ms": 0, "transition": "cut"},
            {"at_ms": 1000, "transition": "cut"},
            {"at_ms": 1010, "transition": "cut"},
        ]
        template = CutTemplate.from_json(roh)
        with self.assertRaises(AssemblyError) as ctx:
            FramePlan.build(template, 25)
        self.assertIn("25 fps", str(ctx.exception))

    def test_blende_unter_einem_bild_wird_ein_bild(self):
        roh = {
            "id": "x", "name": "x", "duration_ms": 4000, "shot_count": 2,
            "cuts": [
                {"at_ms": 0, "transition": "cut"},
                {"at_ms": 2000, "transition": "crossfade", "transition_ms": 5},
            ],
        }
        plan = FramePlan.build(CutTemplate.from_json(roh), 25)
        self.assertEqual(plan.fade[1], 1)

    def test_source_ms_rundet_auf(self):
        plan = FramePlan.build(self.template, 30)
        for i, bilder in enumerate(plan.source):
            self.assertGreaterEqual(plan.source_ms(i) * 30, bilder * 1000)


class TestFilterGraph(unittest.TestCase):
    def setUp(self):
        self.template = CutTemplate.from_json(BEISPIEL)
        self.settings = RenderSettings(width=640, height=360, fps=25)
        self.plan = FramePlan.build(self.template, 25)
        self.graph, self.ergebnis = build_filter_graph(
            self.plan, self.template, self.settings
        )

    def test_ergebnislabel(self):
        self.assertEqual(self.ergebnis, "x7")

    def test_jede_einstellung_wird_auf_bilder_geschnitten(self):
        for i, bilder in enumerate(self.plan.source):
            self.assertIn(f"[{i}:v]fps=25,trim=end_frame={bilder},", self.graph)
            self.assertIn(f"settb=1/25,setpts=N[s{i}]", self.graph)

    def test_blenden_versatz_und_laenge(self):
        # Einstellung 1: Blende 10 Bilder ab Bild 50.
        self.assertIn("xfade=transition=fade:duration=0.400000:offset=2.000000", self.graph)
        # Einstellung 7: 13 Bilder ab Bild 163.
        self.assertIn("xfade=transition=fade:duration=0.520000:offset=6.520000", self.graph)

    def test_harte_schnitte_nutzen_concat(self):
        self.assertEqual(self.graph.count("concat=n=2:v=1:a=0"), 4)

    def test_zeitbasis_nach_jedem_schritt(self):
        """concat setzt die Zeitbasis auf 1/1000000, xfade bricht danach ab."""
        self.assertEqual(self.graph.count("settb=1/25"), 8 + 7)

    def test_jeder_schritt_wird_auf_seine_laenge_festgeschrieben(self):
        """Der Plan ist die Wahrheit, nicht die Buchhaltung des Filters.

        trim schreibt die Laenge jedes Zwischenstroms fest, setpts=N seine
        Zeitstempel. Beides zusammen macht den Schnitt unabhaengig davon, was
        der vorherige Filter hinterlassen hat -- siehe den Kommentar in
        assembler.build_filter_graph zur gemessenen Abweichung auf ffmpeg 5.1.
        """
        for i in range(1, len(self.plan.start)):
            laenge = self.plan.start[i] + self.plan.source[i]
            self.assertIn(f"trim=end_frame={laenge},settb=1/25,setpts=N[x{i}]", self.graph)

    def test_letzter_schritt_endet_auf_der_gesamtlaenge(self):
        letzter = len(self.plan.start) - 1
        self.assertIn(f"trim=end_frame={self.plan.total},settb=1/25,setpts=N[x{letzter}]", self.graph)


class TestBuildArgs(unittest.TestCase):
    def setUp(self):
        self.template = CutTemplate.from_json(BEISPIEL)
        self.clips = [f"/tmp/clip-{i}.mp4" for i in range(8)]

    def test_argumentliste_ohne_shell(self):
        args = build_args(self.template, self.clips, "/tmp/out.mp4")
        self.assertIsInstance(args, list)
        self.assertTrue(all(isinstance(a, str) for a in args))
        self.assertEqual(args.count("-i"), 8)
        self.assertEqual(args[-1], "/tmp/out.mp4")

    def test_bildzahl_wird_festgeschrieben(self):
        args = build_args(self.template, self.clips, "/tmp/out.mp4")
        self.assertEqual(args[args.index("-frames:v") + 1], "200")

    def test_reproduzierbar_kodiert(self):
        args = build_args(self.template, self.clips, "/tmp/out.mp4")
        self.assertIn("+bitexact", args)

    def test_falsche_clipzahl(self):
        with self.assertRaises(AssemblyError) as ctx:
            build_args(self.template, self.clips[:3], "/tmp/out.mp4")
        self.assertIn("8 Einstellungen", str(ctx.exception))


class TestEscape(unittest.TestCase):
    """Die Maskierung ist gegen ffmpeg 6.1.1 gemessen, nicht abgeleitet.

    Zwei Entpack-Durchgaenge: der Graph wird an ',' zerlegt, dann jeder Filter
    an ':'. Ein ':' braucht deshalb zwei Backslashes, ein ',' nur einen.
    """

    def test_doppelpunkt_zwei_backslashes(self):
        self.assertEqual(escape("%{pts:hms}"), "%{pts\\\\:hms}")

    def test_komma_ein_backslash(self):
        self.assertEqual(escape("A,B"), "A\\,B")

    def test_harmloser_text_bleibt(self):
        self.assertEqual(escape("CLIP 00"), "CLIP 00")


if __name__ == "__main__":
    unittest.main()
