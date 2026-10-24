import unittest
from dataclasses import replace
from unittest.mock import patch

import numpy as np

from module.config import server

server.set_lang("global_cn")

from tasks.base.assets.assets_base_popup import TOUCH_TO_CLOSE  # noqa: E402
from tasks.brawl.assets.assets_brawl import HERO_RESULT_CHECK  # noqa: E402
from tasks.brawl.heroes import HeroResult, read_hero_result  # noqa: E402
from tasks.brawl.normal_summon import (  # noqa: E402
    BrawlNormalSummon,
    NormalSummonRequest,
    capture_normal_summon,
)
from tasks.brawl.rules import Imprint, Resources  # noqa: E402
from tasks.brawl.summon import SummonOutcome, _result_balances  # noqa: E402
from tasks.brawl.vision import (  # noqa: E402
    BrawlMode,
    BrawlVision,
    ObservationError,
    PreparationPanel,
)
from tests.support.brawl import BrawlReplay, frame  # noqa: E402
from tests.support.offline import ControlledClock  # noqa: E402


class NormalSummonReadingTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")

    def test_real_button_and_coin_budget_require_a_visible_affordable_action(self):
        self.frame = frame("normal-opening")
        request = capture_normal_summon(self.frame, reserve_coins=60)
        self.assertEqual(request, NormalSummonRequest(PreparationPanel(1, 2, 0, 3, 70, 0),
                                                      BrawlMode.NORMAL, 60))
        with self.assertRaises(ObservationError):
            capture_normal_summon(self.frame, reserve_coins=61)
        self.frame = self.frame.copy()
        self.frame[647:699, 442:587] = 0
        with self.assertRaisesRegex(ObservationError, "not visibly available"):
            capture_normal_summon(self.frame)

    def test_capture_rejects_invalid_reserves_nonpreparation_and_insufficient_coins(self):
        self.frame = frame("normal-opening")
        for reserve in (-1, True, 1.5):
            with self.subTest(reserve=reserve), self.assertRaises(ValueError):
                capture_normal_summon(self.frame, reserve)
        for sample in ("hero-start-03", "normal-deployed"):
            self.frame = frame(sample)
            with self.subTest(sample=sample), self.assertRaises(ObservationError):
                capture_normal_summon(self.frame)


