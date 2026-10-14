import unittest
from dataclasses import replace
from unittest.mock import patch

import numpy as np

from module.config import server

server.set_lang("global_cn")

from tasks.brawl.heroes import HeroResult, read_hero_result  # noqa: E402
from tasks.brawl.rules import Imprint, SummonKind  # noqa: E402
from tasks.brawl.summon import (  # noqa: E402
    BrawlSummon,
    SummonOutcome,
    capture_summon,
)
from tasks.brawl.vision import BrawlVision, ObservationError  # noqa: E402
from tests.support.brawl import BrawlReplay, frame  # noqa: E402
from tests.support.offline import ControlledClock  # noqa: E402


class SynergySummonReadingTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")

    def test_two_real_results_keep_new_hero_and_existing_imprint_upgrade_distinct(self):
        for sample, expected in (("lower-summon-result", HeroResult("维多丽卡", Imprint.C, None)),
                                  ("upper-summon-result", HeroResult("黎洁特", Imprint.SS, Imprint.S))):
            self.frame = frame(sample)
            self.assertEqual(read_hero_result(self.frame), expected)
        self.frame = frame("synergy-summon-before")
        self.assertEqual(capture_summon(self.frame, SummonKind.LOWER).panel.synergy_tokens, 54)
        self.frame = frame("lower-summon-return")
        self.assertEqual(capture_summon(self.frame, SummonKind.UPPER).panel.synergy_tokens, 44)

    def test_budget_reserve_disabled_buttons_and_wrong_scenes_are_rejected(self):
        self.frame = frame("synergy-summon-before")
        self.assertEqual(capture_summon(self.frame, SummonKind.LOWER, reserve_tokens=44).reserve_tokens, 44)
        with self.assertRaises(ObservationError):
            capture_summon(self.frame, SummonKind.UPPER, reserve_tokens=15)
        for reserve in (-1, True, 1.5):
            with self.assertRaises(ValueError):
                capture_summon(self.frame, SummonKind.LOWER, reserve)
        with self.assertRaises(ValueError):
            capture_summon(self.frame, SummonKind.NORMAL)
        for sample in ("upper-summon-return", "upper-summon-stable", "lower-summon-result",
                       "multi-transmit-return", "full-swap-grid"):
            self.frame = frame(sample)
            with self.subTest(sample=sample), self.assertRaises(ObservationError):
                capture_summon(self.frame, SummonKind.LOWER)
        self.frame = frame("synergy-summon-before").copy()
        self.frame[647:699, 620:745] = 0
        with self.assertRaises(ObservationError):
            capture_summon(self.frame, SummonKind.LOWER)

    def test_unseen_actions_between_transmit_and_summons_are_not_one_balance_chain(self):
        self.frame = frame("multi-transmit-return")
        transmit = BrawlVision(self.frame).observe().panel
        summon = BrawlVision(frame("synergy-summon-before")).observe().panel
        self.assertEqual((transmit.coins, transmit.synergy_tokens), (48, 9))
        self.assertEqual((summon.coins, summon.synergy_tokens), (90, 54))
        self.assertNotEqual(transmit, summon)


class SynergySummonReplayTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")
        self.clock = ControlledClock()
        self.clock.__enter__()
        self.addCleanup(self.clock.__exit__, None, None, None)
        self.before = frame("synergy-summon-before")
        self.request = capture_summon(self.before, SummonKind.LOWER)

    def replay(self, images, seconds=.5):
        replay = BrawlReplay(BrawlSummon, images, self.clock, seconds=seconds)
        self.frame = replay.image
        return replay

    def test_lower_then_upper_draws_verify_exact_debits_and_each_result(self):
        for before, result, after, kind, balance, hero in (
                ("synergy-summon-before", "lower-summon-result", "lower-summon-return",
                 SummonKind.LOWER, 44, HeroResult("维多丽卡", Imprint.C, None)),
                ("lower-summon-return", "upper-summon-result", "upper-summon-return",
                 SummonKind.UPPER, 4, HeroResult("黎洁特", Imprint.SS, Imprint.S))):
            request = capture_summon(frame(before), kind)
            replay = self.replay([before] + [result] * 7 + [after])
            report = replay.task.summon_once(request)
            self.assertEqual(report.outcome, SummonOutcome.CONFIRMED)
            self.assertEqual(report.hero, hero)
            self.assertEqual([(action[0], action[1]) for action in replay.actions],
                             [(0, "SUMMON_" + kind.name), (7, "HERO_RESULT_CHECK")])
            self.assertEqual(BrawlVision(replay.image).observe().panel.synergy_tokens, balance)

    def test_repeat_preparation_never_triggers_a_second_payment(self):
        replay = self.replay([self.before] * 34)
        self.assertEqual(replay.task.summon_once(self.request).outcome, SummonOutcome.TIMEOUT)
        self.assertEqual([action[1] for action in replay.actions], ["SUMMON_LOWER"])

    def test_exact_debit_without_a_read_result_does_not_confirm_a_draw(self):
        replay = self.replay([self.before] + ["lower-summon-return"] * 34)
        report = replay.task.summon_once(self.request)
        self.assertEqual(report.outcome, SummonOutcome.TIMEOUT)
        self.assertIsNone(report.hero)
        self.assertEqual([action[1] for action in replay.actions], ["SUMMON_LOWER"])

    def test_result_at_entry_has_no_payment_provenance_and_is_not_dismissed(self):
        replay = self.replay(["lower-summon-result"])
        self.assertEqual(replay.task.summon_once(self.request).outcome, SummonOutcome.SCENE_CHANGED)
        self.assertEqual(replay.actions, [])

    def test_unknown_frames_and_hidden_result_balances_wait_without_extra_payment(self):
        result = frame("lower-summon-result").copy()
        result[66:114, 1010:1264] = 0
        unknown = np.zeros((720, 1280, 3), np.uint8)
        replay = self.replay([self.before, unknown] + [result] * 6
                             + ["lower-summon-result", "lower-summon-return"])
        self.assertEqual(replay.task.summon_once(self.request).outcome, SummonOutcome.CONFIRMED)
        self.assertEqual([(a[0], a[1]) for a in replay.actions],
                         [(0, "SUMMON_LOWER"), (8, "HERO_RESULT_CHECK")])

    def test_wrong_debit_or_coin_change_prevents_result_dismissal(self):
        for balances in ((90, 43), (91, 44), (89, 44), (90, 55)):
            replay = self.replay([self.before, "lower-summon-result"])
            with patch("tasks.brawl.summon._result_balances", return_value=balances):
                self.assertEqual(replay.task.summon_once(self.request).outcome, SummonOutcome.BALANCE_MISMATCH)
            self.assertEqual([action[1] for action in replay.actions], ["SUMMON_LOWER"])

    def test_old_result_balance_does_not_authorize_a_dismissal(self):
        replay = self.replay([self.before] + ["lower-summon-result"] * 34)
        with patch("tasks.brawl.summon._result_balances", return_value=(90, 54)):
            self.assertEqual(replay.task.summon_once(self.request).outcome, SummonOutcome.TIMEOUT)
        self.assertEqual([action[1] for action in replay.actions], ["SUMMON_LOWER"])

    def test_additional_different_result_stops_without_guessing_a_buff(self):
        replay = self.replay([self.before, "lower-summon-result", "upper-summon-result"])
        self.assertEqual(replay.task.summon_once(self.request).outcome, SummonOutcome.RESULT_UNSUPPORTED)
        self.assertEqual([action[1] for action in replay.actions], ["SUMMON_LOWER"])

    def test_overflow_result_is_preserved_without_guessing_its_refund(self):
        # Deliberate synthetic interruption; this old overflow capture is not
        # represented as an actual result of the new lower summon sequence.
        replay = self.replay([self.before, "hero-new-08"])
        self.assertEqual(replay.task.summon_once(self.request).outcome, SummonOutcome.RESULT_UNSUPPORTED)
        self.assertEqual([action[1] for action in replay.actions], ["SUMMON_LOWER"])

    def test_previous_action_interval_is_honored_before_a_new_payment(self):
        replay = self.replay([self.before] * 8 + ["lower-summon-result"] * 7 + ["lower-summon-return"])
        replay.task.interval_reset(replay.task.ACTION_TIMER, interval=replay.task.ACTION_INTERVAL)
        self.assertEqual(replay.task.summon_once(self.request).outcome, SummonOutcome.CONFIRMED)
        self.assertEqual([(a[0], a[1]) for a in replay.actions],
                         [(7, "SUMMON_LOWER"), (14, "HERO_RESULT_CHECK")])

    def test_preparation_boundary_prevents_dismissing_a_later_hero(self):
        unread = frame("lower-summon-return").copy()
        unread[66:114, 1010:1264] = 0
        replay = self.replay([self.before, "lower-summon-result", unread, "lower-summon-result"])
        self.assertEqual(replay.task.summon_once(self.request).outcome, SummonOutcome.SCENE_CHANGED)
        self.assertEqual([action[1] for action in replay.actions], ["SUMMON_LOWER"])

    def test_changed_request_context_aborts_without_paying(self):
        replay = self.replay(["lower-summon-return"])
        self.assertEqual(replay.task.summon_once(self.request).outcome, SummonOutcome.CONTEXT_CHANGED)
        self.assertEqual(replay.actions, [])
        with self.assertRaises(ValueError):
            replay.task.summon_once(replace(self.request, reserve_tokens=55))

    def test_changed_deployment_cannot_confirm_a_paid_draw(self):
        real = BrawlVision.preparation_panel
        returned = frame("lower-summon-return")

        def read_panel(vision):
            panel = real(vision)
            return replace(panel, deployed=9) if vision.image is returned else panel

        replay = self.replay([self.before, "lower-summon-result", returned])
        with patch("tasks.brawl.summon.BrawlVision.preparation_panel", autospec=True, side_effect=read_panel):
            self.assertEqual(replay.task.summon_once(self.request).outcome, SummonOutcome.CONTEXT_CHANGED)
        self.assertEqual([action[1] for action in replay.actions], ["SUMMON_LOWER"])

    def test_slow_exact_return_confirms_before_timeout(self):
        replay = self.replay([self.before, "lower-summon-result", "lower-summon-return"], seconds=10)
        self.assertEqual(replay.task.summon_once(self.request).outcome, SummonOutcome.CONFIRMED)
        self.assertEqual([action[1] for action in replay.actions], ["SUMMON_LOWER", "HERO_RESULT_CHECK"])
