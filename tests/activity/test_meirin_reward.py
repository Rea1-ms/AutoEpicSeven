"""Meirin reward recognition, retry timing and event-owned completion records."""

import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta
from unittest.mock import patch

from module.config import server
from tests.support.interaction import scene
from tests.support.meirin_reward import (
    AVAILABLE,
    AVAILABLE_SHIFTED,
    CAPTURES,
    EVENT_ID,
    LOADING,
    POPUP,
    SIDEBAR,
    UNAVAILABLE,
    Config,
    MeirinReplay,
    captured,
)
from tests.support.offline import ControlledClock

server.set_lang('global_cn')

from tasks.activity import calendar, scheduling
from tasks.activity.assets.assets_activity_special_26_10_15 import (
    MEIRIN_ALL_TASK_DONE,
    MEIRIN_GATE_CHECK,
    MEIRIN_GATE_SELECTED,
    MEIRIN_REWARD_POPUP_CHECK,
    MEIRIN_TASK_REWARD_NONE,
    MEIRIN_TASK_REWARD_PENDING,
)
from tasks.activity.limited_activity import LimitedActivityEntry
from tasks.activity.meirin_reward import MeirinReward
from tasks.base.assets.assets_base_popup import TOUCH_TO_CLOSE

NOW = datetime.fromisoformat('2026-10-15T17:30:00+08:00')


class ReplayCase(unittest.TestCase):
    def setUp(self):
        server.set_lang('global_cn')
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.clock = self.stack.enter_context(ControlledClock())

    def replay(self, frames):
        return MeirinReplay(frames, self.clock)


class AssetTests(ReplayCase):
    def test_real_frames_distinguish_pending_empty_and_scrolled_rows(self):
        for fixture in CAPTURES:
            with self.subTest(fixture=fixture):
                r = self.replay([captured(fixture)])
                self.assertEqual(r.task.match_template_color(MEIRIN_TASK_REWARD_PENDING),
                                 fixture in (AVAILABLE, AVAILABLE_SHIFTED))
                self.assertEqual(r.task.match_template_color(MEIRIN_TASK_REWARD_NONE), fixture == UNAVAILABLE)
                self.assertFalse(r.task.match_template_color(MEIRIN_ALL_TASK_DONE))
                self.assertEqual(r.task.appear(TOUCH_TO_CLOSE), fixture == POPUP)
                self.assertEqual(r.task.match_template_color(MEIRIN_REWARD_POPUP_CHECK), fixture == POPUP)
                self.assertEqual(r.task.match_template_color(MEIRIN_GATE_CHECK),
                                 fixture in (AVAILABLE, AVAILABLE_SHIFTED, UNAVAILABLE))
                self.assertEqual(r.task._activity_selected(MEIRIN_GATE_SELECTED),
                                 fixture in (AVAILABLE, AVAILABLE_SHIFTED, UNAVAILABLE))

    def test_shifted_selected_sidebar_rejects_other_tabs_and_unselected_label(self):
        for fixture in (AVAILABLE_SHIFTED, AVAILABLE, UNAVAILABLE, SIDEBAR, 'meirin-reward-sidebar-top'):
            with self.subTest(fixture=fixture):
                r = self.replay([captured(fixture)])
                self.assertEqual(r.task._activity_selected(MEIRIN_GATE_SELECTED),
                                 fixture in (AVAILABLE_SHIFTED, AVAILABLE, UNAVAILABLE))
                self.assertEqual(r.actions, [])

    def test_only_explicit_synthetic_first_row_check_proves_all_done(self):
        r = self.replay([captured(UNAVAILABLE, complete=True)])
        self.assertTrue(r.task.match_template_color(MEIRIN_GATE_CHECK))
        self.assertTrue(r.task.match_template_color(MEIRIN_ALL_TASK_DONE))
        self.assertFalse(r.task.match_template_color(MEIRIN_TASK_REWARD_PENDING))
        self.assertFalse(r.task.match_template_color(MEIRIN_TASK_REWARD_NONE))

    def test_real_sidebar_selects_meirin_after_stable_text(self):
        r = self.replay([captured(SIDEBAR)] * 2 + [captured(AVAILABLE)] * 2
                        + [captured(POPUP), captured(UNAVAILABLE)])
        self.assertTrue(r.task.run_claim())
        self.assertEqual(r.index, 5)
        self.assertEqual(len(r.actions), 4)
        self.assertEqual(r.actions[0][0], 1)
        self.assertIn('与梅铃一起吧', r.actions[0][1])
        self.assertEqual(r.actions[1:], [(2, 'swipe'), (3, 'MEIRIN_TASK_REWARD_PENDING'), (4, 'TOUCH_TO_CLOSE')])
        self.assertEqual(r.task.config.values, {})


