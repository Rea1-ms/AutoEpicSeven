import unittest
from dataclasses import replace
from unittest.mock import patch

import numpy as np

from module.config import server

server.set_lang("global_cn")

from module.ocr.ocr import Ocr  # noqa: E402
from tasks.brawl.assets.assets_brawl_actions import (  # noqa: E402
    TRANSMIT_COIN_ICON,
    TRANSMIT_TOKEN_ICON,
)
from tasks.brawl.auto_transfer import (  # noqa: E402
    BrawlAutoTransfer,
    TransferPreview,
    read_transfer_cards,
    read_transfer_icons,
)
from tasks.brawl.bench import BenchHero  # noqa: E402
from tasks.brawl.cursor import Cursor, read_cursor  # noqa: E402
from tasks.brawl.heroes import HeroResult  # noqa: E402
from tasks.brawl.name_cache import bench_name_cache  # noqa: E402
from tasks.brawl.rules import Imprint, Resources  # noqa: E402
from tasks.brawl.scanner import (  # noqa: E402
    BenchScanResult,
    BrawlBenchScanner,
    ScanOutcome,
)
from tasks.brawl.transmit import (  # noqa: E402
    BrawlTransmit,
    TransmitOutcome,
    TransmitPanel,
    capture_selection,
    read_transmit_panel,
)
from tasks.brawl.vision import (  # noqa: E402
    BrawlMode,
    BrawlObservation,
    BrawlScreen,
    BrawlVision,
    ObservationError,
    PreparationPanel,
)
from tests.support.brawl import BrawlReplay, frame  # noqa: E402
from tests.support.offline import ControlledClock, record_action  # noqa: E402


class MultiTransmitReadingTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")

    def test_static_transfer_names_reuse_text_but_selected_cards_and_positions_stay_current(self):
        samples = ("multi-transmit-empty", "multi-transmit-one", "multi-transmit-two")
        expected = [read_transfer_cards(frame(sample)) for sample in samples]
        self.assertNotEqual(expected[0], expected[1])
        original = Ocr.ocr_multi_lines
        reads = []

        def recognize(ocr, images):
            result = original(ocr, images)
            if ocr.name == "BRAWL_TRANSFER_NAMES":
                # Unknown artwork, partial neighboring names and badges still
                # need independent OCR. Count only complete current identities
                # when proving that static hero names are not recognized twice.
                reads.extend(text for text, score in result
                             if score >= .8 and text in set().union(*expected))
            return result

        with bench_name_cache(), patch.object(Ocr, "ocr_multi_lines", recognize):
            for sample, cards in zip(samples, expected):
                self.frame = frame(sample)
                self.assertEqual(read_transfer_cards(self.frame), cards)
                count = len(reads)
                self.assertEqual(read_transfer_cards(self.frame.copy()), cards)
                self.assertEqual(len(reads), count)
            self.frame = frame("multi-transmit-empty")
            self.assertEqual(read_transfer_cards(self.frame), expected[0])
        self.assertTrue(reads)

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


