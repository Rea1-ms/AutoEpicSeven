import unittest
from types import SimpleNamespace

import numpy as np

from module.config import server

server.set_lang("global_cn")

from tasks.brawl.combat import BrawlCombat, SpeedOutcome  # noqa: E402
from tests.support.brawl import frame  # noqa: E402
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
