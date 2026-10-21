"""CN reward confirmation and day-bound observations without real devices."""

import unittest
from contextlib import ExitStack, nullcontext
from dataclasses import asdict
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.config import server
from tests.support.daily_missions import EXPECTED, task_type
from tests.support.interaction import InteractionReplay, scene
from tests.support.offline import ControlledClock

DAY = datetime.fromisoformat('2026-10-15T03:00:00')
PLAN_PATH = 'MissionReward.MissionReward.DailyPlan'
DAILY = 'MISSION_REWARD_DAILY_ENTRY_CHECK'
CHEST = 'DAILY_POINTS_1'


class CnDailyCase(unittest.TestCase):
    def setUp(self):
        previous_lang, previous_server = server.lang, server.server
        self.addCleanup(server.set_lang, previous_lang)
        self.addCleanup(setattr, server, 'server', previous_server)
        server.set_lang('cn')
        server.server = 'CN-Official'
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.clock = self.stack.enter_context(ControlledClock())

    def replay(self, frames):
        replay = InteractionReplay(task_type(), frames, self.clock)
        replay.task.config.Emulator_PackageName = 'com.zlongame.cn.epicseven'
        replay.task.config.Scheduler_ServerUpdate = '03:00'
        # Only the visual observation is synthetic. The production chest
        # selection, popup handlers, action intervals and completion timers run.
        for button in replay.task.REWARD_BUTTONS:
            self.stack.enter_context(patch.object(
                button, 'match_color',
                side_effect=lambda image, name=button.name, **kw: name in image['buttons'],
            ))
        return replay


class CnDailyRewardReplayTests(CnDailyCase):
    def test_empty_daily_page_requires_several_fresh_observations(self):
        replay = self.replay([scene(DAILY)] * 10)
        self.assertEqual(replay.task._claim_daily_rewards_verified(), (True, False))
        self.assertGreaterEqual(replay.index, 4)
        self.assertEqual(replay.actions, [])

    def test_ready_chest_during_cooldown_cannot_prove_claim_completion(self):
        replay = self.replay([scene(DAILY, CHEST)] * 8 + [scene(DAILY)] * 8)
        self.assertEqual(replay.task._claim_daily_rewards_verified(), (True, True))
        replay.assert_retried(self, CHEST, 2)
        self.assertTrue(all(index < 8 for index in replay.clicks(CHEST)))
        self.assertGreaterEqual(replay.index, 12)
        self.assertTrue(all(name == CHEST for _, name in replay.actions))

    def test_reward_popup_must_close_and_daily_page_must_settle(self):
        replay = self.replay(
            [scene(DAILY, CHEST)]
            + [scene('TOUCH_TO_CLOSE')] * 8
            + [scene(DAILY)] * 8
        )
        self.assertEqual(replay.task._claim_daily_rewards_verified(), (True, True))
        self.assertEqual(replay.clicks(CHEST), [0])
        replay.assert_retried(self, 'TOUCH_TO_CLOSE', 2)
        self.assertTrue(all(1 <= index < 9 for index in replay.clicks('TOUCH_TO_CLOSE')))
        self.assertGreaterEqual(replay.index, 13)

    def test_network_overlay_does_not_count_as_received_reward(self):
        replay = self.replay(
            [scene(DAILY, CHEST)]
            + [scene('NETWORK_ERROR_ABNORMAL')]
            + [scene()] * 5
            + [scene(DAILY)] * 8
        )
        self.assertEqual(replay.task._claim_daily_rewards_verified(), (True, True))
        self.assertEqual(replay.clicks(CHEST), [0])
        self.assertEqual(replay.clicks('TOUCH_TO_CLOSE'), [1])
        self.assertGreaterEqual(replay.index, 11)

    def test_daily_page_close_hint_is_not_clicked_while_settling(self):
        replay = self.replay([scene(DAILY, 'TOUCH_TO_CLOSE')] * 10)
        self.assertEqual(replay.task._claim_daily_rewards_verified(), (True, False))
        self.assertEqual(replay.actions, [])

    def test_persistent_popup_times_out_without_confirming_rewards(self):
        replay = self.replay([scene('TOUCH_TO_CLOSE')] * 50)
        self.assertEqual(replay.task._claim_daily_rewards_verified(), (False, False))
        self.assertLess(replay.index, 49)
        self.assertGreaterEqual(replay.index, 36)
        replay.assert_retried(self, 'TOUCH_TO_CLOSE', 2)
        self.assertEqual(replay.clicks(CHEST), [])

    def test_ready_chest_that_never_changes_times_out_with_pending_claim(self):
        replay = self.replay([scene(DAILY, CHEST)] * 50)
        self.assertEqual(replay.task._claim_daily_rewards_verified(), (False, True))
        self.assertLess(replay.index, 49)
        replay.assert_retried(self, CHEST, 2)
        self.assertEqual(replay.clicks('TOUCH_TO_CLOSE'), [])


