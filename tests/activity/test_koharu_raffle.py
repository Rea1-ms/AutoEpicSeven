# ruff: noqa: E402
from module.config import server as _test_server

_test_server.set_lang("global_cn")

"""Offline Koharu task claims, event dispatch, and battle-triggered rechecks."""
import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

WORKTREE = Path(__file__).resolve().parents[2]
PROJECT_ROOT = WORKTREE

import module.config.server as server
from tasks.activity import calendar, scheduling
from tasks.activity.limited_activity import LimitedActivityEntry
from tasks.activity.koharu_raffle import (
    KoharuRaffle, CHUN_ALL_TASK_DONE, CHUN_GATE_CHECK, CHUN_GATE_SELECTED,
    CHUN_TASK_REWARD_NONE, CHUN_TASK_REWARD_PENDING,
)

EVENT_ID = 'koharu_raffle_2026_09_17'
NOW = datetime.fromisoformat('2026-09-19T12:00:00+08:00')
SCREENSHOTS = Path(__file__).parent / 'screenshots/koharu_raffle'


class Config:
    SpecialActivity_BuyHucheMysticMedals = False
    Emulator_PackageName = 'com.stove.epic7.google'
    Emulator_GameLanguage = 'auto'
    Scheduler_ServerUpdate = '02:00'
    LimitedActivity_GetKoharuRaffleReward = True
    enabled = True

    def __init__(self):
        self.values = {}
        self.delays = []

    def cross_get(self, path, default=None):
        return self.values.get(path, default)

    def cross_set(self, path, value):
        self.values[path] = value

    def task_delay(self, **kwargs):
        self.delays.append(kwargs)

    def is_task_enabled(self, task):
        return self.enabled


class Claim(KoharuRaffle):
    def __init__(self, frames):
        self.config = Config()
        self.activity_id = EVENT_ID
        self.frames = frames
        self.index = -1
        self.actions = []
        self.routes = []
        self.device = SimpleNamespace(screenshot=self.screenshot, click=self.click,
                                      swipe=self.swipe, app_is_running=lambda: True)
        self.device.screenshot()

    @property
    def frame(self):
        return self.frames[self.index]

    def screenshot(self):
        self.index += 1
        if self.index >= len(self.frames):
            raise AssertionError('Claim did not finish on supplied frames')

    def click(self, button):
        assert button is CHUN_TASK_REWARD_PENDING
        self.actions.append('claim')

    def swipe(self, start, end, **kwargs):
        self.actions.append('top')
        assert 260 < start[0] < 730 and end[1] > start[1]

    def select_activity(self, keyword, selected):
        assert keyword == '收集抽奖券' and selected is CHUN_GATE_SELECTED
        return True

    def ui_goto(self, page, **kwargs):
        self.routes.append(page)

    def match_template_color(self, button, **kwargs):
        if button is CHUN_GATE_CHECK:
            return self.frame in ('pending', 'none', 'done', 'scrolled')
        expected = {CHUN_TASK_REWARD_PENDING: 'pending',
                    CHUN_TASK_REWARD_NONE: 'none', CHUN_ALL_TASK_DONE: 'done'}
        return self.frame == expected[button]

    def handle_touch_to_close(self):
        if self.frame == 'popup':
            self.actions.append('close')
            return True
        return False

    def handle_network_error(self):
        if self.frame == 'network':
            self.actions.append('network')
            return True
        return False