class ClaimTests(ReplayCase):
    def test_already_selected_shifted_sidebar_claims_without_clicking_the_tab(self):
        # This is an explicit replay, not a continuous recording: the new
        # selected-sidebar capture is followed by the existing reward and empty
        # list captures. Exact actions forbid another sidebar tap before claim.
        r = self.replay([captured(AVAILABLE_SHIFTED)] * 2 + [captured(POPUP), captured(UNAVAILABLE)])
        self.assertTrue(r.task.run_claim())
        self.assertEqual(r.index, 3)
        self.assertEqual(r.actions, [(0, 'swipe'), (1, 'MEIRIN_TASK_REWARD_PENDING'), (2, 'TOUCH_TO_CLOSE')])
        self.assertEqual(r.task.config.values, {})

    def test_real_claim_popup_and_empty_queue_stay_recheckable(self):
        r = self.replay([captured(AVAILABLE)] * 2 + [captured(LOADING), captured(POPUP), captured(UNAVAILABLE)])
        self.assertTrue(r.task.run_claim(navigate=False))
        self.assertEqual(r.index, 4)
        self.assertEqual(r.actions, [(0, 'swipe'), (1, 'MEIRIN_TASK_REWARD_PENDING'), (3, 'TOUCH_TO_CLOSE')])
        self.assertEqual(r.task.config.values, {})

    def test_dropped_claim_and_close_retry_only_after_production_intervals(self):
        r = self.replay([captured(AVAILABLE)] * 8 + [captured(POPUP)] * 8 + [captured(UNAVAILABLE)])
        self.assertTrue(r.task.run_claim(navigate=False))
        self.assertEqual(r.index, 16)
        self.assertEqual(r.clicks('MEIRIN_TASK_REWARD_PENDING'), [1, 6])
        self.assertEqual(r.clicks('TOUCH_TO_CLOSE'), [8, 13])
        r.assert_retry_interval(self, 'MEIRIN_TASK_REWARD_PENDING', 2)
        r.assert_retry_interval(self, 'TOUCH_TO_CLOSE', 2)
        self.assertEqual(r.task.config.values, {})

    def test_list_update_before_reward_popup_never_finishes_early(self):
        r = self.replay([captured(AVAILABLE)] * 2 + [captured(UNAVAILABLE)] * 4
                        + [captured(POPUP), captured(UNAVAILABLE)])
        self.assertTrue(r.task.run_claim(navigate=False))
        self.assertEqual(r.index, 7)
        self.assertEqual(r.clicks('MEIRIN_TASK_REWARD_PENDING'), [1])
        self.assertEqual(r.clicks('TOUCH_TO_CLOSE'), [6])
        self.assertEqual(r.task.config.values, {})

    def test_network_repeated_frames_do_not_replace_reward_confirmation(self):
        network = scene('NETWORK_ERROR_ABNORMAL', 'TOUCH_TO_CLOSE')
        r = self.replay([captured(AVAILABLE)] * 2 + [network] * 2 + [captured(UNAVAILABLE)] * 4
                        + [captured(POPUP), captured(UNAVAILABLE)])
        self.assertTrue(r.task.run_claim(navigate=False))
        self.assertEqual(r.index, 9)
        self.assertEqual(r.clicks('MEIRIN_TASK_REWARD_PENDING'), [1])
        self.assertEqual(r.clicks('TOUCH_TO_CLOSE'), [2, 8])
        self.assertEqual(r.task.config.values, {})

    def test_bottom_claimed_row_does_not_mark_today_complete(self):
        r = self.replay([captured(UNAVAILABLE)] * 2)
        self.assertTrue(r.task.run_claim(navigate=False))
        self.assertEqual(r.actions, [(0, 'swipe')])
        self.assertEqual(r.task.config.values, {})

    def test_synthetic_first_row_completion_records_only_meirin(self):
        r = self.replay([captured(UNAVAILABLE, complete=True)] * 2)
        self.assertTrue(r.task.run_claim(navigate=False))
        self.assertEqual(r.actions, [(0, 'swipe')])
        self.assertTrue(scheduling.is_activity_checked_today(r.task.config, EVENT_ID))
        self.assertFalse(scheduling.is_activity_checked_today(r.task.config, 'koharu_raffle_2026_09_17'))
        self.assertFalse(scheduling.is_activity_checked_today(r.task.config, calendar.DEFAULT_FREE_GACHA_20_ID))

    def test_completion_before_popup_waits_for_reward_confirmation(self):
        r = self.replay([captured(AVAILABLE)] * 2 + [captured(UNAVAILABLE, complete=True)] * 4
                        + [captured(POPUP), captured(UNAVAILABLE, complete=True)])
        self.assertTrue(r.task.run_claim(navigate=False))
        self.assertEqual(r.index, 7)
        self.assertEqual(r.clicks('TOUCH_TO_CLOSE'), [6])
        self.assertTrue(scheduling.is_activity_checked_today(r.task.config, EVENT_ID))

    def test_missing_reward_popup_fails_locally_without_completion(self):
        r = self.replay([captured(AVAILABLE)] * 2 + [captured(UNAVAILABLE)] * 80)
        started = self.clock.now
        self.assertFalse(r.task.run_claim(navigate=False))
        self.assertLess(self.clock.now - started, 40)
        self.assertEqual(r.clicks('MEIRIN_TASK_REWARD_PENDING'), [1])
        self.assertFalse(r.clicks('TOUCH_TO_CLOSE'))
        self.assertEqual(r.task.config.values, {})

    def test_wrong_selected_tab_never_claims(self):
        r = self.replay([captured(SIDEBAR)])
        self.assertFalse(r.task.run_claim(navigate=False))
        self.assertEqual(r.actions, [])

    def test_disabled_or_unsupported_server_does_not_touch_device(self):
        for lang, package, enabled in (
            ('global_cn', 'com.stove.epic7.google', False),
            ('cn', 'com.zlongame.cn.epicseven', True),
            ('global_en', 'com.stove.epic7.google', True),
        ):
            with self.subTest(lang=lang, enabled=enabled):
                server.set_lang(lang)
                task = object.__new__(MeirinReward)
                task.config = Config()
                task.config.Emulator_PackageName = package
                task.config.LimitedActivity_GetMeirinReward = enabled
                task.device = None
                self.assertTrue(task.run())
                self.assertEqual(task.config.delays, [{'server_update': True}])


