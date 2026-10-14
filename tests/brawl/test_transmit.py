import unittest
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from module.config import server

server.set_lang("global_cn")

from tasks.brawl.rules import Resources  # noqa: E402
from tasks.brawl.transmit import (  # noqa: E402
    BrawlTransmit, TransmitOutcome, capture_selection, parse_quote, read_transmit_panel,
)
from tasks.brawl.vision import BrawlVision, ObservationError  # noqa: E402
from tests.support.brawl import frame  # noqa: E402
from tests.support.offline import ControlledClock, record_action  # noqa: E402


class TransmitReplay:
    """Frame advancement is independent of clicks and recorded for failures."""

    def __init__(self, frames, clock, seconds=0.5):
        self.frames = [frame(item) if isinstance(item, str) else item for item in frames]
        self.index = 0
        self.image = self.frames[0]
        self.clock = clock
        self.seconds = seconds
        self.actions = []
        self.task = object.__new__(BrawlTransmit)
        self.task.config = SimpleNamespace()
        self.task.device = self
        self.task.interval_timer = {}

    def screenshot(self):
        self.index += 1
        if self.index >= len(self.frames):
            raise AssertionError("Transmit replay exhausted before verification or a bounded exit")
        self.clock.advance(self.seconds)
        self.image = self.frames[self.index]

    def click(self, button):
        action = (self.index, str(button), tuple(button.button))
        self.actions.append(action)
        record_action(action)


class TransmitReadingTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")

    def test_empty_and_selected_quotes_do_not_claim_a_completed_sale(self):
        self.frame = frame("transmit")
        empty = read_transmit_panel(self.frame)
        self.assertFalse(empty.selected)
        self.assertIsNone(empty.selection_fingerprint)
        self.assertEqual(empty.quote, Resources(0, 0))
        self.assertEqual((empty.header.coins, empty.header.synergy_tokens), (25, 0))
        with self.assertRaises(ObservationError):
            capture_selection(self.frame)
        self.frame = frame("transmit-selected")
        selected = read_transmit_panel(self.frame)
        self.assertTrue(selected.selected)
        self.assertEqual(selected.quote, Resources(2, 0))
        self.assertEqual((selected.header.round_number, selected.header.level, selected.header.deployed), (9, 6, 7))
        self.assertEqual((selected.header.coins, selected.header.synergy_tokens), (25, 0))
        snapshot = capture_selection(self.frame)
        self.assertEqual(snapshot.selection_fingerprint, selected.selection_fingerprint)
        with self.assertRaises(FrozenInstanceError):
            snapshot.quote = Resources(0, 0)

    def test_wrong_scenes_and_obscured_quotes_are_rejected(self):
        for name in ("transmit-return", "normal-opening", "coin-tooltip"):
            with self.subTest(sample=name), self.assertRaises(ObservationError):
                self.frame = frame(name)
                read_transmit_panel(self.frame)
        self.frame = frame("transmit-selected").copy()
        self.frame[569:605, 141:169] = 0
        with self.assertRaises(ObservationError):
            read_transmit_panel(self.frame)

    def test_quote_requires_two_confident_nonnegative_integers(self):
        self.assertEqual(parse_quote([(" 2 ", 0.99), ("0", 0.99)]), Resources(2, 0))
        self.assertEqual(parse_quote([("0", 0.99), ("12", 0.99)]), Resources(0, 12))
        for values in ([("2", 0.99)], [("2", 0.79), ("0", 0.99)],
                       [("2", float("nan")), ("0", 0.99)],
                       [("2", 0.99), ("。", 0.99)], [("-2", 0.99), ("0", 0.99)]):
            with self.subTest(values=values), self.assertRaises(ObservationError):
                parse_quote(values)

    def test_quote_model_failures_propagate(self):
        self.frame = frame("transmit-selected")
        with patch("tasks.brawl.transmit.Ocr.ocr_multi_lines", side_effect=RuntimeError("model failed")):
            with self.assertRaisesRegex(RuntimeError, "model failed"):
                read_transmit_panel(self.frame)


class TransmitConfirmationTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")
        self.clock = ControlledClock()
        self.clock.__enter__()
        self.addCleanup(self.clock.__exit__, None, None, None)
        self.selected = frame("transmit-selected")
        self.selection = capture_selection(self.selected)

    def replay(self, frames, seconds=0.5):
        replay = TransmitReplay(frames, self.clock, seconds)
        self.frame = replay.image
        return replay

    def delayed_balance(self):
        image = frame("transmit-return").copy()
        image[66:114, 1060:1264] = self.selected[66:114, 1060:1264]
        return image

    def test_repeated_selected_frames_retry_then_verify_exact_proceeds(self):
        replay = self.replay([self.selected] * 11 + ["transmit-return"])
        self.assertEqual(replay.task.confirm_selected(self.selection), TransmitOutcome.CONFIRMED)
        self.assertEqual(replay.index, 11)
        self.assertEqual(replay.actions, [
            (0, "TRANSMIT_CONFIRM", (22, 624, 179, 680)),
            (7, "TRANSMIT_CONFIRM", (22, 624, 179, 680)),
        ])

    def test_disappeared_tray_waits_for_balance_without_clicking_again(self):
        replay = self.replay([self.selected] + [self.delayed_balance()] * 8 + ["transmit-return"])
        self.assertEqual(replay.task.confirm_selected(self.selection), TransmitOutcome.CONFIRMED)
        self.assertEqual(replay.index, 9)
        self.assertEqual([action[0] for action in replay.actions], [0])

    def test_unread_return_balances_wait_but_never_allow_a_second_sale(self):
        unread = frame("transmit-return").copy()
        unread[66:114, 1060:1264] = 0
        for next_frame, expected in (("transmit-return", TransmitOutcome.CONFIRMED),
                                     (self.selected, TransmitOutcome.SCENE_CHANGED)):
            with self.subTest(expected=expected):
                replay = self.replay([self.selected, unread, next_frame])
                self.assertEqual(replay.task.confirm_selected(self.selection), expected)
                self.assertEqual(replay.index, 2)
                self.assertEqual([action[0] for action in replay.actions], [0])

    def test_unexpected_balances_are_not_accepted_as_transmit_proceeds(self):
        returned = frame("transmit-return")
        real_panel = BrawlVision.preparation_panel
        # Only the numeric observation is varied; the input images and the
        # selection/return order remain captured frames, not a synthetic sale.
        for coins, tokens in ((24, 0), (28, 0), (27, 1)):
            def read_panel(vision):
                panel = real_panel(vision)
                if vision.image is returned:
                    return replace(panel, coins=coins, synergy_tokens=tokens)
                return panel

            with self.subTest(coins=coins, tokens=tokens):
                replay = self.replay([self.selected, returned])
                with patch("tasks.brawl.transmit.BrawlVision.preparation_panel", autospec=True,
                           side_effect=read_panel):
                    self.assertEqual(replay.task.confirm_selected(self.selection), TransmitOutcome.BALANCE_MISMATCH)
                self.assertEqual([action[0] for action in replay.actions], [0])

    def test_changed_round_level_or_deployment_cannot_confirm_a_sale(self):
        returned = frame("transmit-return")
        real_panel = BrawlVision.preparation_panel
        for changes in ({"round_number": 10}, {"level": 7, "capacity": 8}, {"deployed": 6}):
            def read_panel(vision):
                panel = real_panel(vision)
                return replace(panel, **changes) if vision.image is returned else panel

            with self.subTest(changes=changes):
                replay = self.replay([self.selected, returned])
                with patch("tasks.brawl.transmit.BrawlVision.preparation_panel", autospec=True,
                           side_effect=read_panel):
                    self.assertEqual(replay.task.confirm_selected(self.selection), TransmitOutcome.SCENE_CHANGED)
                self.assertEqual([action[0] for action in replay.actions], [0])

    def test_closed_tray_without_proceeds_times_out_without_reopening(self):
        replay = self.replay([self.selected] + [self.delayed_balance()] * 24)
        self.assertEqual(replay.task.confirm_selected(self.selection), TransmitOutcome.TIMEOUT)
        self.assertEqual(replay.index, 21)
        self.assertEqual([action[0] for action in replay.actions], [0])

    def test_clicks_without_a_verified_return_time_out(self):
        replay = self.replay([self.selected] * 25)
        self.assertEqual(replay.task.confirm_selected(self.selection), TransmitOutcome.TIMEOUT)
        self.assertEqual(replay.index, 21)
        self.assertEqual([action[0] for action in replay.actions], [0, 7, 14])

    def test_changed_portrait_anywhere_in_tray_prevents_confirmation(self):
        for y, x in ((110, 45), (500, 220)):
            changed = self.selected.copy()
            changed[y:y + 5, x:x + 5] = (255, 0, 0)
            replay = self.replay([changed])
            self.assertEqual(replay.task.confirm_selected(self.selection), TransmitOutcome.SELECTION_CHANGED)
            self.assertEqual(replay.actions, [])

    def test_changed_quote_aborts_before_clicking(self):
        replay = self.replay([self.selected])
        with patch("tasks.brawl.transmit.parse_quote", return_value=Resources(3, 0)):
            self.assertEqual(replay.task.confirm_selected(self.selection), TransmitOutcome.SELECTION_CHANGED)
        self.assertEqual(replay.actions, [])

    def test_empty_selection_or_other_scene_never_gets_confirmed(self):
        for name, expected in (("transmit", TransmitOutcome.SELECTION_CHANGED),
                               ("normal-opening", TransmitOutcome.SCENE_CHANGED),
                               ("transmit-return", TransmitOutcome.SCENE_CHANGED),
                               ("round-clear", TransmitOutcome.SCENE_CHANGED)):
            with self.subTest(sample=name):
                replay = self.replay([name])
                self.assertEqual(replay.task.confirm_selected(self.selection), expected)
                self.assertEqual(replay.actions, [])

    def test_unknown_frames_wait_without_clicks_until_selection_is_known(self):
        unknown = np.zeros((720, 1280, 3), dtype=np.uint8)
        replay = self.replay([unknown, unknown, self.selected, "transmit-return"])
        self.assertEqual(replay.task.confirm_selected(self.selection), TransmitOutcome.CONFIRMED)
        self.assertEqual([action[0] for action in replay.actions], [2])

    def test_reappearing_tray_after_preparation_is_not_a_second_sale(self):
        replay = self.replay([self.selected, self.delayed_balance(), self.selected])
        self.assertEqual(replay.task.confirm_selected(self.selection), TransmitOutcome.SCENE_CHANGED)
        self.assertEqual(replay.index, 2)
        self.assertEqual([action[0] for action in replay.actions], [0])

    def test_fresh_first_frame_rejects_stale_selection_without_clicking(self):
        replay = self.replay([self.selected, "transmit-return"])
        self.assertEqual(replay.task.confirm_selected(self.selection, skip_first_screenshot=False),
                         TransmitOutcome.SCENE_CHANGED)
        self.assertEqual(replay.index, 1)
        self.assertEqual(replay.actions, [])

    def test_slow_return_still_confirms_positive_exit_before_timeout(self):
        replay = self.replay([self.selected, "transmit-return"], seconds=11)
        self.assertEqual(replay.task.confirm_selected(self.selection), TransmitOutcome.CONFIRMED)
        self.assertEqual(len(replay.actions), 1)
