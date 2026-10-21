"""Overseas Chinese daily evidence, claimed state and independent list order."""

import unittest
from contextlib import ExitStack, nullcontext
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.config import server
from tests.support.daily_missions import EXPECTED as PARTIAL_EXPECTED
from tests.support.daily_missions import mission_scene, scan_frames
from tests.support.global_daily_missions import (
    CAPTURES,
    CLAIMED,
    EXPECTED,
    HOME,
    HOME_AFTER,
    MENU,
    POPUP,
    VISIBLE_KEYS,
    GlobalDailyCaptureReplay,
    GlobalDailyScanReplay,
    capture_probe,
    captured,
)
from tests.support.offline import ControlledClock


class GlobalDailyCase(unittest.TestCase):
    def setUp(self):
        previous_lang, previous_server = server.lang, server.server
        self.addCleanup(server.set_lang, previous_lang)
        self.addCleanup(setattr, server, 'server', previous_server)
        server.set_lang('global_cn')
        server.server = 'OVERSEA-Play'
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.clock = self.stack.enter_context(ControlledClock())


class GlobalDailyCaptureTests(GlobalDailyCase):
    def test_real_captures_read_six_panels_and_160_activity(self):
        for fixture, keys in zip(CAPTURES, VISIBLE_KEYS):
            with self.subTest(fixture=fixture):
                task = capture_probe(fixture)
                self.assertTrue(task._daily_page_ready())
                self.assertEqual(task._read_daily_points(), 160)
                rows = task._read_daily_missions()
                self.assertIsNotNone(rows)
                if fixture == CAPTURES[2]:
                    keys = ('core_3', 'login')
                self.assertEqual(tuple(row.key for row in rows), keys)
                self.assertEqual(
                    [(row.current, row.total, row.points) for row in rows],
                    [EXPECTED[key] for key in keys],
                )

    def test_real_captures_collect_thirteen_reordered_missions(self):
        seen = {}
        for fixture in CAPTURES:
            rows = capture_probe(fixture)._read_daily_missions()
            self.assertIsNotNone(rows, fixture)
            for row in rows:
                if row.key in seen:
                    self.assertEqual(row, seen[row.key], fixture)
                seen[row.key] = row
        self.assertEqual(len(seen), 13)
        self.assertEqual(set(seen), set(EXPECTED))
        completed = [row for row in seen.values() if row.current == row.total]
        self.assertEqual(len(completed), 7)
        self.assertEqual(sum(row.points for row in completed), 160)

    def test_gray_completed_rows_keep_full_progress_and_global_gacha_name(self):
        for fixture, keys in zip(CAPTURES[2:], VISIBLE_KEYS[2:]):
            if fixture == CAPTURES[2]:
                keys = ('core_3', 'login')
            rows = capture_probe(fixture)._read_daily_missions()
            self.assertIsNotNone(rows)
            observed = {row.key: (row.current, row.total, row.points) for row in rows}
            for key in keys:
                if EXPECTED[key][0] == EXPECTED[key][1]:
                    self.assertEqual(observed[key], EXPECTED[key])
        self.assertEqual(observed['arena_10'], (10, 10, 30))

    def test_real_before_popup_after_frames_identify_reward_state(self):
        for fixture, page_ready, chests in (
            (CAPTURES[0], True, [True] * 6),
            (POPUP, False, [False] * 6),
            (CLAIMED, True, [False] * 6),
        ):
            with self.subTest(fixture=fixture):
                task = capture_probe(fixture)
                self.assertEqual(task._daily_page_ready(), page_ready)
                self.assertEqual(
                    [button.match_color(task.device.image, threshold=task.REWARD_COLOR_THRESHOLD)
                     for button in task.REWARD_BUTTONS], chests,
                )
        task = capture_probe(CLAIMED)
        self.assertEqual(task._read_daily_points(), 160)
        self.assertIsNone(task._read_daily_missions())

    def test_real_home_and_menu_never_create_daily_mission_state(self):
        for fixture in (HOME, MENU, HOME_AFTER):
            with self.subTest(fixture=fixture):
                task = capture_probe(fixture)
                self.assertFalse(task._daily_page_ready())
                self.assertIsNone(task._read_daily_missions())


class GlobalDailyRewardReplayTests(GlobalDailyCase):
    def test_real_reward_sequence_retries_then_waits_for_claimed_page(self):
        replay = GlobalDailyCaptureReplay(
            [captured(CAPTURES[0])] * 8 + [captured(POPUP)] * 8 + [captured(CLAIMED)] * 9,
            self.clock,
        )
        self.assertEqual(replay.task._claim_daily_rewards_verified(), (True, True))
        replay.assert_retried(self, 'DAILY_POINTS_1', 2)
        replay.assert_retried(self, 'TOUCH_TO_CLOSE', 2)
        self.assertTrue(all(index < 8 for index in replay.clicks('DAILY_POINTS_1')))
        self.assertTrue(all(8 <= index < 16 for index in replay.clicks('TOUCH_TO_CLOSE')))
        self.assertGreaterEqual(replay.index, 20)
        self.assertTrue(all(name in ('DAILY_POINTS_1', 'TOUCH_TO_CLOSE') for _, name in replay.actions))
        self.assertEqual(replay.task._read_daily_points(), 160)

    def test_real_claimed_page_settles_without_clicking_close_hint(self):
        replay = GlobalDailyCaptureReplay([captured(CLAIMED)] * 10, self.clock)
        self.assertEqual(replay.task._claim_daily_rewards_verified(), (True, False))
        self.assertGreaterEqual(replay.index, 4)
        self.assertEqual(replay.actions, [])