class NormalSummonReplayTests(unittest.TestCase):
    """Real action/result anchors with an explicitly simulated starting panel.

    The photographed opening and first bench card are separated by an upgrade
    and several draws. They are not a recorded single purchase. The starting
    counters below deliberately model level three with twenty coins; the hero
    result and its following ten-coin preparation retain their real readers.
    """

    def setUp(self):
        server.set_lang("global_cn")
        self.clock = ControlledClock()
        self.clock.__enter__()
        self.addCleanup(self.clock.__exit__, None, None, None)
        self.before = frame("normal-opening")
        self.after = frame("normal-first-summon")
        self.hero_image = frame("hero-start-03")
        self.panel = PreparationPanel(1, 3, 0, 4, 20, 0)
        self.request = NormalSummonRequest(self.panel, BrawlMode.NORMAL)
        self.hero = HeroResult("卡萝", Imprint.D, None)
        self.frame = self.before

    def replay(self, images, seconds=.5):
        return BrawlReplay(BrawlNormalSummon, images, self.clock, seconds=seconds)

    def play(self, replay, *, after=None, request=None, **kwargs):
        actual = BrawlVision.preparation_panel

        def read_panel(vision):
            if np.array_equal(vision.image, self.before):
                return self.panel
            if after is not None and np.array_equal(vision.image, self.after):
                return after
            return actual(vision)

        with patch("tasks.brawl.normal_summon.BrawlVision.preparation_panel", autospec=True,
                   side_effect=read_panel):
            return replay.task.summon_once(request or self.request, **kwargs)

    def test_ten_coin_purchase_reads_real_hero_and_closes_once(self):
        # Carrot's weapon obscures the mode title. Paid provenance, the exact
        # result debit and a visible footer support only this result dismissal;
        # the following preparation still independently confirms the same mode.
        self.assertIsNone(BrawlVision(self.hero_image).observe().game_mode)
        replay = self.replay([self.before] + [self.hero_image] * 16 + [self.after])
        report = self.play(replay)
        self.assertEqual(report.outcome, SummonOutcome.CONFIRMED)
        self.assertEqual(report.hero, self.hero)
        self.assertEqual([(a[0], a[1]) for a in replay.actions],
                         [(0, "SUMMON_NORMAL"), (7, "TOUCH_TO_CLOSE")])
        self.assertEqual(BrawlVision(replay.image).observe().panel.coins, 10)

    def test_reported_christy_result_closes_once_without_repeated_ocr(self):
        # Only the result is the reported capture. Start/return counters model
        # one draw after the level-four upgrade; they are not a recorded video.
        original = frame("normal-summon-christy")
        self.panel = PreparationPanel(4, 4, 4, 5, 30, 0)
        after = replace(self.panel, coins=20)
        request = NormalSummonRequest(self.panel, BrawlMode.NORMAL)
        expected_hero = HeroResult("克莉丝缇", Imprint.D, None)
        for arrow_visible in (True, False):
            with self.subTest(arrow_visible=arrow_visible):
                self.hero_image = original.copy()
                if not arrow_visible:
                    # Only the arrow is removed; the real continuation text
                    # remains a valid fallback at its artwork-aware threshold.
                    self.hero_image[678:704, 622:656] = 0
                self.assertEqual(TOUCH_TO_CLOSE.match_template(self.hero_image, similarity=0.85),
                                 arrow_visible)
                self.assertEqual(read_hero_result(self.hero_image), expected_hero)
                self.assertTrue(BrawlVision(self.hero_image).matches(HERO_RESULT_CHECK))
                replay = self.replay([self.before] + [self.hero_image] * 16 + [self.after])
                with patch("tasks.brawl.normal_summon.read_hero_result", wraps=read_hero_result) as hero_reader, \
                        patch("tasks.brawl.normal_summon._result_balances",
                              wraps=_result_balances) as balance_reader:
                    report = self.play(replay, after=after, request=request)
                self.assertEqual(report.outcome, SummonOutcome.CONFIRMED)
                self.assertEqual(report.hero, expected_hero)
                close = "TOUCH_TO_CLOSE" if arrow_visible else "HERO_RESULT_CHECK"
                self.assertEqual([(a[0], a[1]) for a in replay.actions],
                                 [(0, "SUMMON_NORMAL"), (7, close)])
                if arrow_visible:
                    self.assertEqual(replay.actions[1][2], (628, 681, 651, 696))
                hero_reader.assert_called_once()
                balance_reader.assert_called_once()

    def test_waiting_for_payment_interval_reads_panel_only_when_action_is_ready(self):
        replay = self.replay([self.before] * 8 + [self.hero_image] * 7 + [self.after])
        replay.task.interval_reset("BrawlBenchScan", interval=3)
        actual = BrawlVision.preparation_panel
        reads = []

        def read_panel(vision):
            reads.append(replay.index)
            return self.panel if np.array_equal(vision.image, self.before) else actual(vision)

        with patch.object(BrawlVision, "preparation_panel", autospec=True, side_effect=read_panel):
            report = replay.task.summon_once(self.request)
        self.assertEqual(report.outcome, SummonOutcome.CONFIRMED)
        self.assertEqual([(a[0], a[1]) for a in replay.actions],
                         [(7, "SUMMON_NORMAL"), (14, "TOUCH_TO_CLOSE")])
        self.assertEqual(reads, [7, 15])

    def test_reused_result_is_invalidated_by_balance_change_or_missing_badge(self):
        for balance_changed in (True, False):
            with self.subTest(balance_changed=balance_changed):
                changed = self.hero_image.copy()
                if balance_changed:
                    changed[80:83, 1110:1113] = 0
                else:
                    changed[382:422, 771:845] = 0
                replay = self.replay([self.before, self.hero_image] + [changed] * 34)
                with patch("tasks.brawl.normal_summon._result_balances",
                           side_effect=[(10, 0)] + [(9, 0)] * 34) as reader:
                    report = self.play(replay)
                self.assertEqual(report.outcome, SummonOutcome.BALANCE_MISMATCH if balance_changed
                                 else SummonOutcome.TIMEOUT)
                self.assertEqual(reader.call_count, 2 if balance_changed else 1)
                self.assertEqual([a[1] for a in replay.actions], ["SUMMON_NORMAL"])

    def test_shared_interval_delays_both_payment_and_result_close(self):
        replay = self.replay([self.before] * 8 + [self.hero_image] * 7 + [self.after])
        replay.task.interval_reset("BrawlBenchScan", interval=3)
        self.assertEqual(self.play(replay).outcome, SummonOutcome.CONFIRMED)
        self.assertEqual([(a[0], a[1]) for a in replay.actions],
                         [(7, "SUMMON_NORMAL"), (14, "TOUCH_TO_CLOSE")])

    def test_stale_preparation_never_repeats_payment(self):
        replay = self.replay([self.before] * 34)
        self.assertEqual(self.play(replay).outcome, SummonOutcome.TIMEOUT)
        self.assertEqual([a[1] for a in replay.actions], ["SUMMON_NORMAL"])

    def test_exact_debit_without_a_result_never_confirms_purchase(self):
        replay = self.replay([self.before] + [self.after] * 34)
        report = self.play(replay)
        self.assertEqual(report.outcome, SummonOutcome.TIMEOUT)
        self.assertIsNone(report.hero)
        self.assertEqual([a[1] for a in replay.actions], ["SUMMON_NORMAL"])

    def test_result_at_entry_has_no_paid_provenance(self):
        replay = self.replay([self.hero_image])
        self.assertEqual(self.play(replay).outcome, SummonOutcome.SCENE_CHANGED)
        self.assertEqual(replay.actions, [])

    def test_unread_result_cannot_disappear_into_preparation(self):
        replay = self.replay([self.before, self.hero_image, self.after])
        with patch("tasks.brawl.normal_summon.read_hero_result", return_value=None) as reader:
            report = self.play(replay)
        reader.assert_called_once()
        self.assertEqual(report.outcome, SummonOutcome.SCENE_CHANGED)
        self.assertEqual([a[1] for a in replay.actions], ["SUMMON_NORMAL"])

    def test_missing_footer_preserves_read_result_without_clicking(self):
        actual = BrawlVision.matches

        def matches(vision, button, **kwargs):
            return False if button is HERO_RESULT_CHECK else actual(vision, button, **kwargs)

        replay = self.replay([self.before] + [self.hero_image] * 34)
        with patch("tasks.brawl.normal_summon.BrawlVision.matches", autospec=True, side_effect=matches), \
                patch.object(TOUCH_TO_CLOSE, "match_template", return_value=False):
            report = self.play(replay)
        self.assertEqual(report.outcome, SummonOutcome.TIMEOUT)
        self.assertEqual(report.hero, self.hero)
        self.assertEqual([a[1] for a in replay.actions], ["SUMMON_NORMAL"])

    def test_wrong_debit_or_token_change_never_dismisses_result(self):
        for balances in ((9, 0), (21, 0), (10, 1)):
            with self.subTest(balances=balances):
                replay = self.replay([self.before, self.hero_image])
                with patch("tasks.brawl.normal_summon._result_balances", return_value=balances):
                    report = self.play(replay)
                self.assertEqual(report.outcome, SummonOutcome.BALANCE_MISMATCH)
                self.assertEqual([a[1] for a in replay.actions], ["SUMMON_NORMAL"])

    def test_old_balance_waits_without_closing_or_repaying(self):
        replay = self.replay([self.before] + [self.hero_image] * 34)
        with patch("tasks.brawl.normal_summon._result_balances", return_value=(20, 0)):
            self.assertEqual(self.play(replay).outcome, SummonOutcome.TIMEOUT)
        self.assertEqual([a[1] for a in replay.actions], ["SUMMON_NORMAL"])

    def test_changed_mode_or_preparation_context_stops(self):
        replay = self.replay([self.before, "challenge-combat"])
        self.assertEqual(self.play(replay).outcome, SummonOutcome.CONTEXT_CHANGED)
        self.assertEqual([a[1] for a in replay.actions], ["SUMMON_NORMAL"])
        replay = self.replay([self.before] + [self.hero_image] * 7 + [self.after])
        self.assertEqual(self.play(replay, after=replace(self.panel, coins=10, round_number=2)).outcome,
                         SummonOutcome.CONTEXT_CHANGED)
        self.assertEqual([a[1] for a in replay.actions], ["SUMMON_NORMAL", "TOUCH_TO_CLOSE"])

    def test_additional_different_hero_result_remains_visible(self):
        replay = self.replay([self.before, self.hero_image, "hero-start-10"], seconds=4)
        report = self.play(replay)
        self.assertEqual(report.outcome, SummonOutcome.RESULT_UNSUPPORTED)
        self.assertEqual([a[1] for a in replay.actions], ["SUMMON_NORMAL", "TOUCH_TO_CLOSE"])

    def test_full_imprint_overflow_confirms_only_a_measured_refund(self):
        overflow = frame("hero-new-08")
        before = PreparationPanel(1, 3, 0, 4, 126, 35)
        self.panel = before
        request = NormalSummonRequest(before, BrawlMode.NORMAL)
        after = PreparationPanel(1, 3, 0, 4, 123, 45)
        replay = self.replay([self.before] + [overflow] * 16 + [self.after])
        report = self.play(replay, after=after, request=request)
        self.assertEqual(report.outcome, SummonOutcome.CONFIRMED)
        self.assertEqual(report.hero, HeroResult("小春", Imprint.SSS, Imprint.SSS, True))
        self.assertEqual(report.proceeds, Resources(7, 10))
        self.assertEqual([(a[0], a[1]) for a in replay.actions],
                         [(0, "SUMMON_NORMAL"), (7, "TOUCH_TO_CLOSE")])

        # A max-imprint result with no actual credit must not pass as an
        # ordinary ten-coin draw, and a negative delta must stop immediately.
        replay = self.replay([self.before] + [overflow] * 16 + [self.after] * 40, seconds=1)
        report = self.play(replay, after=PreparationPanel(1, 3, 0, 4, 116, 35), request=request)
        self.assertEqual(report.outcome, SummonOutcome.TIMEOUT)
        self.assertIsNone(report.proceeds)
        replay = self.replay([self.before] + [overflow] * 16 + [self.after])
        report = self.play(replay, after=PreparationPanel(1, 3, 0, 4, 115, 35), request=request)
        self.assertEqual(report.outcome, SummonOutcome.BALANCE_MISMATCH)
        self.assertIsNone(report.proceeds)

    def test_slow_confirmed_preparation_takes_priority_over_timeout(self):
        replay = self.replay([self.before, self.hero_image, self.after], seconds=10)
        screenshot = replay.screenshot

        def advance():
            # Independent frame timing: result at ten seconds, then a twenty
            # second delay before preparation, beyond the reset close timeout.
            replay.seconds = 20 if replay.index == 1 else 10
            screenshot()

        replay.screenshot = advance
        self.assertEqual(self.play(replay).outcome, SummonOutcome.CONFIRMED)
        self.assertEqual([a[1] for a in replay.actions], ["SUMMON_NORMAL", "TOUCH_TO_CLOSE"])

    def test_fresh_first_frame_cannot_pay_against_stale_capture(self):
        replay = self.replay([self.before, self.after])
        report = self.play(replay, skip_first_screenshot=False)
        self.assertEqual(report.outcome, SummonOutcome.CONTEXT_CHANGED)
        self.assertEqual(replay.actions, [])

    def test_missing_mode_on_unknown_or_returned_preparation_never_confirms_or_clicks(self):
        unknown = np.zeros((720, 1280, 3), np.uint8)
        replay = self.replay([unknown] * 7, seconds=4)
        self.assertEqual(self.play(replay).outcome, SummonOutcome.TIMEOUT)
        self.assertEqual(replay.actions, [])
        replay = self.replay([self.before] + [unknown] * 7, seconds=4)
        self.assertEqual(self.play(replay).outcome, SummonOutcome.TIMEOUT)
        self.assertEqual([a[1] for a in replay.actions], ["SUMMON_NORMAL"])

        actual = BrawlVision.observe

        def observe(vision, **kwargs):
            result = actual(vision, **kwargs)
            return replace(result, game_mode=None) if np.array_equal(vision.image, self.after) else result

        replay = self.replay([self.before, self.hero_image] + [self.after] * 7, seconds=4)
        with patch("tasks.brawl.normal_summon.BrawlVision.observe", autospec=True, side_effect=observe):
            self.assertEqual(self.play(replay).outcome, SummonOutcome.TIMEOUT)
        self.assertEqual([a[1] for a in replay.actions], ["SUMMON_NORMAL", "TOUCH_TO_CLOSE"])

    def test_occluded_preparation_ends_read_and_unread_result_ownership(self):
        actual = BrawlVision.observe

        def observe(vision, **kwargs):
            observed = actual(vision, **kwargs)
            return replace(observed, game_mode=None) if np.array_equal(vision.image, self.after) else observed

        # Both subcases positively reach preparation with an unreadable title.
        # An unread result must stop there; an acknowledged result may wait for
        # mode recovery, but a later result cannot inherit the earlier payment.
        for readable, last_frame, actions in (
                (False, 2, ["SUMMON_NORMAL"]),
                (True, 3, ["SUMMON_NORMAL", "TOUCH_TO_CLOSE"])):
            with self.subTest(readable=readable):
                replay = self.replay([self.before, self.hero_image, self.after]
                                     + [self.hero_image] * 7, seconds=4)
                with patch("tasks.brawl.normal_summon.BrawlVision.observe", autospec=True,
                           side_effect=observe), patch("tasks.brawl.normal_summon.read_hero_result",
                                                      return_value=self.hero if readable else None) as reader:
                    report = self.play(replay)
                self.assertEqual(report.outcome, SummonOutcome.SCENE_CHANGED)
                self.assertEqual(report.hero, self.hero if readable else None)
                self.assertEqual(replay.index, last_frame)
                reader.assert_called_once()
                self.assertEqual([a[1] for a in replay.actions], actions)
