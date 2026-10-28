import unittest
from dataclasses import replace
from itertools import pairwise
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from module.base.timer import Timer
from module.config import server
from module.device.device import Device

server.set_lang("global_cn")

from tasks.brawl.assets.assets_brawl import HERO_RESULT_CHECK  # noqa: E402
from tasks.brawl.combat import BrawlCombat, RoundOutcome, SpeedOutcome  # noqa: E402
from tasks.brawl.cursor import Cursor  # noqa: E402
from tasks.brawl.heroes import HeroResult  # noqa: E402
from tasks.brawl.rules import Imprint  # noqa: E402
from tasks.brawl.vision import (  # noqa: E402
    BrawlMode,
    BrawlObservation,
    BrawlScreen,
    BrawlVision,
    PreparationPanel,
)
from tests.support.brawl import BrawlReplay, frame  # noqa: E402
from tests.support.offline import ControlledClock, record_action  # noqa: E402


class SpeedReplay:
    """Screenshots advance independently; a click never changes the next frame."""

    def __init__(self, frames, clock, seconds=0.5):
        self.frames = [frame(item) if isinstance(item, str) else item for item in frames]
        self.index = 0
        self.image = self.frames[0]
        self.clock = clock
        self.seconds = seconds
        self.actions = []
        self.task = object.__new__(BrawlCombat)
        self.task.config = SimpleNamespace()
        self.task.device = self
        self.task.interval_timer = {}

    def screenshot(self):
        self.index += 1
        if self.index >= len(self.frames):
            raise AssertionError("Replay exhausted before speed was positively confirmed or timed out")
        self.clock.advance(self.seconds)
        self.image = self.frames[self.index]

    def click(self, button):
        action = (self.index, str(button), tuple(button.button))
        self.actions.append(action)
        record_action(action)


class SpeedControlTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")
        self.clock = ControlledClock()
        self.clock.__enter__()
        self.addCleanup(self.clock.__exit__, None, None, None)

    def replay(self, frames, seconds=0.5):
        result = SpeedReplay(frames, self.clock, seconds)
        self.frame = result.image
        return result

    def test_already_double_speed_exits_without_click(self):
        for name in ("combat-speed-two", "normal-opening"):
            with self.subTest(sample=name):
                replay = self.replay([name])
                self.assertEqual(replay.task.ensure_double_speed(), SpeedOutcome.ENABLED)
                self.assertEqual(replay.index, 0)
                self.assertEqual(replay.actions, [])

    def test_single_speed_retries_after_interval_and_waits_for_confirmation(self):
        replay = self.replay(["combat-speed-one"] * 11 + ["combat-speed-two"])
        self.assertEqual(replay.task.ensure_double_speed(), SpeedOutcome.ENABLED)
        self.assertEqual(replay.index, 11)
        self.assertEqual(replay.actions, [
            (0, "SPEED_ONE_CHECK", (1111, 14, 1151, 53)),
            (7, "SPEED_ONE_CHECK", (1111, 14, 1151, 53)),
        ])

    def test_click_without_visual_confirmation_times_out(self):
        replay = self.replay(["combat-speed-one"] * 25)
        self.assertEqual(replay.task.ensure_double_speed(), SpeedOutcome.TIMEOUT)
        self.assertEqual(replay.index, 21)
        self.assertEqual([item[0] for item in replay.actions], [0, 7, 14])
        self.assertTrue(all(item[1] == "SPEED_ONE_CHECK" for item in replay.actions))

    def test_unknown_speed_never_clicks_or_reports_success(self):
        self.frame = frame("combat-speed-one").copy()
        self.frame[14:53, 1111:1151] = 0
        replay = self.replay([self.frame] * 25)
        self.assertEqual(replay.task.ensure_double_speed(), SpeedOutcome.TIMEOUT)
        self.assertEqual(replay.actions, [])

    def test_unknown_scene_waits_for_positive_double_speed(self):
        unknown = np.zeros((720, 1280, 3), dtype=np.uint8)
        replay = self.replay([unknown, "coin-tooltip", "token-tooltip", "income-tooltip", "combat-speed-two"])
        self.assertEqual(replay.task.ensure_double_speed(), SpeedOutcome.ENABLED)
        self.assertEqual(replay.index, 4)
        self.assertEqual(replay.actions, [])

    def test_slow_double_speed_frame_confirms_before_local_timeout(self):
        replay = self.replay(["combat-speed-one", "combat-speed-two"], seconds=11)
        self.assertEqual(replay.task.ensure_double_speed(), SpeedOutcome.ENABLED)
        self.assertEqual(replay.index, 1)
        self.assertEqual(len(replay.actions), 1)

    def test_overlays_and_results_leave_speed_control_without_clicking(self):
        for name in ("give-up", "upgrade", "hero-result", "probabilities", "transmit",
                     "round-clear", "round-failed", "normal-victory15", "challenge-defeat",
                     "formation", "lobby", "rank-up15", "hero-unlock"):
            with self.subTest(sample=name):
                replay = self.replay([name])
                self.assertEqual(replay.task.ensure_double_speed(), SpeedOutcome.SCENE_CHANGED)
                self.assertFalse(replay.task.handle_double_speed())
                self.assertEqual(replay.actions, [])

    def test_handler_returns_true_only_for_click_and_keeps_interval(self):
        replay = self.replay(["combat-speed-one", "combat-speed-two"])
        self.assertTrue(replay.task.handle_double_speed())
        self.assertFalse(replay.task.handle_double_speed())
        self.clock.advance(3)
        self.assertFalse(replay.task.handle_double_speed())
        self.clock.advance(0.1)
        self.assertTrue(replay.task.handle_double_speed())
        replay.screenshot()
        self.assertFalse(replay.task.handle_double_speed())
        self.assertEqual(len(replay.actions), 2)

    def test_fresh_first_screenshot_ignores_stale_single_speed(self):
        replay = self.replay(["combat-speed-one", "combat-speed-two"])
        self.assertEqual(replay.task.ensure_double_speed(skip_first_screenshot=False), SpeedOutcome.ENABLED)
        self.assertEqual(replay.index, 1)
        self.assertEqual(replay.actions, [])

    def test_page_change_after_click_does_not_claim_speed_success(self):
        replay = self.replay(["combat-speed-one", "round-clear"])
        self.assertEqual(replay.task.ensure_double_speed(), SpeedOutcome.SCENE_CHANGED)
        self.assertEqual(replay.index, 1)
        self.assertEqual(len(replay.actions), 1)


