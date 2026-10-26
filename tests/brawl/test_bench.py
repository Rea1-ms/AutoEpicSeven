import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from module.config import server

server.set_lang("global_cn")

from module.ocr.ocr import Ocr  # noqa: E402
from tasks.brawl.assets.assets_brawl import BENCH_SMALL_IMPRINT_C  # noqa: E402
from tasks.brawl.bench import (  # noqa: E402
    BenchEntry,
    BenchHero,
    BenchViewport,
    merge_bench_views,
    read_bench,
)
from tasks.brawl.name_cache import BenchNameCache, bench_name_cache  # noqa: E402
from tasks.brawl.placement import _selected_entry  # noqa: E402
from tasks.brawl.rules import HEROES, Imprint  # noqa: E402
from tasks.brawl.vision import (  # noqa: E402
    BrawlScreen,
    BrawlVision,
    ObservationError,
    PreparationPanel,
)
from tasks.brawl.withdraw import _bench_agrees  # noqa: E402
from tests.support.brawl import frame  # noqa: E402

PANEL = PreparationPanel(7, 4, 5, 5, 145, 0)


def viewport(*heroes, panel=PANEL):
    return BenchViewport(tuple(BenchEntry(name, imprint, (1000, 250, 1120, 280))
                               for name, imprint in heroes), panel)


class BenchRecognitionTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")

    def test_static_names_are_recognized_once_while_badges_and_controls_stay_current(self):
        self.frame = frame("normal-deployed")
        observation = BrawlVision(self.frame).observe()
        original = Ocr.ocr_multi_lines
        name_reads = []

        def recognize(ocr, images):
            if ocr.name == "BRAWL_BENCH_NAMES":
                name_reads.append(len(images))
            return original(ocr, images)

        with bench_name_cache(), patch.object(Ocr, "ocr_multi_lines", recognize):
            first = read_bench(self.frame, observation=observation)
            changed = self.frame.copy()
            changed[300:600, :900] = 0  # Unrelated animation must not invalidate a name.
            changed[286:323, 997:1049] = 0
            changed[250:322, 925:987] = 0
            after = read_bench(changed, observation=observation)
            repeated = read_bench(self.frame.copy(), observation=observation)
        self.assertEqual(name_reads, [1])
        self.assertEqual((first.entries[0].name, after.entries[0].name, repeated.entries[0].name),
                         ("卡萝", "卡萝", "卡萝"))
        self.assertIsNone(after.entries[0].imprint)
        self.assertIsNone(after.entries[0].plus_area)
        self.assertNotEqual(after.entries[0].name_area, first.entries[0].name_area)
        self.assertEqual(repeated.entries, first.entries)

    def test_name_cache_keeps_new_names_and_other_fonts_separate_and_ends_with_the_run(self):
        self.frame = frame("normal-deployed")
        original = Ocr.ocr_multi_lines
        name_reads = []
        candidate_reads = []

        def recognize(ocr, images):
            values = original(ocr, images)
            if ocr.name == "BRAWL_BENCH_NAMES":
                candidate_reads.append(len(images))
                name_reads.append(sum(score >= .8 and text in HEROES for text, score in values))
            return values

        with bench_name_cache() as cache, patch.object(Ocr, "ocr_multi_lines", recognize):
            first = read_bench(self.frame)
            changed = read_bench(frame("prepare-level4"))
            returned = read_bench(self.frame.copy())
            self.assertEqual(first.entries, returned.entries)
            self.assertEqual([entry.name for entry in changed.entries],
                             ["维多丽卡", "玫勒赛德丝", "萝季"])
            self.assertEqual(name_reads, [1, 3])
            # The current detector also proposes the artwork at (1072, 409,
            # 1169, 433). OCR rejects that fourth candidate with low confidence;
            # it must still be read rather than guessed away as a known badge.
            self.assertEqual(candidate_reads, [1, 4])
            sample = next(iter(cache.templates.values()))[0]
            # Learned selected-card fonts cannot identify a rear-card or a
            # result/transfer label, even if a synthetic crop is identical.
            self.assertIsNone(cache.match(np.repeat(sample[:, :, None], 3, axis=2), "hero_result"))
        with patch.object(Ocr, "detect_and_ocr", side_effect=RuntimeError("fresh run")), \
                self.assertRaisesRegex(RuntimeError, "fresh run"):
            read_bench(self.frame)
        with bench_name_cache(), patch.object(Ocr, "ocr_multi_lines", recognize):
            read_bench(self.frame)
        self.assertEqual(name_reads, [1, 3, 1])

    def test_cached_name_requires_full_glyphs_and_unknown_text_still_uses_ocr(self):
        self.frame = frame("normal-deployed")
        with bench_name_cache():
            read_bench(self.frame)
            changed = self.frame.copy()
            changed[248:284, 1023:1051] = 0  # Erase the last glyph, leaving a prefix.
            original = Ocr.ocr_multi_lines
            reads = []

            def reject_partial(ocr, images):
                if ocr.name == "BRAWL_BENCH_NAMES":
                    reads.append(len(images))
                    return [("未知", .99) for _ in images]
                return original(ocr, images)

            with patch.object(Ocr, "ocr_multi_lines", reject_partial):
                after = read_bench(changed)
            self.assertEqual(after.entries, ())
            self.assertEqual(reads, [1])

    def test_christy_static_template_never_learns_a_missing_final_glyph(self):
        self.frame = frame("queue-second")
        with bench_name_cache() as cache:
            observed = read_bench(self.frame)
            self.assertEqual(observed.entries[0].name, "克莉丝缇")
            self.assertNotIn(("selected", "克莉丝缇"), cache.templates)
            changed = self.frame.copy()
            changed[253:279, 1067:1090] = 0
            self.assertFalse(any(entry.name == "克莉丝缇" for entry in read_bench(changed).entries))

    def test_name_cache_is_bounded_and_ambiguous_templates_cannot_authorize_identity(self):
        self.frame = frame("normal-deployed")
        cache = BenchNameCache()
        cache.MAX_LABELS = 2
        region = self.frame[250:281, 1000:1048]
        cache.remember_region(region, "selected", "卡萝")
        self.assertEqual(cache.match(region, "selected")[0], "卡萝")
        cache.remember_region(region, "selected", "卡琳")
        self.assertIsNone(cache.match(region, "selected"))
        cache.remember_region(region, "small", "卡萝")
        self.assertEqual(len(cache.templates), 2)
        self.assertNotIn(("selected", "卡萝"), cache.templates)

    def test_cold_and_warm_name_cache_preserve_current_entries_across_supported_bench_layouts(self):
        # The plain Ocr reader does not merge boxes (both merge thresholds are
        # zero). Its recognized entries are the baseline for the cache's direct
        # detector path, including long names, rear-card sizes and clipped rows.
        samples = (
            "normal-deployed", "formation-place", "prepare-level4", "prepare-max", "transmit",
            "queue-top", "queue-second", "queue-middle", "queue-bottom", "queue-empty",
            "opening-lorina-carmainerose", "normal-summon-christy", "focus-christy-return",
        )
        for sample in samples:
            with self.subTest(sample=sample):
                self.frame = frame(sample)
                observation = BrawlVision(self.frame).observe()
                expected = read_bench(self.frame, observation=observation)
                with bench_name_cache():
                    cold = read_bench(self.frame, observation=observation)
                    warm = read_bench(self.frame.copy(), observation=observation)
                self.assertEqual(cold.entries, expected.entries)
                self.assertEqual(warm.entries, expected.entries)
                self.assertEqual((cold.panel, warm.panel), (expected.panel, expected.panel))

    def test_live_opening_lorina_has_a_readable_imprint_and_unique_plus(self):
        self.frame = frame("opening-lorina-carmainerose")
        observed = read_bench(self.frame)
        self.assertEqual([(entry.name, entry.imprint, entry.plus_area) for entry in observed.entries],
                         [("萝里娜", Imprint.D, (943, 268, 977, 303))])
        self.assertEqual(observed.panel, PreparationPanel(1, 2, 0, 3, 50, 0))
        # The next name is covered by the selected card. This screenshot
        # cannot establish its identity or a complete two-hero inventory.
        self.assertFalse(observed.complete)

    def test_selected_cards_read_names_imprints_and_current_plus(self):
        expected = {
            "normal-deployed": ("卡萝", Imprint.D, True),
            "formation-place": ("最后的碎片卡琳", Imprint.C, True),
            "prepare-level4": ("维多丽卡", Imprint.C, True),
            "prepare-max": ("护卫队长克劳乌", Imprint.B, True),
            "transmit": ("缪斯黎玛", Imprint.NONE, False),
        }
        for name, (hero, imprint, plus) in expected.items():
            with self.subTest(sample=name):
                self.frame = frame(name)
                observed = read_bench(self.frame)
                self.assertEqual((observed.entries[0].name, observed.entries[0].imprint), (hero, imprint))
                self.assertEqual(observed.entries[0].plus_area is not None, plus)
                if plus:
                    self.assertEqual(observed.entries[0].plus_area, (943, 268, 977, 303))
                self.assertFalse(observed.complete)
                self.assertIsNotNone(observed.panel)

    def test_reuses_supplied_scene_observation_but_still_validates_the_image(self):
        self.frame = frame("normal-deployed")
        observation = BrawlVision(self.frame).observe()
        with patch("tasks.brawl.bench.BrawlVision.observe", side_effect=AssertionError("repeated scene read")):
            bench = read_bench(self.frame, observation=observation)
        self.assertEqual(bench.entries[0].name, "卡萝")
        self.assertEqual(bench.panel, observation.panel)
        self.assertFalse(bench.complete)
        with self.assertRaises(ValueError):
            read_bench(np.zeros((360, 640, 3), np.uint8), observation=observation)

    def test_grid_bench_reuses_only_the_explicit_same_frame_panel(self):
        self.frame = frame("formation-place")
        vision = BrawlVision(self.frame)
        observation = vision.observe()
        panel = vision.preparation_panel()
        with patch.object(BrawlVision, "preparation_panel",
                          side_effect=AssertionError("repeated panel read")):
            bench = read_bench(self.frame, observation=replace(observation, panel=panel))
        self.assertEqual(bench.panel, panel)
        self.assertEqual(bench.entries[0].name, "最后的碎片卡琳")
        # Omitting the supplied panel must perform a fresh read. An earlier
        # successful call cannot fill missing resource text on the next frame.
        with patch.object(BrawlVision, "preparation_panel", return_value=panel) as reader:
            self.assertEqual(read_bench(self.frame, observation=observation).panel, panel)
            reader.assert_called_once_with()
        with patch.object(BrawlVision, "preparation_panel", side_effect=ObservationError("unread")):
            self.assertIsNone(read_bench(self.frame, observation=observation).panel)

    def test_same_frame_viewport_reuses_cards_without_bypassing_context(self):
        self.frame = frame("normal-deployed")
        observation = BrawlVision(self.frame).observe()
        visible = read_bench(self.frame, observation=observation)
        scan = SimpleNamespace(panel=visible.panel,
                               heroes=tuple(BenchHero(entry.name, entry.imprint)
                                            for entry in visible.entries))
        with patch("tasks.brawl.placement.read_bench",
                   side_effect=AssertionError("repeated bench read")), \
                patch("tasks.brawl.withdraw.read_bench",
                      side_effect=AssertionError("repeated bench read")):
            self.assertEqual(_selected_entry(self.frame, observation, viewport=visible), visible.entries[0])
            self.assertTrue(_bench_agrees(self.frame, observation, scan, viewport=visible))
            for changed in (replace(observation, state=BrawlScreen.HERO_RESULT),
                            replace(observation, panel=replace(observation.panel, coins=999))):
                self.assertIsNone(_selected_entry(self.frame, changed, viewport=visible))
                self.assertFalse(_bench_agrees(self.frame, changed, scan, viewport=visible))
            wrong_badge = replace(visible, entries=(replace(visible.entries[0], imprint=Imprint.B),))
            self.assertFalse(_bench_agrees(self.frame, observation, scan, viewport=wrong_badge))
            for reader, args in ((_selected_entry, (observation,)),
                                 (_bench_agrees, (observation, scan))):
                with self.assertRaises(ValueError):
                    reader(np.zeros((360, 640, 3), np.uint8), *args, viewport=visible)

    def test_complete_christy_name_template_requires_the_missing_final_glyph(self):
        self.frame = frame("queue-second")
        selected = read_bench(self.frame).entries[0]
        self.assertEqual((selected.name, selected.imprint), ("克莉丝缇", Imprint.D))
        self.assertIsNotNone(selected.plus_area)
        incomplete = SimpleNamespace(ocr_text="克莉丝", score=.99, box=(1003, 254, 1087, 278))
        for image in (frame("queue-top"), self.frame.copy()):
            image[253:279, 1067:1090] = 0
            with patch("tasks.brawl.bench.Ocr.detect_and_ocr", return_value=[incomplete]):
                self.assertEqual(read_bench(image).entries, ())

    def test_occluded_and_bottom_clipped_rows_are_not_complete_entries(self):
        expected = {
            "formation-place": [("最后的碎片卡琳", Imprint.C)],
            "transmit": [("缪斯黎玛", Imprint.NONE)],
            "prepare-level4": [("维多丽卡", Imprint.C), ("玫勒赛德丝", Imprint.C), ("萝季", Imprint.C)],
            "prepare-max": [("护卫队长克劳乌", Imprint.B), ("露彬", Imprint.NONE), ("最后的骑士克劳乌", Imprint.B)],
        }
        for name, heroes in expected.items():
            with self.subTest(sample=name):
                self.frame = frame(name)
                observed = read_bench(self.frame)
                self.assertEqual([(entry.name, entry.imprint) for entry in observed.entries], heroes)
                self.assertFalse(observed.complete)
                if name == "prepare-level4":
                    # Both size-specific templates must read the current card,
                    # without retaining a prior row or filling an erased badge.
                    searches = [button.search for button in BENCH_SMALL_IMPRINT_C.buttons]
                    changed = self.frame.copy()
                    changed[405:436, 1047:1082] = 0
                    changed[466:497, 1072:1107] = 0
                    blank = read_bench(changed)
                    self.assertEqual([(entry.name, entry.imprint) for entry in blank.entries],
                                     [("维多丽卡", Imprint.C), ("玫勒赛德丝", None), ("萝季", None)])
                    self.assertEqual([button.search for button in BENCH_SMALL_IMPRINT_C.buttons], searches)

    def test_unread_imprint_and_hidden_plus_never_reuse_previous_frame(self):
        self.frame = frame("normal-deployed")
        original = read_bench(self.frame)
        self.assertIsNotNone(original.entries[0].plus_area)
        changed = self.frame.copy()
        changed[286:323, 997:1049] = 0
        changed[250:322, 925:987] = 0
        observed = read_bench(changed)
        self.assertEqual(observed.entries[0].name, "卡萝")
        self.assertIsNone(observed.entries[0].imprint)
        self.assertIsNone(observed.entries[0].plus_area)
        self.assertFalse(observed.complete)
        self.assertIn("Selected formation plus matches=0", observed.reason)
        self.assertIn("Selected imprint unread", observed.reason)

    def test_low_confidence_unknown_names_and_overlays_do_not_create_entries(self):
        self.frame = frame("normal-deployed")
        for name, score in (("卡萝", .79), ("卡萝", float("nan")), ("未知英雄", .99)):
            with self.subTest(name=name, score=score), patch(
                    "tasks.brawl.bench.Ocr.detect_and_ocr", return_value=[
                        SimpleNamespace(ocr_text=name, score=score, box=(1000, 253, 1060, 278))]):
                observed = read_bench(self.frame)
                self.assertEqual(observed.entries, ())
                self.assertIn("Selected name rejected", observed.reason)
                self.assertIn("area=(1000, 253, 1060, 278)", observed.reason)
                self.assertIn("confidence=", observed.reason)
        for name in ("lobby", "hero-new-05", "probabilities", "round-clear"):
            with self.subTest(sample=name):
                self.frame = frame(name)
                self.assertEqual(read_bench(self.frame).entries, ())
        self.assertEqual(read_bench(np.zeros((720, 1280, 3), np.uint8)).entries, ())

    def test_model_failures_and_unsupported_inputs_are_explicit(self):
        self.frame = frame("normal-deployed")
        with patch("tasks.brawl.bench.Ocr.detect_and_ocr", side_effect=RuntimeError("model unavailable")), \
                self.assertRaisesRegex(RuntimeError, "model unavailable"):
            read_bench(self.frame)
        for invalid in (None, np.zeros((360, 640, 3), np.uint8)):
            with self.assertRaises(ValueError):
                read_bench(invalid)
        try:
            server.set_lang("cn")
            with self.assertRaises(ValueError):
                read_bench(self.frame)
        finally:
            server.set_lang("global_cn")


