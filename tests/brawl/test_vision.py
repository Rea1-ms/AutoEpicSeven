import unittest
from dataclasses import asdict, replace
from typing import ClassVar
from unittest.mock import patch

import numpy as np

from module.config import server

server.set_lang("global_cn")

from module.ocr.ocr import Ocr  # noqa: E402
from tasks.brawl.assets.assets_brawl import (  # noqa: E402
    BRAWL_CHECK,
    COIN_ICON,
    HERO_RESULT_CHECK,
    TOKEN_ICON,
)
from tasks.brawl.observation_cache import (  # noqa: E402
    invalidate_panel_observation_cache,
    panel_observation_cache,
)
from tasks.brawl.talents import read_opening_talents  # noqa: E402
from tasks.brawl.vision import (  # noqa: E402
    BrawlMode,
    BrawlScreen,
    BrawlVision,
    ObservationError,
    parse_panel,
)
from tests.support.brawl import frame  # noqa: E402


class VisionTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")

    def observe(self, name):
        self.frame = frame(name)
        return BrawlVision(self.frame).observe()

    def test_scoped_panel_cache_reuses_real_grid_and_move_frames(self):
        reads = Ocr.ocr_multi_lines
        with panel_observation_cache(), patch.object(
                Ocr, "ocr_multi_lines", autospec=True, side_effect=reads) as reader:
            first = self.observe("full-swap-before")
            self.assertIsNotNone(first.panel, first.reason)
            for name in ("full-swap-grid", "full-swap-after", "full-swap-stable"):
                self.frame = frame(name)
                vision = BrawlVision(self.frame)
                current = vision.observe()
                panel = current.panel or vision.preparation_panel()
                self.assertEqual(panel, first.panel)
            self.assertEqual(reader.call_count, 1)

    def test_panel_cache_refreshes_changed_digits_and_never_estimates_delayed_balances(self):
        reads = Ocr.ocr_multi_lines
        with panel_observation_cache(), patch.object(
                Ocr, "ocr_multi_lines", autospec=True, side_effect=reads) as reader:
            before = self.observe("normal-round2")
            self.assertEqual(self.observe("normal-round2").panel, before.panel)
            after = self.observe("normal-after-loss")
            self.assertEqual((before.panel.round_number, after.panel.round_number), (2, 2))
            self.assertEqual((before.panel.coins, after.panel.coins), (55, 70))
            self.assertEqual(reader.call_count, 2)
            # The old actual frame remains old evidence, not an assumed credit.
            self.assertEqual(self.observe("normal-round2").panel.coins, 55)
            self.assertEqual(reader.call_count, 3)

    def test_panel_cache_keeps_overlay_and_missing_currency_unreadable(self):
        with panel_observation_cache():
            self.assertIsNotNone(self.observe("prepare-level4").panel)
            with patch.object(Ocr, "ocr_multi_lines", side_effect=AssertionError("obscured OCR")):
                current = self.observe("network-retry")
                self.assertEqual(current.state, BrawlScreen.NETWORK_RETRY)
                self.assertIsNone(current.panel)
                with self.assertRaises(ObservationError):
                    BrawlVision(self.frame).preparation_panel()
                self.frame = frame("prepare-level4").copy()
                self.frame[70:110, 1020:1210] = 0
                self.assertIsNone(BrawlVision(self.frame).observe().panel)
            # An unread numeric field must not return the previous valid panel.
            self.frame = frame("prepare-level4").copy()
            self.frame[48:94, 606:676] = 0
            with self.assertRaises(ObservationError):
                BrawlVision(self.frame).preparation_panel()

    def test_panel_cache_event_boundaries_and_explicit_invalidation_force_fresh_reads(self):
        reads = Ocr.ocr_multi_lines
        with panel_observation_cache(), patch.object(
                Ocr, "ocr_multi_lines", autospec=True, side_effect=reads) as reader:
            first = self.observe("normal-round2")
            self.assertEqual(reader.call_count, 1)
            self.observe("normal-combat")
            self.observe("round-failed")
            self.assertEqual(self.observe("normal-round2").panel, first.panel)
            self.assertEqual(reader.call_count, 2)
            invalidate_panel_observation_cache()
            self.assertEqual(self.observe("normal-round2").panel, first.panel)
            self.assertEqual(reader.call_count, 3)
            # Repeated direct dialog reads do not repeatedly invalidate a page.
            self.frame = frame("upgrade-dialog")
            first_dialog = BrawlVision(self.frame).preparation_panel()
            self.assertEqual(BrawlVision(self.frame.copy()).preparation_panel(), first_dialog)
            self.assertEqual(reader.call_count, 4)

    def test_panel_cache_run_scopes_and_standalone_observations_are_isolated(self):
        reads = Ocr.ocr_multi_lines
        with patch.object(Ocr, "ocr_multi_lines", autospec=True, side_effect=reads) as reader:
            with panel_observation_cache():
                expected = self.observe("prepare-level4").panel
                self.assertEqual(self.observe("prepare-level4").panel, expected)
            with panel_observation_cache():
                self.assertEqual(self.observe("prepare-level4").panel, expected)
            self.assertEqual(self.observe("prepare-level4").panel, expected)
            self.assertEqual(self.observe("prepare-level4").panel, expected)
            self.assertEqual(reader.call_count, 4)

    def test_panel_cache_reuses_scene_proof_only_inside_the_current_observe_call(self):
        self.frame = frame("prepare-level4")
        vision = BrawlVision(self.frame)
        with panel_observation_cache(), patch.object(vision, "matches", wraps=vision.matches) as matched:
            expected = vision.observe().panel
            self.assertIsNotNone(expected)
            self.assertEqual(sum(call.args[0] is BRAWL_CHECK for call in matched.call_args_list), 1)
            matched.reset_mock()
            self.assertEqual(vision.preparation_panel(), expected)
            self.assertEqual(sum(call.args[0] is BRAWL_CHECK for call in matched.call_args_list), 1)
            # A failed numeric read must also release the temporary page proof.
            with patch.object(vision, "preparation_panel", side_effect=ObservationError("unread")):
                self.assertIsNone(vision.observe().panel)
            vision.image = frame("network-retry")
            with self.assertRaises(ObservationError):
                vision.preparation_panel()

    def test_reported_network_overlay_precedes_underlying_preparation_without_ocr(self):
        self.frame = frame("network-retry")
        with patch.object(BrawlVision, "preparation_panel", side_effect=AssertionError("dimmed counters")):
            observed = BrawlVision(self.frame).observe()
        self.assertEqual(observed.state.value, "network_retry")
        self.assertIsNone(observed.panel)
        self.assertIsNone(observed.speed)

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

    def test_hero_footer_accepts_artwork_variation_without_matching_other_pages(self):
        self.frame = frame("normal-summon-christy")
        self.assertFalse(HERO_RESULT_CHECK.match_template(self.frame, similarity=0.9))
        self.assertTrue(BrawlVision(self.frame).matches(HERO_RESULT_CHECK))
        self.assertEqual(BrawlVision(self.frame).observe().state, BrawlScreen.HERO_RESULT)
        self.frame = self.frame.copy()
        self.frame[626:691, 570:715] = 0
        self.assertFalse(BrawlVision(self.frame).matches(HERO_RESULT_CHECK))
        for name in ("lobby", "normal-opening", "normal-first-summon", "prepare-level5",
                     "combat", "round-clear", "round-failed", "upgrade-dialog", "formation",
                     "transmit", "probabilities", "normal-victory15", "challenge-defeat"):
            with self.subTest(sample=name):
                self.frame = frame(name)
                self.assertFalse(BrawlVision(self.frame).matches(HERO_RESULT_CHECK))

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