class GlobalDailyScanTests(GlobalDailyCase):
    def test_reordered_synthetic_partial_day_scans_all_thirteen_missions(self):
        # The supplied screenshots are already at 160 activity. These explicit
        # observations model a 25-point day: every unfinished row precedes login,
        # rather than retaining the 160-point capture's completed-row order.
        panels = (
            ('core_1', 'flower', 'hero'), ('hero', 'equipment', 'core_2'),
            ('core_2', 'core_3', 'stamina_50'), ('stamina_50', 'arena_5', 'gacha'),
            ('gacha', 'stamina_100', 'stamina_150'), ('stamina_150', 'arena_10', 'login'),
        )
        replay = GlobalDailyScanReplay(scan_frames(panels), self.clock)
        task = replay.task
        task._enter_mission_reward = Mock()
        task._read_daily_points = Mock(return_value=25)
        task._claim_daily_rewards_verified = Mock(return_value=(True, False))
        task.ui_goto = Mock()
        task.config.multi_set = nullcontext
        task.config.stored = SimpleNamespace(DailyActivity=SimpleNamespace(set=Mock()))
        self.stack.enter_context(patch(
            'tasks.mission_reward.daily_reward.get_server_last_update',
            return_value=datetime.fromisoformat('2026-10-17T02:00:00'),
        ))
        self.assertTrue(task.run())
        plan = replay.values['MissionReward.MissionReward.DailyPlan']
        self.assertEqual(plan['points'], 25)
        self.assertTrue(plan['rewards_claimed'])
        self.assertEqual(
            {row['key']: (row['current'], row['total'], row['points']) for row in plan['missions']},
            PARTIAL_EXPECTED,
        )
        task.config.task_delay.assert_called_once_with(server_update=True)
        task.config.task_call.assert_not_called()
        self.assertEqual(len(replay.swipes), 5)
        self.assertTrue(all(start[1] > end[1] for _, start, end, _ in replay.swipes))
        replay.assert_swipe_intervals(self)

    def test_partial_reordered_list_never_claims_completeness(self):
        panels = tuple(tuple(key for key in keys if key != 'core_1') for keys in VISIBLE_KEYS[:3])
        frames = scan_frames(panels, expected=EXPECTED, points=160)
        frames += [mission_scene(panels[-1], expected=EXPECTED, points=160)] * 45
        replay = GlobalDailyScanReplay(frames, self.clock)
        self.assertIsNone(replay.task._scan_daily_missions(skip_first_screenshot=True))
        self.assertLess(replay.index, len(frames) - 1)
        self.assertTrue(all(action == 'swipe' for _, action in replay.actions))
        replay.assert_swipe_intervals(self)

    def test_partial_activity_infers_completed_suffix_without_reading_it(self):
        expected = {
            **PARTIAL_EXPECTED, 'gacha': (1, 1, 15),
            'flower': (1, 1, 15), 'hero': (1, 1, 15),
        }
        panels = (
            ('stamina_50', 'core_1', 'arena_5'), ('arena_5', 'equipment', 'stamina_100'),
            ('stamina_100', 'stamina_150', 'core_2'), ('core_2', 'core_3', 'arena_10'),
            ('arena_10', 'login'),
        )
        replay = GlobalDailyScanReplay(scan_frames(panels, expected=expected, points=70), self.clock)
        rows = replay.task._scan_daily_missions()
        self.assertEqual({row.key: (row.current, row.total, row.points) for row in rows}, expected)
        self.assertEqual(len(replay.swipes), 4)
        self.assertTrue(all(start[1] > end[1] for _, start, end, _ in replay.swipes))
        replay.assert_swipe_intervals(self)

    def test_real_login_boundary_infers_completed_suffix_without_scanning_it(self):
        frames = [captured(CAPTURES[0])] * 4
        frames += [captured(CAPTURES[1])] * 5 + [captured(CAPTURES[2])] * 5
        replay = GlobalDailyCaptureReplay(frames, self.clock)
        rows = replay.task._scan_daily_missions()
        self.assertEqual({row.key: (row.current, row.total, row.points) for row in rows}, EXPECTED)
        self.assertEqual(len(replay.swipes), 2)
        self.assertTrue(all(start[1] > end[1] for _, start, end, _ in replay.swipes))
        self.assertTrue(all(action == 'swipe' for _, action in replay.actions))
        self.assertLess(replay.index, len(frames) - 2)
        for earlier, later in zip(replay.swipes, replay.swipes[1:]):
            self.assertGreaterEqual(later[3] - earlier[3], 2)

    def test_fields_below_completed_login_are_not_read(self):
        task = capture_probe(CAPTURES[2])
        task.device.image[605:639, 500:600] = 0
        rows = task._read_daily_missions()
        self.assertEqual(tuple(row.key for row in rows), ('core_3', 'login'))
        self.assertEqual((rows[-1].current, rows[-1].total), (1, 1))

    def test_unreadable_activity_cannot_verify_inferred_completed_rows(self):
        frames = scan_frames(VISIBLE_KEYS[:3], expected=EXPECTED, points=None)
        frames += [mission_scene(VISIBLE_KEYS[2], expected=EXPECTED, points=None)] * 35
        replay = GlobalDailyScanReplay(frames, self.clock)
        self.assertIsNone(replay.task._scan_daily_missions())
        self.assertTrue(all(start[1] > end[1] for _, start, end, _ in replay.swipes))