class ClaimTests(unittest.TestCase):
    def setUp(self):
        server.set_lang('global_cn')

    def test_empty_queue_does_not_mark_today_done(self):
        claim = Claim(['none', 'none'])
        self.assertTrue(claim.run_claim())
        self.assertEqual(claim.actions, ['top'])
        self.assertEqual(claim.config.values, {})

    def test_all_done_marks_only_this_event(self):
        claim = Claim(['done', 'done'])
        self.assertTrue(claim.run_claim())
        self.assertTrue(scheduling.is_activity_checked_today(claim.config, EVENT_ID))
        self.assertFalse(scheduling.is_activity_checked_today(claim.config, calendar.DEFAULT_FREE_GACHA_20_ID))

    def test_claim_multiple_tasks_after_reordering(self):
        claim = Claim(['pending', 'pending', 'popup', 'pending', 'popup', 'none'])
        self.assertTrue(claim.run_claim())
        self.assertEqual(claim.actions, ['top', 'claim', 'close', 'claim', 'close'])
        self.assertEqual(claim.config.values, {})

    def test_last_claim_updates_daily_record_after_popup(self):
        claim = Claim(['pending', 'pending', 'done', 'popup', 'done'])
        self.assertTrue(claim.run_claim())
        self.assertEqual(claim.index, 4)
        self.assertTrue(scheduling.is_activity_checked_today(claim.config, EVENT_ID))

    def test_no_reward_before_popup_does_not_exit_early(self):
        claim = Claim(['pending', 'pending', 'none', 'popup', 'none'])
        self.assertTrue(claim.run_claim())
        self.assertEqual(claim.index, 4)
        self.assertEqual(claim.config.values, {})

    def test_dropped_claim_and_close_clicks_retry(self):
        claim = Claim(['pending', 'pending', 'pending', 'popup', 'popup', 'done'])
        self.assertTrue(claim.run_claim())
        self.assertEqual(claim.actions, ['top', 'claim', 'claim', 'close', 'close'])

    def test_reentry_scroll_and_loading_are_not_completion(self):
        claim = Claim(['scrolled', 'loading', 'none'])
        self.assertTrue(claim.run_claim())
        self.assertEqual(claim.actions, ['top'])
        self.assertEqual(claim.index, 2)

    def test_network_close_does_not_replace_reward_close(self):
        claim = Claim(['pending', 'pending', 'network', 'done', 'popup', 'done'])
        self.assertTrue(claim.run_claim())
        self.assertEqual(claim.index, 5)
        self.assertEqual(claim.actions, ['top', 'claim', 'network', 'close'])

    def test_unknown_missing_popup_and_ineffective_claim_are_bounded(self):
        for frames in (['other'], ['pending', 'pending', 'none'],
                       ['pending', 'pending', 'pending'], ['network', 'network']):
            with self.subTest(frames=frames), patch('tasks.activity.koharu_raffle.Timer') as timer:
                timer.return_value.start.return_value.reached.side_effect = [False] * (len(frames)-1) + [True]
                claim = Claim(frames)
                self.assertFalse(claim.run_claim())
                self.assertEqual(claim.config.values, {})
                timer.return_value.start.return_value.reset.assert_not_called()

    def test_sidebar_failure_does_not_claim(self):
        claim = Claim(['pending'])
        claim.select_activity = lambda *args: False
        self.assertFalse(claim.run_claim())
        self.assertEqual(claim.actions, [])

    def test_success_stays_on_activity_failure_schedules_retry(self):
        from tasks.base.page import page_common_activity
        claim = Claim(['none', 'none'])
        self.assertTrue(claim.run())
        self.assertEqual(claim.routes, [page_common_activity])
        self.assertEqual(claim.config.delays, [{'server_update': True}])
        claim = Claim(['other'])
        with patch.object(claim, 'run_claim', return_value=False):
            self.assertFalse(claim.run())
        self.assertEqual(claim.config.delays, [{'success': False}])

    def test_disabled_cn_and_english_never_touch_device_or_assets(self):
        for lang, package, enabled in (
            ('global_cn', 'com.stove.epic7.google', False),
            ('cn', 'com.zlongame.cn.epicseven', True),
            ('global_en', 'com.stove.epic7.google', True),
        ):
            with self.subTest(lang=lang, enabled=enabled):
                server.set_lang(lang)
                claim = Claim(['pending'])
                claim.config.Emulator_PackageName = package
                claim.config.LimitedActivity_GetKoharuRaffleReward = enabled
                claim.device = None
                self.assertTrue(claim.run())
                self.assertEqual(claim.config.delays, [{'server_update': True}])


