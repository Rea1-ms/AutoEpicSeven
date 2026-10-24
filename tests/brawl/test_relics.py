"""Captured relics and save route; repeated frames and mutations are synthetic."""

import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

import numpy as np

from module.config import server

server.set_lang("global_cn")

from tasks.brawl.assets.assets_brawl_relics import (  # noqa: E402
    HARMONY_SELECTED, NODE_AVAILABLE, NODE_INACTIVE, RELIC_PAGE_CHECK, SAVE_EXIT,
)
from tasks.brawl.relics import (  # noqa: E402
    BrawlRelics, _NODES, harmony_selected, pause_menu, read_relic_talents, relic_page,
)
from tasks.brawl.vision import (  # noqa: E402
    BrawlVision, ObservationError, PreparationPanel,
)
from tests.support.brawl import BrawlReplay, frame  # noqa: E402
from tests.support.offline import ControlledClock  # noqa: E402


BONUSES = {name: 1 for name, *_ in _NODES}
PANEL = PreparationPanel(3, 3, 4, 4, 60, 0)


class RelicRecognitionTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")

    def test_real_harmony_reads_all_six_without_ocr(self):
        image = frame("relic-harmony")
        with patch("tasks.brawl.vision.Ocr", side_effect=AssertionError("Relics require no OCR")):
            self.assertEqual(read_relic_talents(image), BONUSES)

    def test_other_relic_trees_cannot_supply_harmony_talents(self):
        for label in ("earth", "sky", "stars", "trials"):
            with self.subTest(label=label):
                image = frame("relic-" + label)
                self.assertTrue(relic_page(image))
                self.assertFalse(harmony_selected(image))
                with self.assertRaises(ObservationError):
                    read_relic_talents(image)

    def test_synthetic_gray_and_available_borders_are_explicit_zero_states(self):
        # Only blue harmony nodes were photographed. These are color-state
        # mutations using references from other real relic trees, not captures
        # of a second account with unactivated profession bonuses.
        for reference in (NODE_INACTIVE, NODE_AVAILABLE):
            image = frame("relic-harmony").copy()
            for _, _, (x, y) in _NODES:
                image[y - 35:y - 32, x - 12:x + 13] = reference.color
            self.assertEqual(read_relic_talents(image), dict.fromkeys(BONUSES, 0))

    def test_missing_or_displaced_glyph_never_defaults_to_zero(self):
        for name, icon, _ in _NODES:
            for displacement in (0, 7):
                with self.subTest(name=name, displacement=displacement):
                    image = frame("relic-harmony").copy()
                    l, t, r, b = icon.area
                    glyph = image[t:b, l:r].copy()
                    image[t:b, l:r] = 0
                    if displacement:
                        image[t:b, l + displacement:r + displacement] = glyph
                    with self.assertRaises(ObservationError):
                        read_relic_talents(image)

    def test_unknown_border_or_missing_page_proof_never_returns_partial_talents(self):
        original = frame("relic-harmony")
        boxes = [(x - 12, y - 35, x + 13, y - 32) for _, _, (x, y) in _NODES]
        boxes += [RELIC_PAGE_CHECK.area, HARMONY_SELECTED.area]
        for box in boxes:
            for color in ((0, 0, 0), (255, 0, 0)):
                with self.subTest(box=box, color=color):
                    image = original.copy()
                    l, t, r, b = box
                    image[t:b, l:r] = color
                    with self.assertRaises(ObservationError):
                        read_relic_talents(image)
        with self.assertRaises(ObservationError):
            read_relic_talents((original * 0.45).astype(np.uint8))

    def test_pause_overlay_is_distinct_from_preparation_and_requires_all_controls(self):
        self.assertFalse(pause_menu(frame("relic-preparation")))
        image = frame("relic-paused")
        self.assertTrue(pause_menu(image))
        image = image.copy()
        l, t, r, b = SAVE_EXIT.search
        image[t:b, l:r] = 0
        self.assertFalse(pause_menu(image))


class RelicFlowTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")
        self.clock = ControlledClock()
        self.clock.__enter__()
        self.addCleanup(self.clock.__exit__, None, None, None)

    def replay(self, frames):
        replay = BrawlReplay(BrawlRelics, frames, self.clock)
        replay.task.ui_ensure = Mock(side_effect=AssertionError("Unexpected global navigation"))
        return replay

    def visit(self):
        return (["relic-lobby"] * 10 + ["relic-earth"] * 10
                + ["relic-harmony"] * 12 + ["relic-return-lobby"] * 10)

    def saved_visit(self):
        return ["relic-preparation"] * 10 + ["relic-paused"] * 10 + self.visit()

    def assert_actions(self, replay, names):
        self.assertEqual([action[1] for action in replay.actions], names)
        for before, after in zip(replay.actions, replay.actions[1:]):
            self.assertGreaterEqual((after[0] - before[0]) * replay.seconds, 3)

    def test_lobby_visit_uses_each_control_once_and_never_ocr_or_nodes(self):
        replay = self.replay(self.visit())
        with patch("tasks.brawl.vision.Ocr", side_effect=AssertionError("Relics require no OCR")):
            self.assertEqual(replay.task.read(), BONUSES)
        self.assert_actions(replay, ["LOBBY_RELICS", "HARMONY_TAB", "BACK"])
        self.assertEqual(replay.index, 32)

    def test_starting_on_harmony_confirms_two_frames_then_waits_for_lobby(self):
        replay = self.replay(["relic-harmony"] * 12 + ["relic-return-lobby"])
        self.assertEqual(replay.task.read(), BONUSES)
        self.assert_actions(replay, ["BACK"])
        self.assertEqual(replay.actions[0][0], 1)
        self.assertEqual(replay.index, 12)
        # A positive terminal frame is valid even if the screenshot arrives
        # after the local waiting deadline; an unknown frame is still bounded.
        replay = self.replay(["relic-harmony"] * 2 + ["relic-return-lobby"])
        screenshot = replay.screenshot

        def delayed_return():
            screenshot()
            if replay.index == 2:
                self.clock.advance(21)

        replay.screenshot = delayed_return
        self.assertEqual(replay.task.read(), BONUSES)
        self.assert_actions(replay, ["BACK"])

    def test_initial_shared_action_cooldown_is_preserved(self):
        replay = self.replay(self.visit())
        replay.task.interval_reset(replay.task.ACTION_TIMER, interval=3)
        self.assertEqual(replay.task.read(), BONUSES)
        self.assertEqual(replay.actions[0][0], 7)
        self.assert_actions(replay, ["LOBBY_RELICS", "HARMONY_TAB", "BACK"])

    def test_preparation_saves_reads_and_continues_same_real_panel(self):
        replay = self.replay(self.saved_visit() + ["relic-preparation"])
        reads = []
        original = BrawlVision.preparation_panel

        def panel(vision):
            reads.append(replay.index)
            return original(vision)

        with patch.object(BrawlVision, "preparation_panel", panel):
            self.assertEqual(replay.task.read(), BONUSES)
        self.assert_actions(replay, ["PAUSE_BUTTON", "SAVE_EXIT", "LOBBY_RELICS",
                                     "HARMONY_TAB", "BACK", "LOBBY_CONTINUE"])
        self.assertEqual(reads, [0, 62])
        self.assertEqual(BrawlVision(replay.image).observe().panel, PANEL)

    def test_starting_paused_returns_once_to_obtain_unmasked_original_context(self):
        replay = self.replay(["relic-paused"] * 10 + self.saved_visit() + ["relic-preparation"])
        with patch.object(BrawlVision, "preparation_panel", return_value=PANEL):
            self.assertEqual(replay.task.read(), BONUSES)
        self.assert_actions(replay, ["PAUSE_RETURN", "PAUSE_BUTTON", "SAVE_EXIT", "LOBBY_RELICS",
                                     "HARMONY_TAB", "BACK", "LOBBY_CONTINUE"])

    def test_missing_save_after_exit_stops_without_new_entry_or_relic_click(self):
        replay = self.replay(["relic-preparation"] * 10 + ["relic-paused"] * 10
                             + ["normal-lobby-empty"])
        with patch.object(BrawlVision, "preparation_panel", return_value=PANEL), \
                self.assertRaisesRegex(ObservationError, "saved run is not available"):
            replay.task.read()
        self.assert_actions(replay, ["PAUSE_BUTTON", "SAVE_EXIT"])

    def test_save_changed_after_relic_read_stops_before_continue_or_new_entry(self):
        replay = self.replay(["relic-lobby"] * 10 + ["relic-earth"] * 10
                             + ["relic-harmony"] * 12 + ["normal-lobby-empty"])
        with self.assertRaisesRegex(ObservationError, "lobby save changed"):
            replay.task.read()
        self.assert_actions(replay, ["LOBBY_RELICS", "HARMONY_TAB", "BACK"])

    def test_changed_original_panel_or_mode_stops_after_only_one_continue(self):
        for field, value in (("round_number", 4), ("level", 4), ("deployed", 3),
                             ("capacity", 5), ("coins", 61), ("synergy_tokens", 1)):
            with self.subTest(field=field):
                replay = self.replay(self.saved_visit() + ["relic-preparation"])
                with patch.object(BrawlVision, "preparation_panel", side_effect=[PANEL, replace(PANEL, **{field: value})]), \
                        self.assertRaisesRegex(ObservationError, "saved preparation changed"):
                    replay.task.read()
                self.assertEqual([a[1] for a in replay.actions].count("LOBBY_CONTINUE"), 1)
        replay = self.replay(self.saved_visit() + ["challenge-opening"])
        with patch.object(BrawlVision, "preparation_panel", return_value=PANEL), \
                self.assertRaisesRegex(ObservationError, "saved preparation changed"):
            replay.task.read()

    def test_missing_talent_or_unconfirmed_return_times_out_without_duplicate_actions(self):
        broken = frame("relic-harmony").copy()
        broken[219:222, 467:492] = 0
        for frames, actions in (([broken] * 45, []),
                                (["relic-harmony"] * 45, ["BACK"]),
                                (["relic-lobby"] * 45, ["LOBBY_RELICS"]),
                                (["relic-earth"] * 45, ["HARMONY_TAB"]),
                                (["relic-preparation"] * 45, ["PAUSE_BUTTON"]),
                                (["relic-preparation"] + ["relic-paused"] * 60,
                                 ["PAUSE_BUTTON", "SAVE_EXIT"]),
                                (self.saved_visit() + ["relic-return-lobby"] * 45,
                                 ["PAUSE_BUTTON", "SAVE_EXIT", "LOBBY_RELICS", "HARMONY_TAB", "BACK", "LOBBY_CONTINUE"])):
            with self.subTest(actions=actions):
                replay = self.replay(frames)
                with patch.object(BrawlVision, "preparation_panel", return_value=PANEL), \
                        self.assertRaisesRegex(ObservationError, "verified return"):
                    replay.task.read()
                self.assert_actions(replay, actions)

    def test_unexpected_scene_and_unread_initial_panel_never_authorize_battle(self):
        replay = self.replay(["relic-harmony"] * 12 + ["combat"])
        with self.assertRaisesRegex(ObservationError, "unexpected scene"):
            replay.task.read()
        self.assert_actions(replay, ["BACK"])
        replay = self.replay(["relic-harmony"] * 12 + ["relic-paused"])
        with self.assertRaisesRegex(ObservationError, "unexpected pause menu"):
            replay.task.read()
        self.assert_actions(replay, ["BACK"])
        replay = self.replay(["relic-preparation"] * 45)
        with patch.object(BrawlVision, "preparation_panel", side_effect=ObservationError("unread panel")), \
                self.assertRaisesRegex(ObservationError, "unread panel"):
            replay.task.read()
        self.assert_actions(replay, [])

    def test_partial_observation_breaks_the_required_two_complete_frames(self):
        broken = frame("relic-harmony").copy()
        broken[219:222, 467:492] = 0
        replay = self.replay(["relic-harmony", broken, "relic-harmony", "relic-harmony"]
                             + ["relic-return-lobby"])
        self.assertEqual(replay.task.read(), BONUSES)
        self.assertEqual(replay.actions[0][:2], (3, "BACK"))