class OpeningTalentTests(unittest.TestCase):
    EXPECTED: ClassVar[dict[str, int]] = {"战士": 1, "骑士": 1, "射手": 1, "魔导士": 1, "盗贼": 1, "精灵师": 1}

    def setUp(self):
        server.set_lang("global_cn")

    def opening(self):
        self.frame = frame("opening-lorina-carmainerose")
        return BrawlVision(self.frame).observe()

    def test_captured_empty_openings_read_all_six_current_talents(self):
        for name in ("opening-lorina-carmainerose", "normal-opening", "normal-first-summon"):
            with self.subTest(sample=name):
                self.frame = frame(name)
                self.assertEqual(read_opening_talents(self.frame), self.EXPECTED)

    def test_captured_incomplete_profession_list_is_not_six_confirmed_talents(self):
        self.frame = frame("challenge-opening")
        # The user confirmed that locked talents are hidden entirely. This
        # older capture predates unlocking Soul Weaver, unlike the new frame.
        self.assertEqual(read_opening_talents(self.frame), {**self.EXPECTED, "精灵师": 0})

    def test_reordered_rows_follow_profession_icons_instead_of_old_positions(self):
        observed = self.opening()
        original = self.frame.copy()
        # Reorder complete photographed rows, including their count and bar.
        # Mixed count parsing is tested separately with explicit OCR outputs.
        for target, source in enumerate((5, 3, 1, 4, 0, 2)):
            top, source_top = 380 + target * 37, 380 + source * 37
            self.frame[top:top + 37, 20:110] = original[source_top:source_top + 37, 20:110]
        self.assertEqual(read_opening_talents(self.frame, observation=observed), self.EXPECTED)

    def test_missing_duplicate_and_dimmed_icons_cannot_default_to_zero(self):
        observed = self.opening()
        original = self.frame.copy()
        for damage in ("missing", "duplicate", "dimmed"):
            with self.subTest(damage=damage):
                self.frame = original.copy()
                if damage == "missing":
                    self.frame[384:411, 26:55] = 0
                elif damage == "duplicate":
                    self.frame[200:227, 26:55] = original[384:411, 26:55]
                else:
                    self.frame[384:411, 26:55] = (original[384:411, 26:55] * 0.3).astype(np.uint8)
                with patch("tasks.brawl.talents.Ocr.ocr_multi_lines") as counts, self.assertRaises(ObservationError):
                    read_opening_talents(self.frame, observation=observed)
                counts.assert_not_called()
        self.frame = original
        self.assertEqual(read_opening_talents(self.frame, observation=observed), self.EXPECTED)

    def test_hidden_or_dimmed_synergy_tab_rejects_before_count_reading(self):
        observed = self.opening()
        original = self.frame.copy()
        for factor in (0, 0.3):
            with self.subTest(factor=factor):
                self.frame = original.copy()
                self.frame[656:679, 24:94] = (original[656:679, 24:94] * factor).astype(np.uint8)
                with patch("tasks.brawl.talents.Ocr") as ocr, self.assertRaises(ObservationError):
                    read_opening_talents(self.frame, observation=observed)
                ocr.assert_not_called()

    def test_deployed_later_round_and_overlays_are_not_talent_evidence(self):
        for name in ("normal-deployed", "normal-round2", "hero-result", "formation-place"):
            with self.subTest(sample=name):
                self.frame = frame(name)
                with self.assertRaises(ObservationError):
                    read_opening_talents(self.frame)
        observed = self.opening()
        for invalid in (replace(observed, panel=None), replace(observed, game_mode=None)):
            with self.subTest(observation=invalid), patch("tasks.brawl.talents.Ocr") as ocr, \
                    self.assertRaises(ObservationError):
                read_opening_talents(self.frame, observation=invalid)
            ocr.assert_not_called()

    def test_explicit_zero_and_mixed_counts_are_read_without_manual_defaults(self):
        observed = self.opening()
        with patch("tasks.brawl.talents.Ocr.ocr_multi_lines") as counts:
            counts.return_value = [
                (text, 0.99) for text in ("0/6", "1/6", "0/6", "1/4", "1/6", "0/4")]
            self.assertEqual(read_opening_talents(self.frame, observation=observed),
                             {"战士": 0, "骑士": 1, "射手": 0, "魔导士": 1, "盗贼": 1, "精灵师": 0})
        self.assertEqual(len(counts.call_args.args[0]), 6)

    def test_incomplete_low_confidence_and_invalid_counts_stay_unknown(self):
        observed = self.opening()
        valid = [(text, 0.99) for text in ("1/6", "1/6", "1/6", "1/4", "1/6", "1/4")]
        candidates = [valid[:-1], valid + [("1/6", 0.99)]]
        for value in (("", 0.99), ("1/6", 0.89), ("1/6", float("nan")),
                      ("2/6", 0.99), ("1/4", 0.99), ("1/6-", 0.99)):
            candidates.append([value, *valid[1:]])
        for values in candidates:
            with self.subTest(values=values), \
                    patch("tasks.brawl.talents.Ocr.ocr_multi_lines", return_value=values), \
                    self.assertRaises(ObservationError):
                read_opening_talents(self.frame, observation=observed)

    def test_supplied_observation_validates_image_and_model_errors_propagate(self):
        observed = self.opening()
        with patch("tasks.brawl.talents.Ocr.ocr_multi_lines", side_effect=RuntimeError("model unavailable")), \
                self.assertRaisesRegex(RuntimeError, "model unavailable"):
            read_opening_talents(self.frame, observation=observed)
        for invalid in (None, np.zeros((360, 640, 3), np.uint8),
                        np.zeros((720, 1280, 3), np.float32)):
            with self.subTest(shape=getattr(invalid, "shape", None)), self.assertRaises(ValueError):
                read_opening_talents(invalid, observation=observed)
        with patch.object(server, "lang", "cn"), self.assertRaises(ValueError):
            read_opening_talents(self.frame, observation=observed)