class CnDailyRunTests(CnDailyCase):
    def run_probe(self, points, *, frames=None, complete_scan=True, next_day=False):
        from tasks.mission_reward.daily import DailyMission

        replay = self.replay(frames if frames is not None else [scene(DAILY)] * 10)
        task = replay.task
        rows = [DailyMission(key, *values) for key, values in EXPECTED.items()]
        task._enter_mission_reward = Mock()
        task._read_daily_points = Mock(side_effect=points)
        task._scan_daily_missions = Mock(return_value=rows if complete_scan else None)
        task.ui_goto = Mock()
        task.config.multi_set = nullcontext
        task.config.stored = SimpleNamespace(DailyActivity=SimpleNamespace(set=Mock()))
        task.config.cross_set = Mock(side_effect=lambda key, value: replay.values.__setitem__(key, value))
        replay.values[PLAN_PATH] = {'day': 'old', 'points': 100, 'rewards_claimed': True}
        self.stack.enter_context(patch(
            'tasks.mission_reward.daily_reward.get_server_last_update',
            side_effect=[DAY, DAY + timedelta(days=1) if next_day else DAY],
        ))
        return replay, rows

    def assert_invalidation_and_return(self, task):
        self.assertEqual(task.config.cross_set.call_args_list[0].args, (PLAN_PATH, {}))
        task._enter_mission_reward.assert_called_once_with(skip_first_screenshot=False)
        task.ui_goto.assert_called_once()
        self.assertEqual(task.ui_goto.call_args.args[0].name, 'page_main')

    def test_complete_day_uses_post_claim_points_and_skips_list(self):
        replay, _ = self.run_probe(
            [85, 100],
            frames=[scene(DAILY, CHEST)] + [scene('TOUCH_TO_CLOSE')] * 2 + [scene(DAILY)] * 8,
        )
        task = replay.task
        self.assertTrue(task.run())
        self.assert_invalidation_and_return(task)
        self.assertEqual(replay.values[PLAN_PATH], {
            'server': 'CN-Official', 'language': 'cn',
            'day': DAY.isoformat(), 'points': 100, 'rewards_claimed': True, 'missions': [],
        })
        task._scan_daily_missions.assert_not_called()
        task.config.stored.DailyActivity.set.assert_called_once_with(100)
        task.config.task_call.assert_called_once_with('DataUpdate', force_call=False)
        task.config.task_delay.assert_called_once_with(server_update=True)

    def test_full_activity_with_unconfirmed_claim_retries_without_completed_day(self):
        replay, _ = self.run_probe([100], frames=[scene(DAILY, CHEST)] * 50)
        task = replay.task
        self.assertFalse(task.run())
        self.assert_invalidation_and_return(task)
        self.assertEqual(replay.values[PLAN_PATH], {
            'server': 'CN-Official', 'language': 'cn',
            'day': DAY.isoformat(), 'points': 100, 'rewards_claimed': False, 'missions': [],
        })
        task._scan_daily_missions.assert_not_called()
        task.config.task_call.assert_not_called()
        task.config.task_delay.assert_called_once_with(minute=5)

    def test_partial_day_saves_all_observed_missions_for_due_task_priority(self):
        replay, rows = self.run_probe([25, 25])
        task = replay.task
        self.assertTrue(task.run())
        self.assert_invalidation_and_return(task)
        self.assertEqual(replay.values[PLAN_PATH], {
            'server': 'CN-Official', 'language': 'cn',
            'day': DAY.isoformat(), 'points': 25, 'rewards_claimed': True,
            'missions': [asdict(row) for row in rows],
        })
        task._scan_daily_missions.assert_called_once_with(skip_first_screenshot=True)
        task.config.stored.DailyActivity.set.assert_called_once_with(25)
        task.config.task_call.assert_not_called()
        task.config.task_delay.assert_called_once_with(server_update=True)

    def test_unreadable_points_invalidate_previous_plan_and_keep_stored_activity(self):
        replay, _ = self.run_probe([100, None])
        task = replay.task
        self.assertFalse(task.run())
        self.assert_invalidation_and_return(task)
        self.assertEqual(replay.values[PLAN_PATH], {})
        task._scan_daily_missions.assert_not_called()
        task.config.stored.DailyActivity.set.assert_not_called()
        task.config.task_call.assert_not_called()
        task.config.task_delay.assert_called_once_with(minute=5)

    def test_reset_during_scan_invalidates_observation_instead_of_reusing_old_day(self):
        replay, _ = self.run_probe([25, 25], next_day=True)
        task = replay.task
        self.assertFalse(task.run())
        self.assert_invalidation_and_return(task)
        self.assertEqual(replay.values[PLAN_PATH], {})
        task.config.stored.DailyActivity.set.assert_not_called()
        task.config.task_delay.assert_called_once_with(minute=5)

    def test_incomplete_scan_retries_without_retaining_old_missions(self):
        replay, _ = self.run_probe([25, 25], complete_scan=False)
        task = replay.task
        self.assertFalse(task.run())
        self.assert_invalidation_and_return(task)
        self.assertEqual(replay.values[PLAN_PATH], {
            'server': 'CN-Official', 'language': 'cn',
            'day': DAY.isoformat(), 'points': 25, 'rewards_claimed': True, 'missions': [],
        })
        task.config.task_delay.assert_called_once_with(minute=5)

    def test_server_change_during_scan_cannot_relabel_previous_observation(self):
        replay, rows = self.run_probe([25, 25])
        task = replay.task

        def change_server(**kwargs):
            task.config.Emulator_PackageName = 'com.stove.epic7.google'
            return rows

        task._scan_daily_missions.side_effect = change_server
        self.assertFalse(task.run())
        self.assertEqual(replay.values[PLAN_PATH], {})
        task.config.stored.DailyActivity.set.assert_not_called()
        task.config.task_delay.assert_called_once_with(minute=5)

    def test_navigation_failure_cannot_leave_previous_day_plan_eligible(self):
        replay, _ = self.run_probe([25, 25])
        task = replay.task
        task._enter_mission_reward.side_effect = RuntimeError('synthetic navigation failure')
        with self.assertRaisesRegex(RuntimeError, 'synthetic navigation failure'):
            task.run()
        self.assertEqual(replay.values[PLAN_PATH], {})
        task._read_daily_points.assert_not_called()
        task.config.stored.DailyActivity.set.assert_not_called()
        task.config.task_delay.assert_not_called()


class CnDailyPointsRuleTests(CnDailyCase):
    def test_unreadable_or_decorated_text_is_unknown_and_zero_is_valid(self):
        from tasks.mission_reward.daily_reward import DailyPointsOcr

        reader = object.__new__(DailyPointsOcr)
        for text, expected in (
            ('0', 0), ('25', 25), ('1 O O', 100), ('125', 125),
            ('', None), ('（25', None), ('25/100', None), ('1000', None), ('-1', None),
        ):
            with self.subTest(text=text):
                self.assertEqual(reader.format_result(text), expected)