class RoundFlowTests(unittest.TestCase):
    """Captured scene anchors in synthetic sequences, never real battle history.

    The source preparation captures have different field identities. These
    tests verify round boundaries and visible counters only; the formation
    controller must separately reconcile heroes after the returned boundary.
    Screenshots advance with the shared replay clock, never because of clicks.
    """

    def setUp(self):
        server.set_lang("global_cn")
        self.clock = ControlledClock()
        self.clock.__enter__()
        self.addCleanup(self.clock.__exit__, None, None, None)
        self.panel = PreparationPanel(1, 3, 1, 4, 0, 0)
        self.next_panel = PreparationPanel(2, 3, 1, 4, 55, 0)

    def replay(self, frames, seconds=0.5):
        replay = BrawlReplay(BrawlCombat, frames, self.clock, seconds=seconds)
        replay.stuck_clear_frames = []
        replay.stuck_record_clear = lambda: replay.stuck_clear_frames.append(replay.index)
        self.frame = replay.image
        return replay

    def play(self, replay, panel=None, **kwargs):
        return replay.task.play_round(self.panel if panel is None else panel, BrawlMode.NORMAL, **kwargs)

    def test_start_is_single_and_next_preparation_exits_without_starting_again(self):
        replay = self.replay(["normal-deployed"] * 8 + ["combat-speed-two", "round-clear",
                             "round-clear"] + ["normal-round2"] * 3 + ["combat-speed-two"])
        result = self.play(replay)
        self.assertEqual(result.outcome, RoundOutcome.NEXT_PREPARATION, result.reason)
        self.assertEqual(result.panel, self.next_panel)
        self.assertEqual(result.hero_results, ())
        self.assertEqual(replay.index, 13)
        self.assertEqual([(a[0], a[1]) for a in replay.actions], [(0, "PREPARATION_CHECK")])

    def test_reported_network_retry_keeps_the_started_round_and_requires_real_combat(self):
        replay = self.replay(["normal-deployed"] + ["network-retry"] * 8
                             + ["normal-deployed"] * 2 + ["combat-speed-two", "round-clear"]
                             + ["normal-round2"] * 3, seconds=1)
        result = self.play(replay)
        self.assertEqual(result.outcome, RoundOutcome.NEXT_PREPARATION, result.reason)
        self.assertEqual([a[1] for a in replay.actions],
                         ["PREPARATION_CHECK", "TOUCH_TO_CLOSE", "TOUCH_TO_CLOSE"])
        retry = replay.actions[1]
        self.assertEqual([a[0] for a in replay.actions[1:]], [1, 7])
        self.assertTrue(620 <= retry[2][0] <= 650 and 460 <= retry[2][1] <= 480)
        self.assertEqual(result.panel, self.next_panel)

    def test_network_retry_is_bounded_and_never_restarts_or_clicks_a_missing_arrow(self):
        missing = frame("network-retry").copy()
        missing[455:495, 620:665] = 0
        for overlay in (frame("network-retry"), missing):
            with self.subTest(arrow=overlay is not missing):
                replay = self.replay(["normal-deployed"] + [overlay] * 40, seconds=1)
                result = self.play(replay)
                self.assertEqual(result.outcome, RoundOutcome.FAILED)
                self.assertIn("Network retry", result.reason)
                self.assertEqual(sum(a[1] == "PREPARATION_CHECK" for a in replay.actions), 1)
                retries = [a for a in replay.actions if a[1] == "TOUCH_TO_CLOSE"]
                self.assertTrue(all(b[0] - a[0] >= 5 for a, b in pairwise(retries)))
                if overlay is missing:
                    self.assertEqual(retries, [])

    def test_network_reappearance_does_not_renew_budget_or_own_an_unrequested_round(self):
        replay = self.replay(["network-retry"])
        result = self.play(replay)
        self.assertEqual(result.outcome, RoundOutcome.FAILED)
        self.assertIn("before this call started", result.reason)
        self.assertEqual(replay.actions, [])
        replay = self.replay(["normal-deployed"] + ["network-retry"] * 14
                             + ["normal-deployed"] + ["network-retry"] * 40, seconds=1)
        result = self.play(replay)
        self.assertEqual(result.outcome, RoundOutcome.FAILED)
        self.assertIn("Network retry", result.reason)
        self.assertLess(replay.index, 35)
        self.assertEqual(sum(a[1] == "PREPARATION_CHECK" for a in replay.actions), 1)
        self.assertEqual(replay.stuck_clear_frames, [])

    def test_disconnect_leaves_the_tracked_round_without_login_or_retry(self):
        popup = frame("network-retry")
        replay = self.replay(["normal-deployed", popup], seconds=1)
        # No disconnected capture is claimed: explicitly simulate the shared
        # disconnect match on this overlay, with the production loop intact.
        with patch("tasks.brawl.vision.NETWORK_ERROR_DISCONNECT.match_template",
                   side_effect=lambda image, **kwargs: np.array_equal(image, popup)):
            result = self.play(replay)
        self.assertEqual(result.outcome, RoundOutcome.FAILED)
        self.assertIn("Network disconnected", result.reason)
        self.assertEqual([a[1] for a in replay.actions], ["PREPARATION_CHECK"])

    def test_resumed_combat_ends_the_retry_episode_before_a_later_outage(self):
        replay = self.replay(["normal-deployed"] + ["network-retry"] * 5
                             + ["combat-speed-two"] * 32 + ["network-retry"] * 5
                             + ["combat-speed-two", "round-clear"] + ["normal-round2"] * 3, seconds=1)
        result = self.play(replay)
        self.assertEqual(result.outcome, RoundOutcome.NEXT_PREPARATION, result.reason)
        self.assertEqual([a[1] for a in replay.actions],
                         ["PREPARATION_CHECK", "TOUCH_TO_CLOSE", "TOUCH_TO_CLOSE"])
        self.assertGreater(replay.actions[-1][0] - replay.actions[1][0], 30)

    def test_start_and_reward_close_share_the_previous_formation_interval(self):
        # The registered case now checks that a previous formation timer does
        # not postpone a verified start or an owned, readable reward close.
        replay = self.replay(["normal-deployed"] * 8 + ["combat-speed-two"] * 3
                             + ["hero-start-10"] * 5 + ["normal-round2"] * 3)
        replay.task.interval_reset("BrawlBenchScan", interval=3)
        result = self.play(replay)
        self.assertEqual(result.outcome, RoundOutcome.NEXT_PREPARATION, result.reason)
        self.assertEqual(result.hero_results, (HeroResult("萝季", Imprint.C, None),))
        self.assertEqual([(a[0], a[1]) for a in replay.actions],
                         [(0, "PREPARATION_CHECK"), (11, "HERO_RESULT_CHECK")])

    def test_start_without_combat_response_times_out_without_another_start(self):
        replay = self.replay(["normal-deployed"] * 35)
        result = self.play(replay)
        self.assertEqual(result.outcome, RoundOutcome.FAILED, result.reason)
        self.assertEqual([a[1] for a in replay.actions], ["PREPARATION_CHECK"])
        self.assertIsNone(result.panel)

    def test_long_combat_and_temporary_unknown_frames_have_no_short_battle_deadline(self):
        unknown = np.zeros((720, 1280, 3), np.uint8)
        replay = self.replay(["normal-deployed", "combat-speed-two", "combat-speed-two",
                             unknown, "combat-speed-two", "round-clear"] + ["normal-round2"] * 3, seconds=30)
        # Keep the long fight and temporary unread interval, but provide the
        # automatic return and settled preparation inside their local deadline.
        # A 30-second gap after the banner would instead test an expired return.
        actual_screenshot = replay.screenshot

        def screenshot():
            replay.seconds = 0.5 if replay.index >= 5 else 30
            actual_screenshot()

        replay.screenshot = screenshot
        result = self.play(replay)
        self.assertEqual(result.outcome, RoundOutcome.NEXT_PREPARATION, result.reason)
        self.assertEqual(result.panel, self.next_panel)
        self.assertEqual(replay.index, 8)
        self.assertEqual(replay.stuck_clear_frames, [1, 2, 4])
        self.assertEqual([a[1] for a in replay.actions], ["PREPARATION_CHECK"])

    def test_positive_combat_keeps_real_device_watchdog_alive_without_clearing_unknown_frames(self):
        unknown = np.zeros((720, 1280, 3), np.uint8)
        replay = self.replay(["normal-deployed"] + ["combat-speed-two"] * 72 + [unknown] * 4
                             + ["combat-speed-two", "round-clear"] + ["normal-round2"] * 3, seconds=1)
        # Bind only the real device watchdog to this offline replay. No Device
        # instance, screenshot backend or application connection is constructed.
        # The synthetic timeline exceeds both the 60-second and 60-check guards.
        replay.detect_record = set()
        replay.stuck_timer = Timer(60, count=60).start()
        replay.app_is_running = lambda: True
        actual_screenshot = replay.screenshot
        checked_frames = []

        def clear_stuck_record():
            replay.stuck_clear_frames.append(replay.index)
            Device.stuck_record_clear(replay)

        def screenshot():
            checked_frames.append(replay.index)
            Device.stuck_record_check(replay)
            actual_screenshot()

        replay.stuck_record_clear = clear_stuck_record
        replay.screenshot = screenshot
        result = self.play(replay)
        self.assertEqual(result.outcome, RoundOutcome.NEXT_PREPARATION, result.reason)
        self.assertEqual(result.panel, self.next_panel)
        self.assertEqual(replay.index, 80)
        self.assertEqual(checked_frames, list(range(80)))
        self.assertEqual(replay.stuck_clear_frames, list(range(1, 73)) + [77])
        self.assertEqual([a[1] for a in replay.actions], ["PREPARATION_CHECK"])

    def test_persistently_unread_battle_stops_without_inventing_a_round_result(self):
        unknown = np.zeros((720, 1280, 3), np.uint8)
        replay = self.replay(["normal-deployed", "combat-speed-two"] + [unknown] * 8, seconds=20)
        result = self.play(replay)
        self.assertEqual(result.outcome, RoundOutcome.FAILED, result.reason)
        self.assertEqual(result.reason, "The brawl scene remained continuously unread")
        self.assertIsNone(result.panel)
        self.assertEqual(result.hero_results, ())
        self.assertEqual([a[1] for a in replay.actions], ["PREPARATION_CHECK"])

    def test_failed_round_returns_same_round_without_clicking_result_or_retry(self):
        replay = self.replay(["normal-round2", "combat-speed-two", "round-failed",
                             "round-failed"] + ["normal-after-loss"] * 3 + ["combat-speed-two"])
        result = self.play(replay, self.next_panel)
        self.assertEqual(result.outcome, RoundOutcome.NEXT_PREPARATION, result.reason)
        self.assertEqual(result.panel, replace(self.next_panel, coins=70))
        self.assertTrue(result.reason)
        self.assertEqual(replay.index, 6)
        self.assertEqual([a[1] for a in replay.actions], ["PREPARATION_CHECK"])

    def test_same_round_without_failed_result_cannot_confirm_return(self):
        replay = self.replay(["normal-deployed", "combat-speed-two"] + ["normal-deployed"] * 35)
        self.assertEqual(self.play(replay).outcome, RoundOutcome.FAILED)
        self.assertEqual([a[1] for a in replay.actions], ["PREPARATION_CHECK"])

    def test_changed_preparation_or_rewards_without_combat_evidence_are_not_accepted(self):
        for name in ("normal-round2", "hero-start-10"):
            with self.subTest(sample=name):
                replay = self.replay(["normal-deployed", name])
                result = self.play(replay)
                self.assertEqual(result.outcome, RoundOutcome.FAILED, result.reason)
                self.assertEqual(result.hero_results, ())
                self.assertEqual([a[1] for a in replay.actions], ["PREPARATION_CHECK"])

    def test_fast_round_can_confirm_from_positive_end_scene_without_a_combat_frame(self):
        for ending, returned, expected in (
                ("round-clear", "normal-round2", RoundOutcome.NEXT_PREPARATION),
                ("round-failed", "normal-deployed", RoundOutcome.NEXT_PREPARATION),
                ("normal-victory15", None, RoundOutcome.RUN_ENDED),
                ("normal-defeat", None, RoundOutcome.RUN_ENDED)):
            with self.subTest(ending=ending):
                names = ["normal-deployed", ending] + ([returned] * 3 if returned else [])
                replay = self.replay(names)
                result = self.play(replay)
                self.assertEqual(result.outcome, expected)
                self.assertEqual([a[1] for a in replay.actions], ["PREPARATION_CHECK"])

    def test_unstarted_combat_results_and_rewards_never_authorize_any_click(self):
        for name in ("combat-speed-two", "round-clear", "round-failed", "hero-start-10",
                     "normal-victory15", "normal-defeat"):
            with self.subTest(sample=name):
                replay = self.replay([name])
                result = self.play(replay)
                self.assertEqual(result.outcome, RoundOutcome.FAILED, result.reason)
                self.assertEqual(result.hero_results, ())
                self.assertEqual(replay.actions, [])
                self.assertEqual(replay.stuck_clear_frames, [])

    def test_changed_initial_panel_or_combat_mode_stops_before_further_actions(self):
        replay = self.replay(["normal-deployed"])
        self.assertEqual(self.play(replay, replace(self.panel, coins=1)).outcome, RoundOutcome.FAILED)
        self.assertEqual(replay.actions, [])
        replay = self.replay(["normal-deployed", "challenge-combat"])
        self.assertEqual(self.play(replay).outcome, RoundOutcome.FAILED)
        self.assertEqual([a[1] for a in replay.actions], ["PREPARATION_CHECK"])
        self.assertEqual(replay.stuck_clear_frames, [])

    def test_skipped_round_or_untracked_level_and_deployment_changes_fail(self):
        actual_reader = BrawlVision.preparation_panel
        for changed in (replace(self.next_panel, round_number=3),
                        replace(self.next_panel, level=5, capacity=6),
                        replace(self.next_panel, deployed=2)):
            with self.subTest(panel=changed):
                replay = self.replay(["normal-deployed", "combat-speed-two", "normal-round2"])

                def read_panel(vision, changed=changed, replay=replay):
                    # Deliberate counter variants on a captured preparation
                    # anchor; these are not additional captured observations.
                    return changed if vision.image is replay.frames[-1] else actual_reader(vision)

                with patch("tasks.brawl.combat.BrawlVision.preparation_panel", autospec=True,
                           side_effect=read_panel):
                    self.assertEqual(self.play(replay).outcome, RoundOutcome.FAILED)
                self.assertEqual([a[1] for a in replay.actions], ["PREPARATION_CHECK"])

    def test_next_round_may_automatically_gain_one_level_and_matching_capacity(self):
        replay = self.replay(["normal-deployed", "combat-speed-two"] + ["normal-round2"] * 3)
        actual_reader = BrawlVision.preparation_panel
        upgraded = replace(self.next_panel, level=4, capacity=5)

        def read_panel(vision):
            # This explicit one-level counter change is synthetic. It models
            # automatic progress, not an unobserved paid upgrade transaction.
            observed = actual_reader(vision)
            return upgraded if observed.round_number == 2 else observed

        with patch("tasks.brawl.combat.BrawlVision.preparation_panel", autospec=True, side_effect=read_panel):
            result = self.play(replay)
        self.assertEqual(result.outcome, RoundOutcome.NEXT_PREPARATION, result.reason)
        self.assertEqual(result.panel, upgraded)
        self.assertEqual([a[1] for a in replay.actions], ["PREPARATION_CHECK"])

    def test_speed_change_preserves_the_shared_start_and_reward_action_interval(self):
        # Speed retries retain their own interval; neither the battle start
        # nor the owned reward is delayed by that unrelated button's timer.
        replay = self.replay(["normal-deployed"] + ["combat-speed-one"] * 7 + ["combat-speed-two"]
                             + ["hero-start-10"] * 7 + ["normal-round2"] * 3)
        result = self.play(replay)
        self.assertEqual(result.outcome, RoundOutcome.NEXT_PREPARATION, result.reason)
        self.assertEqual([(a[0], a[1]) for a in replay.actions],
                         [(0, "PREPARATION_CHECK"), (1, "SPEED_ONE_CHECK"), (9, "HERO_RESULT_CHECK")])

    def test_fast_distinct_rewards_and_speed_use_verified_states_without_shared_wait(self):
        replay = self.replay(["normal-deployed", "combat-speed-one", "combat-speed-two",
                              "hero-start-10", "hero-start-10", "hero-new-01", "hero-new-01"]
                             + ["normal-round2"] * 4, seconds=0.25)
        replay.task.interval_reset("BrawlBenchScan", interval=3)
        result = self.play(replay)
        self.assertEqual(result.outcome, RoundOutcome.NEXT_PREPARATION, result.reason)
        self.assertEqual(result.hero_results, (HeroResult("萝季", Imprint.C, None),
                                               HeroResult("萝季", Imprint.B, Imprint.C)))
        self.assertEqual([(a[0], a[1]) for a in replay.actions], [
            (0, "PREPARATION_CHECK"), (1, "SPEED_ONE_CHECK"),
            (3, "HERO_RESULT_CHECK"), (5, "HERO_RESULT_CHECK")])
        self.assertEqual(replay.index, 10)
        self.assertLess(replay.index * replay.seconds, 3)

    def test_new_hero_and_imprint_upgrade_are_recorded_once_despite_repeated_frames(self):
        replay = self.replay(["normal-deployed", "combat-speed-two", "round-clear",
                             "hero-start-10", "hero-start-10", "hero-new-01", "hero-new-01"]
                             + ["normal-round2"] * 3, seconds=4)
        result = self.play(replay)
        self.assertEqual(result.outcome, RoundOutcome.NEXT_PREPARATION, result.reason)
        self.assertEqual(result.hero_results, (HeroResult("萝季", Imprint.C, None),
                                               HeroResult("萝季", Imprint.B, Imprint.C)))
        self.assertEqual([(a[0], a[1]) for a in replay.actions],
                         [(0, "PREPARATION_CHECK"), (3, "HERO_RESULT_CHECK"), (5, "HERO_RESULT_CHECK")])
        self.assertEqual(replay.stuck_clear_frames, [1])

    def test_unread_hero_result_is_not_closed_or_assumed_acquired(self):
        replay = self.replay(["normal-deployed", "combat-speed-two"] + ["hero-start-10"] * 7, seconds=4)
        with patch("tasks.brawl.combat.read_hero_result", return_value=None) as reader:
            result = self.play(replay)
        self.assertTrue(reader.called)
        self.assertEqual(result.outcome, RoundOutcome.FAILED, result.reason)
        self.assertEqual(result.reason, "The hero reward remained unread or unclosed")
        self.assertEqual(result.hero_results, ())
        self.assertEqual([a[1] for a in replay.actions], ["PREPARATION_CHECK"])

    def test_owned_reward_with_artwork_covering_mode_is_read_and_closed_once(self):
        replay = self.replay(["normal-deployed", "combat-speed-two", "hero-start-03",
                             "hero-start-03"] + ["normal-round2"] * 3, seconds=4)
        captured = BrawlVision(replay.frames[2]).observe()
        self.assertEqual(captured.state, BrawlScreen.HERO_RESULT)
        self.assertIsNone(captured.game_mode)
        result = self.play(replay)
        self.assertEqual(result.outcome, RoundOutcome.NEXT_PREPARATION, result.reason)
        self.assertEqual(result.panel, self.next_panel)
        self.assertEqual(result.hero_results, (HeroResult("卡萝", Imprint.D, None),))
        self.assertEqual([(a[0], a[1]) for a in replay.actions],
                         [(0, "PREPARATION_CHECK"), (2, "HERO_RESULT_CHECK")])

    def test_unowned_reward_with_hidden_mode_never_authorizes_a_close(self):
        for started in (False, True):
            with self.subTest(started=started):
                replay = self.replay((["normal-deployed"] if started else [])
                                     + ["hero-start-03"] * 7, seconds=4)
                result = self.play(replay)
                self.assertEqual(result.outcome, RoundOutcome.FAILED, result.reason)
                self.assertEqual(result.reason, "The round transition remained unread")
                self.assertEqual(result.hero_results, ())
                self.assertEqual([a[1] for a in replay.actions], ["PREPARATION_CHECK"] if started else [])

    def test_owned_reward_exception_never_accepts_a_changed_mode_or_unread_return_mode(self):
        actual_observe = BrawlVision.observe
        for changed_state, mode, reason in (
                (BrawlScreen.HERO_RESULT, BrawlMode.CHALLENGE, "The brawl mode changed"),
                (BrawlScreen.PREPARATION, BrawlMode.CHALLENGE, "The brawl mode changed"),
                (BrawlScreen.PREPARATION, None, "The round transition remained unread")):
            with self.subTest(state=changed_state, mode=mode):
                replay = self.replay(["normal-deployed", "combat-speed-two", "hero-start-03"]
                                     + ["normal-round2"] * 7, seconds=4)

                def observe(vision, changed_state=changed_state, mode=mode):
                    # This is an explicit mode-recognition variant. The hero,
                    # footer, preparation panel and preceding battle stay real.
                    observed = actual_observe(vision)
                    if observed.state == changed_state and (
                            changed_state == BrawlScreen.HERO_RESULT or observed.panel.round_number == 2):
                        return replace(observed, game_mode=mode)
                    return observed

                with patch("tasks.brawl.combat.BrawlVision.observe", autospec=True, side_effect=observe):
                    result = self.play(replay)
                self.assertEqual(result.outcome, RoundOutcome.FAILED, result.reason)
                self.assertEqual(result.reason, reason)
                expected = ["PREPARATION_CHECK"]
                if changed_state == BrawlScreen.PREPARATION:
                    expected.append("HERO_RESULT_CHECK")
                    self.assertEqual(result.hero_results, (HeroResult("卡萝", Imprint.D, None),))
                else:
                    self.assertEqual(result.hero_results, ())
                self.assertEqual([a[1] for a in replay.actions], expected)

    def test_unread_reward_cannot_disappear_into_preparation_or_combat(self):
        for returned, reason in (
                ("normal-round2", "Preparation returned before the hero reward was closed"),
                ("combat-speed-two", "Combat reappeared after the round rewards began")):
            with self.subTest(returned=returned):
                replay = self.replay(["normal-deployed", "combat-speed-two", "hero-start-10", returned],
                                     seconds=4)
                with patch("tasks.brawl.combat.read_hero_result", return_value=None) as reader:
                    result = self.play(replay)
                reader.assert_called_once()
                self.assertEqual(result.outcome, RoundOutcome.FAILED, result.reason)
                self.assertEqual(result.reason, reason)
                self.assertEqual(result.hero_results, ())
                self.assertEqual([a[1] for a in replay.actions], ["PREPARATION_CHECK"])

    def test_unread_tail_after_a_verified_reward_close_can_return_to_preparation(self):
        hero = HeroResult("萝季", Imprint.C, None)
        replay = self.replay(["normal-deployed", "combat-speed-two", "hero-start-10",
                             "hero-start-10"] + ["normal-round2"] * 3, seconds=4)
        with patch("tasks.brawl.combat.read_hero_result", side_effect=(hero, None)):
            result = self.play(replay)
        self.assertEqual(result.outcome, RoundOutcome.NEXT_PREPARATION, result.reason)
        self.assertEqual(result.hero_results, (hero,))
        self.assertEqual([a[1] for a in replay.actions], ["PREPARATION_CHECK", "HERO_RESULT_CHECK"])

    def test_readable_hero_without_continue_prompt_is_not_closed_or_assumed_received(self):
        replay = self.replay(["normal-deployed", "combat-speed-two"] + ["hero-start-10"] * 7, seconds=4)
        actual_matches = BrawlVision.matches

        def matches(vision, button, **kwargs):
            # The hero heading remains real. Only the missing footer is an
            # explicit recognition variant; readable identity cannot replace it.
            return False if button is HERO_RESULT_CHECK else actual_matches(vision, button, **kwargs)

        with patch("tasks.brawl.combat.BrawlVision.matches", autospec=True, side_effect=matches):
            result = self.play(replay)
        self.assertEqual(result.outcome, RoundOutcome.FAILED, result.reason)
        self.assertEqual(result.reason, "The hero reward remained unread or unclosed")
        self.assertEqual(result.hero_results, ())
        self.assertEqual([a[1] for a in replay.actions], ["PREPARATION_CHECK"])

    def test_preparation_before_reward_close_does_not_commit_the_unclosed_hero(self):
        replay = self.replay(["normal-deployed", "combat-speed-two", "hero-start-10", "normal-round2"])
        actual_matches = BrawlVision.matches

        def matches(vision, button, **kwargs):
            # The old case relied on an unrelated three-second cooldown to
            # leave the reward unclosed. Model the actual missing permission:
            # its readable identity has no currently recognized close footer.
            return False if button is HERO_RESULT_CHECK else actual_matches(vision, button, **kwargs)

        with patch("tasks.brawl.combat.BrawlVision.matches", autospec=True, side_effect=matches):
            result = self.play(replay)
        self.assertEqual(result.outcome, RoundOutcome.FAILED, result.reason)
        self.assertEqual(result.reason, "Preparation returned before the hero reward was closed")
        self.assertEqual(result.hero_results, ())
        self.assertEqual([a[1] for a in replay.actions], ["PREPARATION_CHECK"])

    def test_failed_round13_keeps_both_rewards_and_confirms_real_same_round_return(self):
        # This is an explicit reconstruction, not a continuous recording.
        # The earlier start panel is supplied from the log; combat, failed
        # banner and Karin C->B are existing captures in a synthetic sequence.
        # Karin's old capture is from challenge mode, so only its mode is
        # explicitly supplied as normal for this synthetic reward sequence.
        # Achates B->A and the 160-coin return are the user's two new captures;
        # their names, badges, page identities and counters stay unmocked.
        before = PreparationPanel(13, 7, 8, 8, 135, 0)
        returned = PreparationPanel(13, 7, 8, 8, 160, 0)
        replay = self.replay(["normal-deployed", "combat-speed-two", "round-failed",
                              "hero-new-27", "hero-new-27",
                              "hero-reward-achates-a", "hero-reward-achates-a"]
                             + ["failed-round13-return"] * 4, seconds=0.25)
        actual_observe = BrawlVision.observe

        def observe(vision, **kwargs):
            observation = actual_observe(vision, **kwargs)
            if replay.index in (3, 4):
                self.assertEqual(observation.game_mode, BrawlMode.CHALLENGE)
                return replace(observation, game_mode=BrawlMode.NORMAL)
            return replace(observation, panel=before) if replay.index == 0 else observation

        with patch("tasks.brawl.combat.BrawlVision.observe", autospec=True, side_effect=observe):
            result = self.play(replay, panel=before)
        self.assertEqual(result.outcome, RoundOutcome.NEXT_PREPARATION, result.reason)
        self.assertEqual(result.panel, returned)
        self.assertEqual(result.hero_results, (HeroResult("卡琳", Imprint.B, Imprint.C),
                                               HeroResult("未知可能性雅卡泰丝", Imprint.A, Imprint.B)))
        self.assertEqual([(a[0], a[1]) for a in replay.actions], [
            (0, "PREPARATION_CHECK"), (3, "HERO_RESULT_CHECK"), (5, "HERO_RESULT_CHECK")])
        self.assertEqual(replay.index, 10)
        self.assertIn("failed; retry preparation confirmed", result.reason)

    def test_missed_failed_banner_uses_one_lost_heart_and_keeps_both_rewards(self):
        # The user log missed the short defeat banner. This reconstructed
        # sequence uses an older three-heart preparation with only its panel
        # replaced from that log; it is not a captured continuous battle.
        # Both the starting hearts and the user's two-heart return are read
        # from real pixels, as are the two independent hero reward identities.
        # Only Karin's mode from its older challenge capture is explicitly
        # replaced for this normal-mode sequence; production mode checks remain.
        before = PreparationPanel(13, 7, 8, 8, 135, 0)
        returned = PreparationPanel(13, 7, 8, 8, 160, 0)
        replay = self.replay(["normal-deployed", "combat-speed-two",
                              "hero-new-27", "hero-new-27",
                              "hero-reward-achates-a", "hero-reward-achates-a"]
                             + ["failed-round13-return"] * 4, seconds=0.25)
        actual_observe = BrawlVision.observe

        def observe(vision, **kwargs):
            observation = actual_observe(vision, **kwargs)
            if replay.index in (2, 3):
                self.assertEqual(observation.game_mode, BrawlMode.CHALLENGE)
                return replace(observation, game_mode=BrawlMode.NORMAL)
            return replace(observation, panel=before) if replay.index == 0 else observation

        with patch("tasks.brawl.combat.BrawlVision.observe", autospec=True, side_effect=observe):
            result = self.play(replay, panel=before)
        self.assertEqual(result.outcome, RoundOutcome.NEXT_PREPARATION, result.reason)
        self.assertEqual(result.panel, returned)
        self.assertEqual(result.hero_results, (HeroResult("卡琳", Imprint.B, Imprint.C),
                                               HeroResult("未知可能性雅卡泰丝", Imprint.A, Imprint.B)))
        self.assertEqual([(a[0], a[1]) for a in replay.actions], [
            (0, "PREPARATION_CHECK"), (2, "HERO_RESULT_CHECK"), (4, "HERO_RESULT_CHECK")])
        self.assertEqual(replay.index, 9)
        self.assertIn("lives: 3->2", result.reason)

    def test_disconnected_upgrade_chain_is_not_closed_or_added(self):
        replay = self.replay(["normal-deployed", "combat-speed-two", "hero-start-10", "hero-new-01"],
                             seconds=4)
        with patch("tasks.brawl.combat.read_hero_result", side_effect=(
                HeroResult("萝季", Imprint.C, None), HeroResult("萝季", Imprint.A, Imprint.B))):
            result = self.play(replay)
        self.assertEqual(result.outcome, RoundOutcome.FAILED, result.reason)
        self.assertEqual(result.reason, "Hero rewards do not form a consistent imprint chain")
        self.assertEqual(result.hero_results, (HeroResult("萝季", Imprint.C, None),))
        self.assertEqual([a[1] for a in replay.actions], ["PREPARATION_CHECK", "HERO_RESULT_CHECK"])

    def test_terminal_results_return_without_clicking_back_or_starting_again(self):
        for name in ("normal-victory15", "normal-defeat"):
            with self.subTest(sample=name):
                replay = self.replay(["normal-deployed", "combat-speed-two", name, "normal-round2"])
                result = self.play(replay)
                self.assertEqual(result.outcome, RoundOutcome.RUN_ENDED, result.reason)
                self.assertIsNone(result.panel)
                self.assertEqual(replay.index, 2)
                self.assertEqual([a[1] for a in replay.actions], ["PREPARATION_CHECK"])

    def test_unhandled_overlays_stop_without_clicking_through_them(self):
        for name in ("rank-up15", "hero-unlock", "give-up"):
            with self.subTest(sample=name):
                replay = self.replay(["normal-deployed", "combat-speed-two", name])
                self.assertEqual(self.play(replay).outcome, RoundOutcome.FAILED)
                self.assertEqual([a[1] for a in replay.actions], ["PREPARATION_CHECK"])

    def test_fresh_first_frame_discards_an_unowned_reward_before_starting(self):
        replay = self.replay(["hero-start-10", "normal-deployed", "combat-speed-two"] + ["normal-round2"] * 3)
        result = self.play(replay, skip_first_screenshot=False)
        self.assertEqual(result.outcome, RoundOutcome.NEXT_PREPARATION, result.reason)
        self.assertEqual(result.hero_results, ())
        self.assertEqual([(a[0], a[1]) for a in replay.actions], [(1, "PREPARATION_CHECK")])