class BenchMergeTests(unittest.TestCase):
    def test_overlapping_views_cannot_grow_past_ten_bench_seats(self):
        heroes = [(name, Imprint.NONE) for name in tuple(HEROES)[:11]]
        first = viewport(*heroes[:6])
        tenth = viewport(*heroes[5:10])
        merged = merge_bench_views((first, tenth))
        self.assertEqual(len(merged.heroes), 10)
        self.assertFalse(merged.complete)
        with self.assertRaisesRegex(ObservationError, "ten-seat"):
            merge_bench_views((first, tenth, viewport(*heroes[9:])))
        with self.assertRaisesRegex(ObservationError, "ten-seat"):
            merge_bench_views((viewport(*heroes),))

    def test_overlapping_reads_merge_in_both_directions_without_coordinates(self):
        first = viewport(("卡琳", Imprint.C), ("萝季", Imprint.C))
        later = viewport(("萝季", Imprint.C), ("卡萝", Imprint.D))
        before = viewport(("露彬", Imprint.NONE), ("卡琳", Imprint.C))
        observed = merge_bench_views((first, later, before))
        self.assertEqual(observed.heroes, (BenchHero("露彬", Imprint.NONE), BenchHero("卡琳", Imprint.C),
                                          BenchHero("萝季", Imprint.C), BenchHero("卡萝", Imprint.D)))
        self.assertFalse(observed.complete)
        self.assertFalse(hasattr(observed.heroes[0], "plus_area"))

    def test_repeated_subset_frames_and_unknown_imprints_never_prove_completion(self):
        first = viewport(("卡琳", None), ("萝季", Imprint.C))
        observed = merge_bench_views((first, first, viewport(("萝季", Imprint.C))))
        self.assertEqual(observed.heroes[0], BenchHero("卡琳", None))
        self.assertFalse(observed.complete)
        known = viewport(("卡琳", Imprint.C), ("萝季", Imprint.C))
        observed = merge_bench_views((first, known, first))
        self.assertEqual(observed.heroes[0], BenchHero("卡琳", Imprint.C))
        self.assertFalse(observed.complete)

    def test_same_round_with_any_changed_preparation_field_is_rejected(self):
        first = viewport(("卡琳", Imprint.C))
        for field in ("round_number", "level", "deployed", "capacity", "coins", "synergy_tokens"):
            changed = replace(PANEL, **{field: getattr(PANEL, field) + 1})
            with self.subTest(field=field), self.assertRaisesRegex(ObservationError, "panels disagree"):
                merge_bench_views((first, viewport(("卡琳", Imprint.C), panel=changed)))
        with self.assertRaisesRegex(ObservationError, "panel is unread"):
            merge_bench_views((viewport(("卡琳", Imprint.C), panel=None),))

    def test_contradictory_or_disconnected_views_cannot_be_silently_joined(self):
        first = viewport(("卡琳", Imprint.C), ("萝季", Imprint.C))
        invalid = (
            viewport(("萝季", Imprint.B), ("卡萝", Imprint.D)),
            viewport(("萝季", Imprint.C), ("卡琳", Imprint.C)),
            viewport(("露彬", Imprint.NONE)),
            viewport(("卡琳", Imprint.C), ("卡琳", Imprint.C)),
            viewport(),
        )
        for later in invalid:
            with self.subTest(later=later), self.assertRaises(ObservationError):
                merge_bench_views((first, later))
        with self.assertRaises(ObservationError):
            merge_bench_views(())