class SchedulingTests(ReplayCase):
    def setUp(self):
        super().setUp()
        self.config = Config()
        clock = self.stack.enter_context(patch.object(calendar, 'datetime', wraps=datetime))
        clock.now.return_value = NOW

    def test_empty_queue_is_scheduled_again_after_battle(self):
        r = self.replay([captured(UNAVAILABLE)] * 2)
        self.assertTrue(r.task.run_claim(navigate=False))
        scheduling.schedule_activity_after_battle(r.task.config)
        self.assertEqual(r.task.config.calls, [('LimitedActivity', {'force_call': False})])
        scheduling.mark_activity_checked(r.task.config, EVENT_ID)
        self.assertFalse(scheduling.should_schedule_after_battle(r.task.config))

    def test_old_event_or_other_server_record_does_not_suppress_meirin(self):
        scheduling.mark_activity_checked(self.config, 'koharu_raffle_2026_09_17')
        checked_at = datetime.now(calendar.EVENT_TIMEZONE).astimezone().replace(tzinfo=None)
        self.config.values['SpecialActivity.ActivityRuntime.TaskRewardClaimedAt'] = checked_at
        self.assertTrue(scheduling.should_schedule_after_battle(self.config))
        self.config.values[scheduling._checked_path(self.config, EVENT_ID)] = checked_at - timedelta(days=2)
        self.assertTrue(scheduling.should_schedule_after_battle(self.config))
        scheduling.mark_activity_checked(self.config, EVENT_ID)
        self.config.Emulator_PackageName = 'com.zlongame.cn.epicseven'
        self.assertFalse(scheduling.is_activity_checked_today(self.config, EVENT_ID))

    def test_disabled_saved_option_or_task_never_schedules(self):
        self.config.values['LimitedActivity.LimitedActivity.GetMeirinReward'] = False
        self.assertFalse(scheduling.should_schedule_after_battle(self.config))
        self.config.values.clear()
        self.config.enabled = False
        self.assertFalse(scheduling.should_schedule_after_battle(self.config))

    def test_current_calendar_activates_meirin_and_retires_koharu(self):
        events = calendar.active_activities(self.config, NOW)
        self.assertIn(EVENT_ID, {event.event_id for event in events})
        self.assertNotIn('koharu_raffle_2026_09_17', {event.event_id for event in events})
        event = next(event for event in events if event.event_id == EVENT_ID)
        for at, expected in ((event.start - timedelta(seconds=1), False), (event.start, True), (event.end, False)):
            self.assertEqual(EVENT_ID in {e.event_id for e in calendar.active_activities(self.config, at)}, expected)

    def test_entry_rechecks_empty_queue_but_skips_event_after_completion(self):
        events = tuple(e for e in calendar.active_activities(self.config, NOW) if e.event_id == EVENT_ID)
        self.assertEqual(len(events), 1)
        with patch('tasks.activity.entry.active_activities', return_value=events), patch(
            'tasks.activity.entry.CommonActivityBatch'
        ) as task:
            task.ACTIVITIES = {'meirin_reward': None}
            task.return_value.run.return_value = True
            self.assertTrue(LimitedActivityEntry(self.config).run())
            self.assertTrue(LimitedActivityEntry(self.config).run())
            self.assertEqual(task.call_count, 2)
            self.assertEqual(task.return_value.run.call_args.args[0][0].event_id, EVENT_ID)
            scheduling.mark_activity_checked(self.config, EVENT_ID)
            task.reset_mock()
            self.assertTrue(LimitedActivityEntry(self.config).run())
            task.assert_not_called()
