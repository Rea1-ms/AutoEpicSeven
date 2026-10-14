import unittest

import numpy as np

from module.config import server

server.set_lang("global_cn")

from tasks.brawl.assets.assets_brawl_actions import (  # noqa: E402
    TRANSMIT_COIN_ICON,
    TRANSMIT_TOKEN_ICON,
)
from tasks.brawl.cursor import Cursor, read_cursor  # noqa: E402
from tasks.brawl.rules import Resources  # noqa: E402
from tasks.brawl.scanner import BrawlBenchScanner, ScanOutcome  # noqa: E402
from tasks.brawl.transmit import (  # noqa: E402
    BrawlTransmit,
    TransmitOutcome,
    capture_selection,
    read_transmit_panel,
)
from tasks.brawl.vision import (  # noqa: E402
    BrawlVision,
    ObservationError,
    PreparationPanel,
)
from tests.support.brawl import BrawlReplay, frame  # noqa: E402
from tests.support.offline import ControlledClock  # noqa: E402


class MultiTransmitReadingTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")

    def test_real_batch_quotes_do_not_change_balances_before_confirmation(self):
        for sample, quote in (("multi-transmit-empty", (0, 0)), ("multi-transmit-one", (3, 2)),
                              ("multi-transmit-two", (8, 7)), ("multi-transmit-three", (11, 9))):
            with self.subTest(sample=sample):
                self.frame = frame(sample)
                panel = read_transmit_panel(self.frame)
                self.assertEqual(panel.quote, Resources(*quote))
                self.assertEqual(panel.header, PreparationPanel(21, 9, 10, 10, 37, 0))
                self.assertEqual(panel.selected, sample != "multi-transmit-empty")
                self.assertEqual(read_cursor(self.frame), Cursor())
        before = BrawlVision(frame("multi-transmit-before")).observe().panel
        self.assertEqual(before, panel.header)

    def test_multi_digit_quote_requires_current_unique_icons(self):
        original = frame("multi-transmit-three")
        for area in ((99, 574, 124, 599), (173, 574, 197, 599), (129, 569, 169, 605)):
            self.frame = original.copy()
            x1, y1, x2, y2 = area
            self.frame[y1:y2, x1:x2] = 0
            with self.subTest(area=area), self.assertRaises(ObservationError):
                read_transmit_panel(self.frame)
        self.frame = original.copy()
        self.frame[574:599, 133:158] = original[574:599, 99:124]
        with self.assertRaisesRegex(ObservationError, "ambiguous"):
            read_transmit_panel(self.frame)

    def test_previous_matches_never_shift_the_new_two_digit_quote(self):
        self.frame = frame("multi-transmit-three")
        expected = read_transmit_panel(self.frame)
        try:
            TRANSMIT_COIN_ICON.load_offset((12, -5))
            TRANSMIT_TOKEN_ICON.load_offset((-9, 7))
            self.assertEqual(read_transmit_panel(self.frame), expected)
        finally:
            TRANSMIT_COIN_ICON.load_offset((0, 0))
            TRANSMIT_TOKEN_ICON.load_offset((0, 0))

    def test_actual_return_and_toast_both_show_exact_batch_proceeds(self):
        before = BrawlVision(frame("multi-transmit-before")).observe().panel
        for sample in ("multi-transmit-proceeds", "multi-transmit-return"):
            self.frame = frame(sample)
            panel = BrawlVision(self.frame).observe().panel
            self.assertEqual((panel.coins - before.coins, panel.synergy_tokens - before.synergy_tokens), (11, 9))
            self.assertEqual((panel.round_number, panel.level, panel.deployed, panel.capacity), (21, 9, 10, 10))


class MultiTransmitReplayTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")
        self.clock = ControlledClock()
        self.clock.__enter__()
        self.addCleanup(self.clock.__exit__, None, None, None)
        self.selected = frame("multi-transmit-three")
        self.selection = capture_selection(self.selected)

    def test_captured_three_hero_batch_verifies_both_currencies(self):
        replay = BrawlReplay(BrawlTransmit, [self.selected] * 8 + ["multi-transmit-proceeds"], self.clock)
        self.frame = replay.image
        self.assertEqual(replay.task.confirm_selected(self.selection), TransmitOutcome.CONFIRMED)
        self.assertEqual([action[0] for action in replay.actions], [0, 7])

    def test_changed_batch_or_third_portrait_aborts_before_confirming(self):
        changed = self.selected.copy()
        changed[100:115, 191:206] = 0
        for image in (frame("multi-transmit-two"), changed):
            replay = BrawlReplay(BrawlTransmit, [image], self.clock)
            self.frame = image
            self.assertEqual(replay.task.confirm_selected(self.selection), TransmitOutcome.SELECTION_CHANGED)
            self.assertEqual(replay.actions, [])

    def test_paid_summon_result_is_not_a_captured_free_transmit_reward(self):
        # Synthetic interruption uses a real summon image; it is deliberately
        # not registered as proof that this transmission granted that hero.
        replay = BrawlReplay(BrawlTransmit, [self.selected, "lower-summon-result"], self.clock)
        self.frame = replay.image
        self.assertEqual(replay.task.confirm_selected(self.selection), TransmitOutcome.SCENE_CHANGED)
        self.assertEqual([action[1] for action in replay.actions], ["TRANSMIT_CONFIRM"])
        self.assertEqual(replay.index, 1)

    def test_bench_scanner_never_clicks_a_card_in_the_transmit_scene(self):
        replay = BrawlReplay(BrawlBenchScanner, ["multi-transmit-empty"], self.clock)
        self.frame = replay.image
        result = replay.task.scan()
        self.assertEqual(result.outcome, ScanOutcome.SCENE_CHANGED)
        self.assertFalse(result.coverage_complete)
        self.assertEqual(replay.actions, [])

    def test_unread_quote_waits_without_selecting_any_bench_hero(self):
        unread = self.selected.copy()
        unread[569:605, 129:169] = 0
        replay = BrawlReplay(BrawlTransmit, [unread, self.selected, "multi-transmit-return"], self.clock)
        self.frame = replay.image
        self.assertEqual(replay.task.confirm_selected(self.selection), TransmitOutcome.CONFIRMED)
        self.assertEqual([action[1] for action in replay.actions], ["TRANSMIT_CONFIRM"])
        self.assertEqual(replay.actions[0][0], 1)

    def test_unknown_frame_does_not_reuse_a_selected_batch(self):
        replay = BrawlReplay(BrawlTransmit, [np.zeros((720, 1280, 3), np.uint8),
                                           self.selected, "multi-transmit-return"], self.clock)
        self.frame = replay.image
        self.assertEqual(replay.task.confirm_selected(self.selection), TransmitOutcome.CONFIRMED)
        self.assertEqual([action[0] for action in replay.actions], [1])