class PostBattlePreparationTests(unittest.TestCase):
    """Explicit synthetic counters and bench readiness model the reported race."""

    def setUp(self):
        self.clock = ControlledClock()
        self.clock.__enter__()
        self.addCleanup(self.clock.__exit__, None, None, None)
        self.panel = PreparationPanel(3, 3, 4, 4, 60, 0)
        self.returned = replace(self.panel, round_number=4, coins=100)

    def replay(self, panels, *, seconds=0.25, unread_bench=()):
        images = [np.full((720, 1280, 3), index, np.uint8) for index in range(len(panels) + 2)]
        replay = BrawlReplay(BrawlCombat, images, self.clock, seconds=seconds)
        replay.stuck_record_clear = lambda: None

        def observe(image):
            index = int(image[0, 0, 0])
            if index == 0:
                return BrawlObservation(BrawlScreen.PREPARATION, self.panel, game_mode=BrawlMode.NORMAL)
            if index == 1:
                return BrawlObservation(BrawlScreen.COMBAT, game_mode=BrawlMode.NORMAL, speed=2)
            panel = panels[index - 2]
            return BrawlObservation(BrawlScreen.PREPARATION, panel, game_mode=BrawlMode.NORMAL) if panel else (
                BrawlObservation(BrawlScreen.UNKNOWN))

        def cursor(image, *, observation):
            if replay.index in unread_bench:
                return Cursor(panel=observation.panel, game_mode=observation.game_mode)
            return Cursor(top=True, bottom=True, empty=True,
                          panel=observation.panel, game_mode=observation.game_mode)

        patches = (
            patch("tasks.brawl.combat.BrawlVision", side_effect=lambda image: SimpleNamespace(
                observe=lambda: observe(image), matches=lambda *args, **kwargs: True)),
            patch("tasks.brawl.combat.read_cursor", side_effect=cursor),
        )
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        return replay

    def play(self, replay):
        return replay.task.play_round(self.panel, BrawlMode.NORMAL)

    def test_coin_countup_and_fading_empty_bench_finish_before_reconciliation(self):
        # A readable intermediate 96 persists long enough to defeat a simple
        # two-frame equality check. Its empty-bench anchor is still fading.
        panels = [replace(self.returned, coins=96)] * 4 + [self.returned] * 4
        replay = self.replay(panels, unread_bench=range(2, 6))
        result = self.play(replay)
        self.assertEqual(result.outcome, RoundOutcome.NEXT_PREPARATION, result.reason)
        self.assertEqual(result.panel, self.returned)
        self.assertEqual(result.hero_results, ())
        self.assertEqual(replay.index, 9)
        self.assertEqual([action[1] for action in replay.actions], ["PREPARATION_CHECK"])

    def test_unknown_frame_restarts_preparation_stability_without_more_actions(self):
        replay = self.replay([self.returned] * 2 + [None] + [self.returned] * 4)
        result = self.play(replay)
        self.assertEqual(result.outcome, RoundOutcome.NEXT_PREPARATION, result.reason)
        self.assertEqual(result.panel, self.returned)
        self.assertEqual(replay.index, 8)
        self.assertEqual([action[1] for action in replay.actions], ["PREPARATION_CHECK"])

    def test_changing_counters_or_unread_bench_have_a_bounded_read_only_wait(self):
        for changing in (False, True):
            with self.subTest(changing=changing):
                panels = [replace(self.returned, coins=100 + index) if changing else self.returned
                          for index in range(36)]
                replay = self.replay(panels, seconds=0.5,
                                     unread_bench=() if changing else range(2, 38))
                result = self.play(replay)
                self.assertEqual(result.outcome, RoundOutcome.FAILED, result.reason)
                self.assertEqual(result.reason, "The post-battle preparation or bench did not settle")
                self.assertIsNone(result.panel)
                self.assertEqual(result.hero_results, ())
                self.assertLess(replay.index, 37)
                self.assertEqual([action[1] for action in replay.actions], ["PREPARATION_CHECK"])

    def test_positive_settled_return_wins_over_the_same_frame_timeout(self):
        replay = self.replay([self.returned] * 2, seconds=16)
        result = self.play(replay)
        self.assertEqual(result.outcome, RoundOutcome.NEXT_PREPARATION, result.reason)
        self.assertEqual(result.panel, self.returned)
        self.assertEqual(replay.index, 3)
        self.assertEqual([action[1] for action in replay.actions], ["PREPARATION_CHECK"])

    def test_same_round_requires_a_measured_single_nonterminal_life_loss(self):
        # Explicit synthetic counts cover cases for which no complete capture
        # pair exists. Neither a reward balance nor an absolute heart count
        # may replace evidence of this particular battle losing one life.
        returned = replace(self.panel, coins=100)
        for starting, remaining in ((3, 3), (None, 2), (3, None), (3, 1), (1, 0), (2, 3)):
            with self.subTest(starting=starting, remaining=remaining):
                replay = self.replay([returned] * 36, seconds=0.5)
                with patch("tasks.brawl.combat.read_lives", side_effect=lambda *args,
                           starting=starting, remaining=remaining, replay=replay, **kwargs: (
                        starting if replay.index == 0 else remaining)):
                    result = self.play(replay)
                self.assertEqual(result.outcome, RoundOutcome.FAILED, result.reason)
                self.assertIsNone(result.panel)
                self.assertEqual(result.hero_results, ())
                self.assertLess(replay.index, 37)
                self.assertEqual([action[1] for action in replay.actions], ["PREPARATION_CHECK"])

    def test_unread_lives_restart_same_round_preparation_stability(self):
        returned = replace(self.panel, coins=100)
        replay = self.replay([returned] * 8)
        # Frames 2/3 have two lives; frame 4 is unread, so the same readable
        # panel cannot finish until a fresh 0.6-second interval after frame 5.
        with patch("tasks.brawl.combat.read_lives", side_effect=lambda *args, **kwargs: (
                3 if replay.index == 0 else None if replay.index == 4 else 2)):
            result = self.play(replay)
        self.assertEqual(result.outcome, RoundOutcome.NEXT_PREPARATION, result.reason)
        self.assertEqual(result.panel, returned)
        self.assertEqual(replay.index, 8)
        self.assertIn("lives: 3->2", result.reason)
        self.assertEqual([action[1] for action in replay.actions], ["PREPARATION_CHECK"])