class SchedulingTests(unittest.TestCase):
    def setUp(self):
        server.set_lang('global_cn')
        self.config = Config()
        self.events = calendar.active_activities(self.config, NOW)
        clock = patch.object(calendar, 'datetime', wraps=datetime)
        self.addCleanup(clock.stop)
        clock.start().now.return_value = NOW

    def test_active_unfinished_tasks_schedule_after_battle(self):
        self.assertTrue(scheduling.should_schedule_after_battle(self.config))
        scheduling.mark_activity_checked(self.config, EVENT_ID)
        self.assertFalse(scheduling.should_schedule_after_battle(self.config))

    def test_other_events_and_old_summer_record_do_not_suppress_claims(self):
        scheduling.mark_activity_checked(self.config, 'e7wc_battle_gate_2026_09_10')
        self.config.values['SpecialActivity.ActivityRuntime.TaskRewardClaimedAt'] = datetime.now()
        self.assertTrue(scheduling.should_schedule_after_battle(self.config))

    def test_previous_day_and_other_server_record_do_not_suppress_claims(self):
        self.config.values[scheduling._checked_path(self.config, EVENT_ID)] = datetime.now() - timedelta(days=2)
        self.assertTrue(scheduling.should_schedule_after_battle(self.config))
        scheduling.mark_activity_checked(self.config, EVENT_ID)
        self.config.Emulator_PackageName = 'com.zlongame.cn.epicseven'
        self.assertFalse(scheduling.is_activity_checked_today(self.config, EVENT_ID))

    def test_disabled_or_expired_events_do_not_schedule(self):
        self.config.LimitedActivity_GetKoharuRaffleReward = False
        self.assertFalse(scheduling.should_schedule_after_battle(self.config))
        self.config.LimitedActivity_GetKoharuRaffleReward = True
        self.config.enabled = False
        self.assertFalse(scheduling.should_schedule_after_battle(self.config))
        self.config.enabled = True
        with patch.object(scheduling, 'active_activities', return_value=()):
            self.assertFalse(scheduling.should_schedule_after_battle(self.config))

    def test_start_inclusive_end_exclusive(self):
        event = next(e for e in self.events if e.event_id == EVENT_ID)
        ids = lambda at: {e.event_id for e in calendar.active_activities(self.config, at)}  # noqa: E731
        self.assertNotIn(EVENT_ID, ids(event.start - timedelta(seconds=1)))
        self.assertIn(EVENT_ID, ids(event.start))
        self.assertNotIn(EVENT_ID, ids(event.end))

    def test_cn_is_not_enabled_by_dates_alone(self):
        event = calendar.ActivityWindow(EVENT_ID, 'Koharu', 'koharu_raffle', 'CN', NOW, NOW + timedelta(days=1))
        server.set_lang('cn')
        self.config.Emulator_PackageName = 'com.zlongame.cn.epicseven'
        with patch.object(calendar, 'load_calendar', return_value=(event,)):
            self.assertEqual(calendar.active_activities(self.config, NOW), ())
            self.assertFalse(scheduling.should_schedule_after_battle(self.config))

    def test_dispatch_rechecks_empty_queue_but_skips_all_done(self):
        events = tuple(e for e in self.events if e.event_id == EVENT_ID)
        with patch('tasks.activity.entry.active_activities', return_value=events), patch(
            'tasks.activity.entry.CommonActivityBatch'
        ) as task:
            task.ACTIVITIES = {'koharu_raffle': None}
            task.return_value.run.return_value = True
            self.assertTrue(LimitedActivityEntry(self.config).run())
            self.assertEqual(task.return_value.run.call_args.args[0][0].event_id, EVENT_ID)
            self.assertTrue(LimitedActivityEntry(self.config).run())
            self.assertEqual(task.call_count, 2)
            scheduling.mark_activity_checked(self.config, EVENT_ID)
            task.reset_mock()
            self.assertTrue(LimitedActivityEntry(self.config).run())
            task.assert_not_called()

    def test_generated_config_upgrade_keeps_disabled_choice(self):
        from module.config.config_updater import ConfigUpdater
        old = {'SpecialActivity': {'SpecialActivity': {'GetKoharuRaffleReward': False, 'GetFreeGacha': False}}}
        result = ConfigUpdater().config_update(old)['LimitedActivity']['LimitedActivity']
        self.assertFalse(result['GetKoharuRaffleReward'])
        self.assertFalse(result['GetFreeGacha'])
        self.assertTrue(ConfigUpdater().config_update({})['LimitedActivity']['LimitedActivity']['GetKoharuRaffleReward'])
        for path in Path('module/config/i18n').glob('*.json'):
            texts = json.loads(path.read_text(encoding='utf-8'))['LimitedActivity']['GetKoharuRaffleReward']
            for key in ('name', 'help'):
                self.assertTrue(texts[key])
                self.assertNotIn('SpecialActivity.GetKoharuRaffleReward', texts[key])