class AutoTransferReplayTests(unittest.TestCase):
    """Real tray transitions with an explicitly supplied complete bench scan.

    These captures show three adjacent selections and their actual proceeds.
    The complete bench identities are annotated inputs; the preparation bench
    scanner is not bypassed in production. Gift cases below reuse a separately
    photographed gift with simulated balances, not a recorded sale/gift chain.
    """

    NAMES = ("卡琳", "西风处刑者修里", "玫勒赛德丝")
    BENCH = NAMES + ("利纳柯", "最后的骑士克劳乌", "实验体赛兹", "指挥型莱伊卡", "战斗型玛雅")
    HEADER = PreparationPanel(21, 9, 10, 10, 37, 0)

    def setUp(self):
        server.set_lang("global_cn")
        self.clock = ControlledClock()
        self.clock.__enter__()
        self.addCleanup(self.clock.__exit__, None, None, None)

    def annotated_scan(self):
        # The transfer selector consumes names, not imprint-derived prices.
        # Imprints are explicit legal model values, not screenshot assertions.
        heroes = tuple(BenchHero(name, Imprint.B) for name in self.BENCH)
        return BenchScanResult(heroes, self.HEADER, BrawlMode.NORMAL, ScanOutcome.COMPLETE)

    def finish_replay(self, images, seconds=4):
        selected = frame("multi-transmit-three")
        panel = read_transmit_panel(selected)
        replay = BrawlReplay(BrawlAutoTransfer, images, self.clock, seconds=seconds)
        preview = TransferPreview(panel.header, panel.game_mode, self.NAMES, panel.quote,
                                  panel.selection_fingerprint)
        # Isolate completion ownership from selection in these boundary cases.
        # The full selection-to-completion case below obtains this in production.
        replay.task._pending_preview = preview
        self.frame = replay.image
        return replay, preview

    def test_real_three_hero_selection_then_exact_proceeds_confirms_once(self):
        replay = BrawlReplay(BrawlAutoTransfer,
                             ["multi-transmit-before"] + ["multi-transmit-empty"] * 2
                             + ["multi-transmit-one"] * 2 + ["multi-transmit-two"] * 2
                             + ["multi-transmit-three"] * 3 + ["multi-transmit-proceeds"],
                             self.clock, seconds=4)
        self.frame = replay.image
        preview = replay.task.preview(self.NAMES, self.annotated_scan())
        self.assertEqual(preview.names, self.NAMES)
        self.assertEqual(preview.header, self.HEADER)
        self.assertEqual(preview.quote, Resources(11, 9))
        self.assertEqual(replay.index, 7)
        report = replay.task.finish(preview, commit=True)
        self.assertTrue(report.committed)
        self.assertEqual(report.names, self.NAMES)
        self.assertEqual(report.hero_results, ())
        self.assertEqual(report.panel, replace(self.HEADER, coins=48, synergy_tokens=9))
        self.assertEqual([(a[0], a[1]) for a in replay.actions],
                         [(0, "TRANSMIT_ENTER"), (2, "BRAWL_TRANSFER_SELECT"),
                          (4, "BRAWL_TRANSFER_SELECT"), (6, "BRAWL_TRANSFER_SELECT"),
                          (7, "TRANSMIT_CONFIRM")])
        for action, sample, name in zip(replay.actions[1:4],
                                        ("multi-transmit-empty", "multi-transmit-one", "multi-transmit-two"),
                                        self.NAMES):
            self.assertEqual(action[2], read_transfer_cards(frame(sample))[name])

    def test_each_selected_portrait_remains_identical_while_names_leave_the_bench(self):
        previous = ()
        for count, sample in enumerate(("multi-transmit-empty", "multi-transmit-one",
                                        "multi-transmit-two", "multi-transmit-three")):
            with self.subTest(sample=sample):
                self.frame = frame(sample)
                icons = read_transfer_icons(self.frame)
                cards = read_transfer_cards(self.frame)
                self.assertEqual(len(icons), count)
                self.assertTrue(set(previous).issubset(icons))
                self.assertTrue(set(self.NAMES[:count]).isdisjoint(cards))
                if count < len(self.NAMES):
                    self.assertIn(self.NAMES[count], cards)
                previous = icons

    def test_five_modeled_selections_accumulate_quotes_before_a_single_confirmation(self):
        # No five-portrait screenshot was captured. Explicit model observations
        # exercise all five production loop transitions and their action gate;
        # the separate three-hero case retains the real visual readers.
        images = [np.full((720, 1280, 3), index, np.uint8) for index in range(14)]
        replay = BrawlReplay(BrawlAutoTransfer, images, self.clock, seconds=4)
        self.frame = replay.image
        names = self.BENCH[:5]
        quotes = tuple(Resources(*values) for values in ((0, 0), (3, 2), (8, 7),
                                                         (11, 9), (16, 14), (21, 19)))
        portraits = tuple((50 + index * 40, 128, name) for index, name in enumerate(names))

        def selected_count():
            return min(5, max(0, (replay.index - 1) // 2))

        def observation(_vision):
            if replay.index in (0, 13):
                panel = self.HEADER if replay.index == 0 else replace(self.HEADER, coins=58, synergy_tokens=19)
                return BrawlObservation(BrawlScreen.PREPARATION, panel, game_mode=BrawlMode.NORMAL)
            return BrawlObservation(BrawlScreen.TRANSMIT_SELECTION, game_mode=BrawlMode.NORMAL)

        def tray(_image):
            count = selected_count()
            return TransmitPanel(self.HEADER, quotes[count], bool(count),
                                 f"modeled-selection-{count}" if count else None, BrawlMode.NORMAL)

        def cards(_image):
            return {name: (1000, 150 + index * 40, 1170, 174 + index * 40)
                    for index, name in enumerate(names[selected_count():])}

        with patch("tasks.brawl.auto_transfer.BrawlVision.observe", autospec=True, side_effect=observation), \
                patch("tasks.brawl.auto_transfer.BrawlVision.matches", return_value=True), \
                patch("tasks.brawl.auto_transfer.read_transmit_panel", side_effect=tray), \
                patch("tasks.brawl.auto_transfer.read_transfer_cards", side_effect=cards), \
                patch("tasks.brawl.auto_transfer.read_transfer_icons",
                      side_effect=lambda _image: portraits[:selected_count()]):
            preview = replay.task.preview(names, self.annotated_scan())
            self.assertEqual(preview.names, names)
            self.assertEqual(preview.quote, Resources(21, 19))
            self.assertEqual(replay.index, 11)
            report = replay.task.finish(preview, commit=True)
        self.assertEqual(report.names, names)
        self.assertEqual(report.quote, Resources(21, 19))
        self.assertTrue(report.committed)
        self.assertEqual(report.panel, replace(self.HEADER, coins=58, synergy_tokens=19))
        self.assertEqual([(a[0], a[1]) for a in replay.actions],
                         [(0, "TRANSMIT_ENTER"), (2, "BRAWL_TRANSFER_SELECT"),
                          (4, "BRAWL_TRANSFER_SELECT"), (6, "BRAWL_TRANSFER_SELECT"),
                          (8, "BRAWL_TRANSFER_SELECT"), (10, "BRAWL_TRANSFER_SELECT"),
                          (11, "TRANSMIT_CONFIRM")])

    def test_a_swipe_that_selects_a_hero_stops_before_any_card_or_confirm_click(self):
        # A drag can be interpreted as a tap by the game. Its unowned tray
        # addition must fail even though the new portrait and quote look valid.
        images = [np.full((720, 1280, 3), index, np.uint8) for index in range(4)]
        replay = BrawlReplay(BrawlAutoTransfer, images, self.clock, seconds=4)
        self.frame = replay.image

        def swipe(start, end, name):
            action = (replay.index, name, (start, end))
            replay.actions.append(action)
            record_action(action)

        replay.swipe = swipe

        def observation(_vision):
            state = BrawlScreen.PREPARATION if replay.index == 0 else BrawlScreen.TRANSMIT_SELECTION
            return BrawlObservation(state, self.HEADER if replay.index == 0 else None,
                                    game_mode=BrawlMode.NORMAL)

        def tray(_image):
            selected = replay.index == 3
            return TransmitPanel(self.HEADER, Resources(3, 2) if selected else Resources(0, 0),
                                 selected, "unrequested-selection" if selected else None, BrawlMode.NORMAL)

        with patch("tasks.brawl.auto_transfer.BrawlVision.observe", autospec=True, side_effect=observation), \
                patch("tasks.brawl.auto_transfer.BrawlVision.matches", return_value=True), \
                patch("tasks.brawl.auto_transfer.read_transmit_panel", side_effect=tray), \
                patch("tasks.brawl.auto_transfer.read_transfer_cards",
                      return_value={"利纳柯": (1000, 250, 1100, 275)}), \
                patch("tasks.brawl.auto_transfer.read_transfer_icons",
                      side_effect=lambda _image: ((58, 128, "unrequested"),) if replay.index == 3 else ()):
            with self.assertRaisesRegex(ObservationError, "tray changed without a verified selection"):
                replay.task.preview(("卡琳",), self.annotated_scan())
        self.assertEqual(replay.index, 3)
        self.assertEqual([(a[0], a[1]) for a in replay.actions],
                         [(0, "TRANSMIT_ENTER"), (2, "BRAWL_TRANSFER_SCROLL")])

    def test_zero_six_duplicate_and_incomplete_requests_never_enter_transfer(self):
        scan = self.annotated_scan()
        for names, supplied in (((), scan), (self.BENCH[:6], scan),
                                (("卡琳", "卡琳"), scan), (list(self.NAMES), scan),
                                (self.NAMES, replace(scan, outcome=ScanOutcome.TIMEOUT))):
            with self.subTest(names=names, outcome=supplied.outcome):
                replay = BrawlReplay(BrawlAutoTransfer, ["multi-transmit-before"], self.clock)
                self.frame = replay.image
                with self.assertRaisesRegex(ObservationError, "one to five"):
                    replay.task.preview(names, supplied)
                self.assertEqual(replay.actions, [])
        # Five is an admissible request size, but an existing unowned tray is
        # still rejected. This is an input-boundary check, not five-sale proof.
        replay = BrawlReplay(BrawlAutoTransfer, ["multi-transmit-three"], self.clock)
        with self.assertRaisesRegex(ObservationError, "existing tray"):
            replay.task.preview(self.BENCH[:5], scan)
        self.assertEqual(replay.actions, [])

    def test_changed_selection_fingerprint_prevents_confirmation(self):
        changed = frame("multi-transmit-three").copy()
        changed[100:115, 191:206] = 0
        replay, preview = self.finish_replay([changed])
        with self.assertRaisesRegex(ObservationError, "selection changed"):
            replay.task.finish(preview, commit=True)
        self.assertEqual(replay.actions, [])

    def test_cancel_returns_unchanged_balances_without_confirming(self):
        replay, preview = self.finish_replay(["multi-transmit-three"] * 3 + ["multi-transmit-before"])
        report = replay.task.finish(preview, commit=False)
        self.assertFalse(report.committed)
        self.assertEqual(report.panel, self.HEADER)
        self.assertEqual(report.hero_results, ())
        self.assertEqual([a[1] for a in replay.actions], ["TRANSMIT_CHECK"])

    def test_cancellation_cannot_collect_an_unrelated_gift(self):
        replay, preview = self.finish_replay(["multi-transmit-three", "transmit-gift-result"])
        with patch("tasks.brawl.auto_transfer.read_hero_result") as reader, \
                self.assertRaisesRegex(ObservationError, "not attributable"):
            replay.task.finish(preview, commit=False)
        reader.assert_not_called()
        self.assertEqual([a[1] for a in replay.actions], ["TRANSMIT_CHECK"])

    def test_repeated_gift_frames_close_once_then_verify_exact_return(self):
        replay, preview = self.finish_replay(["multi-transmit-three"]
                                             + ["transmit-gift-result"] * 4 + ["multi-transmit-return"])
        with patch("tasks.brawl.auto_transfer._result_balances", return_value=(48, 9)):
            report = replay.task.finish(preview, commit=True)
        self.assertEqual(report.hero_results, (HeroResult("黎洁特", Imprint.S, Imprint.A),))
        self.assertEqual(report.panel, replace(self.HEADER, coins=48, synergy_tokens=9))
        self.assertEqual([(a[0], a[1]) for a in replay.actions],
                         [(0, "TRANSMIT_CONFIRM"), (1, "HERO_RESULT_CHECK")])

    def test_unread_gift_cannot_disappear_into_a_successful_return(self):
        replay, preview = self.finish_replay(["multi-transmit-three", "transmit-gift-result",
                                             "multi-transmit-return"])
        with patch("tasks.brawl.auto_transfer.read_hero_result", return_value=None) as reader, \
                self.assertRaisesRegex(ObservationError, "disappeared before its verified close"):
            replay.task.finish(preview, commit=True)
        reader.assert_called_once()
        self.assertEqual([a[1] for a in replay.actions], ["TRANSMIT_CONFIRM"])

    def test_unclosed_gift_cannot_disappear_during_the_shared_action_interval(self):
        replay, preview = self.finish_replay(["multi-transmit-three", "transmit-gift-result",
                                             "multi-transmit-return"], seconds=.5)
        with patch("tasks.brawl.auto_transfer._result_balances", return_value=(48, 9)), \
                self.assertRaisesRegex(ObservationError, "disappeared before its verified close"):
            replay.task.finish(preview, commit=True)
        self.assertEqual([a[1] for a in replay.actions], ["TRANSMIT_CONFIRM"])

    def test_gift_balance_mismatch_preserves_the_result_without_closing(self):
        replay, preview = self.finish_replay(["multi-transmit-three", "transmit-gift-result"])
        with patch("tasks.brawl.auto_transfer._result_balances", return_value=(47, 9)), \
                self.assertRaisesRegex(ObservationError, "balances do not match"):
            replay.task.finish(preview, commit=True)
        self.assertEqual([a[1] for a in replay.actions], ["TRANSMIT_CONFIRM"])

    def test_successive_same_hero_gifts_require_a_continuous_imprint_chain(self):
        replay, preview = self.finish_replay(["multi-transmit-three", "transmit-gift-result",
                                             "upper-summon-result", "multi-transmit-return"])
        # The two result images are real A -> S and S -> SS observations, but
        # their adjacency and quoted balances are explicitly simulated here.
        with patch("tasks.brawl.auto_transfer._result_balances", return_value=(48, 9)):
            report = replay.task.finish(preview, commit=True)
        self.assertEqual(report.hero_results, (HeroResult("黎洁特", Imprint.S, Imprint.A),
                                              HeroResult("黎洁特", Imprint.SS, Imprint.S)))
        self.assertEqual([(a[0], a[1]) for a in replay.actions],
                         [(0, "TRANSMIT_CONFIRM"), (1, "HERO_RESULT_CHECK"), (2, "HERO_RESULT_CHECK")])

    def test_discontinuous_gift_chain_never_closes_the_second_result(self):
        replay, preview = self.finish_replay(["multi-transmit-three", "transmit-gift-result",
                                             "upper-summon-result"])
        results = (HeroResult("黎洁特", Imprint.S, Imprint.A),
                   HeroResult("黎洁特", Imprint.SS, Imprint.A))
        with patch("tasks.brawl.auto_transfer._result_balances", return_value=(48, 9)), \
                patch("tasks.brawl.auto_transfer.read_hero_result", side_effect=results), \
                self.assertRaisesRegex(ObservationError, "consistent imprint chain"):
            replay.task.finish(preview, commit=True)
        self.assertEqual([(a[0], a[1]) for a in replay.actions],
                         [(0, "TRANSMIT_CONFIRM"), (1, "HERO_RESULT_CHECK")])

    def test_timed_out_confirmation_consumes_its_preview_and_cannot_be_retried(self):
        replay, preview = self.finish_replay(["multi-transmit-three"] * 6)
        with self.assertRaisesRegex(ObservationError, "completion was not verified"):
            replay.task.finish(preview, commit=True)
        with self.assertRaisesRegex(ObservationError, "current preview"):
            replay.task.finish(preview, commit=True)
        self.assertEqual([a[1] for a in replay.actions], ["TRANSMIT_CONFIRM"])
