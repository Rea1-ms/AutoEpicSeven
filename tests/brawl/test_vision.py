import unittest
from dataclasses import asdict
from unittest.mock import patch

import numpy as np

from module.config import server

server.set_lang("global_cn")

from tasks.brawl.assets.assets_brawl import COIN_ICON, TOKEN_ICON  # noqa: E402
from tasks.brawl.vision import BrawlMode, BrawlScreen, BrawlVision, parse_panel  # noqa: E402
from tests.support.brawl import frame  # noqa: E402


class VisionTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")

    def observe(self, name):
        self.frame = frame(name)
        return BrawlVision(self.frame).observe()

    def test_real_preparation_fields_across_levels_and_currencies(self):
        expected = {
            "prepare-level4": (7, 4, 5, 5, 145, 0),
            "prepare-level5": (8, 5, 5, 6, 105, 0),
            "prepare-level6": (11, 6, 7, 7, 147, 0),
            "prepare-level7": (12, 7, 7, 8, 52, 0),
            "prepare-level8": (15, 8, 8, 9, 11, 0),
            "prepare-max-spare": (19, 9, 9, 10, 41, 0),
            "prepare-one-coin": (20, 9, 10, 10, 1, 0),
            "prepare-transfer-return": (20, 9, 10, 10, 44, 37),
            "prepare-max": (21, 9, 10, 10, 33, 62),
        }
        for name, values in expected.items():
            with self.subTest(sample=name):
                observation = self.observe(name)
                self.assertEqual(observation.state, BrawlScreen.PREPARATION)
                self.assertIsNotNone(observation.panel, observation.reason)
                self.assertEqual(tuple(asdict(observation.panel).values()), values)

    def test_overlay_scene_priority_does_not_read_underlying_balances(self):
        expected = {
            "lobby": BrawlScreen.LOBBY,
            "give-up": BrawlScreen.GIVE_UP_CONFIRM,
            "upgrade": BrawlScreen.LEVEL_UP,
            "hero-result": BrawlScreen.HERO_RESULT,
            "hero-imprint": BrawlScreen.HERO_RESULT,
            "transmit": BrawlScreen.TRANSMIT_SELECTION,
            "probabilities": BrawlScreen.PROBABILITY,
            "combat": BrawlScreen.COMBAT,
            "round-clear": BrawlScreen.ROUND_CLEAR,
            "coin-tooltip": BrawlScreen.UNKNOWN,
            "token-tooltip": BrawlScreen.UNKNOWN,
            "income-tooltip": BrawlScreen.UNKNOWN,
            "formation": BrawlScreen.FORMATION_SELECTION,
        }
        for name, state in expected.items():
            with self.subTest(sample=name):
                result = self.observe(name)
                self.assertEqual(result.state, state)
                self.assertIsNone(result.panel)

    def test_upgrade_animation_waits_then_reads_new_level_and_capacity(self):
        observations = [self.observe(name) for name in (
            "upgrade", "upgrade-animation", "upgrade-animation", "prepare-level5")]
        self.assertEqual(observations[0].state, BrawlScreen.LEVEL_UP)
        self.assertIsNone(observations[1].panel)
        self.assertIsNone(observations[2].panel)
        self.assertEqual((observations[3].panel.level, observations[3].panel.capacity), (5, 6))
        self.assertEqual(observations[3].panel.coins, 105)

    def test_currency_positions_shift_and_missing_icons_never_reuse_previous_match(self):
        original_search = (COIN_ICON.search, TOKEN_ICON.search)
        first = frame("prepare-level4")
        second = frame("prepare-max")
        first_areas = BrawlVision(first).currency_areas()
        second_areas = BrawlVision(second).currency_areas()
        self.assertNotEqual(first_areas, second_areas)
        self.assertEqual((COIN_ICON.search, TOKEN_ICON.search), original_search)
        try:
            COIN_ICON.load_offset((8, 0))
            TOKEN_ICON.load_offset((8, 0))
            self.assertEqual(BrawlVision(first).currency_areas(), first_areas)
        finally:
            COIN_ICON.load_offset((0, 0))
            TOKEN_ICON.load_offset((0, 0))
        for region in ((1020, 70, 1210, 110), (1210, 70, 1260, 115)):
            self.frame = second.copy()
            x1, y1, x2, y2 = region
            self.frame[y1:y2, x1:x2] = 0
            observed = BrawlVision(self.frame).observe()
            self.assertIsNone(observed.panel)
            self.assertEqual((COIN_ICON.search, TOKEN_ICON.search), original_search)
        self.assertEqual(BrawlVision(first).observe().panel.coins, 145)

    def test_blank_dimmed_and_wrong_resolution_inputs_are_not_preparation(self):
        self.assertEqual(BrawlVision(np.zeros((720, 1280, 3), dtype=np.uint8)).observe().state,
                         BrawlScreen.UNKNOWN)
        self.frame = (frame("prepare-level4").astype(np.float32) * 0.3).astype(np.uint8)
        self.assertIsNone(BrawlVision(self.frame).observe().panel)
        for invalid in (None, np.zeros((360, 640, 3), dtype=np.uint8),
                        np.zeros((720, 1280, 3), dtype=np.float32)):
            with self.subTest(shape=getattr(invalid, "shape", None)), self.assertRaises(ValueError):
                BrawlVision(invalid)

    def test_parser_rejects_low_confidence_and_contradictory_fields(self):
        valid = [(value, 0.99) for value in ("7", "Lv.4", "5/5", "145", "0")]
        for index, value in ((0, "26"), (1, "Lv.10"), (2, "5/6"), (2, "6/5"),
                             (3, "-10"), (4, "。"), (4, "")):
            changed = list(valid)
            changed[index] = (value, 0.99)
            with self.subTest(index=index, value=value), self.assertRaises(ValueError):
                parse_panel(changed)
        for confidence in (0.79, float("nan")):
            with self.subTest(confidence=confidence), self.assertRaises(ValueError):
                parse_panel([*valid[:-1], ("0", confidence)])

    def test_model_errors_are_not_downgraded_to_unknown_readings(self):
        self.frame = frame("prepare-level4")
        with patch("tasks.brawl.vision.Ocr.ocr_multi_lines", side_effect=ValueError("model unavailable")), \
                self.assertRaisesRegex(ValueError, "model unavailable"):
            BrawlVision(self.frame).observe()

    def test_new_modes_openings_and_final_results(self):
        panels = {
            "normal-opening": (BrawlMode.NORMAL, (1, 2, 0, 3, 70, 0)),
            "challenge-opening": (BrawlMode.CHALLENGE, (1, 2, 0, 3, 70, 0)),
            "challenge-prepared": (BrawlMode.CHALLENGE, (1, 3, 2, 4, 0, 0)),
            "normal-first-summon": (BrawlMode.NORMAL, (1, 3, 0, 4, 10, 0)),
            "normal-deployed": (BrawlMode.NORMAL, (1, 3, 1, 4, 0, 0)),
            "normal-round2": (BrawlMode.NORMAL, (2, 3, 1, 4, 55, 0)),
            "normal-after-loss": (BrawlMode.NORMAL, (2, 3, 1, 4, 70, 0)),
        }
        for name, (mode, values) in panels.items():
            with self.subTest(sample=name):
                result = self.observe(name)
                self.assertEqual(result.state, BrawlScreen.PREPARATION)
                self.assertEqual(result.game_mode, mode)
                self.assertIsNotNone(result.panel, result.reason)
                self.assertEqual(tuple(asdict(result.panel).values()), values)
        scenes = {
            "normal-victory15": (BrawlScreen.RUN_VICTORY, BrawlMode.NORMAL),
            "normal-victory16": (BrawlScreen.RUN_VICTORY, BrawlMode.NORMAL),
            "normal-defeat": (BrawlScreen.RUN_DEFEAT, BrawlMode.NORMAL),
            "challenge-defeat": (BrawlScreen.RUN_DEFEAT, BrawlMode.CHALLENGE),
            "normal-combat": (BrawlScreen.COMBAT, BrawlMode.NORMAL),
            "challenge-combat": (BrawlScreen.COMBAT, BrawlMode.CHALLENGE),
            "normal-lobby-empty": (BrawlScreen.LOBBY, None),
            "challenge-lobby-empty": (BrawlScreen.LOBBY, None),
        }
        for name, (state, mode) in scenes.items():
            with self.subTest(sample=name):
                result = self.observe(name)
                self.assertEqual((result.state, result.game_mode), (state, mode))
                self.assertIsNone(result.panel)

    def test_rank_and_unlock_overlays_are_not_summons_or_completion(self):
        for name, expected in (("rank-up15", BrawlScreen.RANK_UP), ("rank-up16", BrawlScreen.RANK_UP),
                               ("hero-unlock", BrawlScreen.HERO_UNLOCK)):
            with self.subTest(sample=name):
                result = self.observe(name)
                self.assertEqual(result.state, expected)
                self.assertIsNone(result.panel)

    def test_grid_selection_blocks_preparation_readings(self):
        for name in ("formation-place", "formation-move"):
            with self.subTest(sample=name):
                result = self.observe(name)
                self.assertEqual(result.state, BrawlScreen.FORMATION_SELECTION)
                self.assertIsNone(result.panel)

    def test_round_failures_are_automatic_transitions(self):
        for name in ("round-failed", "last-round-failed"):
            with self.subTest(sample=name):
                result = self.observe(name)
                self.assertEqual(result.state, BrawlScreen.ROUND_FAILED)
                self.assertIsNone(result.panel)

    def test_final_result_requires_both_message_and_return_control(self):
        for name, text_area in (("normal-victory15", (423, 242, 857, 271)),
                                ("challenge-defeat", (495, 242, 787, 271))):
            for area in (text_area, (615, 628, 668, 658)):
                with self.subTest(sample=name, masked=area):
                    self.frame = frame(name).copy()
                    x1, y1, x2, y2 = area
                    self.frame[y1:y2, x1:x2] = 0
                    result = BrawlVision(self.frame).observe()
                    self.assertNotIn(result.state, (BrawlScreen.RUN_VICTORY, BrawlScreen.RUN_DEFEAT))
                    self.assertIsNone(result.panel)

    def test_speed_requires_stable_scene_and_unambiguous_visible_control(self):
        for name, speed in (("combat-speed-one", 1), ("combat-speed-two", 2), ("normal-opening", 2)):
            with self.subTest(sample=name):
                self.assertEqual(self.observe(name).speed, speed)
        for name in ("rank-up15", "normal-victory15", "hero-unlock", "formation-place", "round-failed"):
            with self.subTest(sample=name):
                self.assertIsNone(self.observe(name).speed)
        self.frame = frame("combat-speed-one").copy()
        self.frame[0:65, 1090:1170] = 0
        self.assertIsNone(BrawlVision(self.frame).observe().speed)
