# ruff: noqa: E402
from module.config import server as _test_server

_test_server.set_lang("global_cn")

"""Archived Koharu claims remain replayable without production activation."""
import json
import subprocess
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

WORKTREE = Path(__file__).resolve().parents[2]
PROJECT_ROOT = WORKTREE

import module.config.server as server
from module.game_info.catalog import load_info
from tasks.activity import calendar, scheduling
from tasks.activity.limited_activity import LimitedActivityEntry

EVENT_ID = 'koharu_raffle_2026_09_17'
NOW = datetime.fromisoformat('2026-09-19T12:00:00+08:00')
SCREENSHOTS = Path(__file__).parent / 'screenshots/koharu_raffle'


from tests.support.history_koharu_raffle import Config, Claim

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
            with self.subTest(frames=frames), patch(
                'tasks.activity.legacy.koharu_raffle_2026_09_17.koharu_raffle.Timer'
            ) as timer:
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
        """Retain the historical case ID; archived unfinished tasks cannot wake."""
        self.assertNotIn(EVENT_ID, {event.event_id for event in self.events})
        self.assertFalse(scheduling.should_schedule_after_battle(self.config))
        scheduling.mark_activity_checked(self.config, EVENT_ID)
        self.assertFalse(scheduling.should_schedule_after_battle(self.config))

    def test_other_events_and_old_summer_record_do_not_suppress_claims(self):
        """Other receipts neither mark Koharu complete nor reactivate it."""
        scheduling.mark_activity_checked(self.config, 'e7wc_battle_gate_2026_09_10')
        self.config.values['SpecialActivity.ActivityRuntime.TaskRewardClaimedAt'] = datetime.now()
        self.assertFalse(scheduling.is_activity_checked_today(self.config, EVENT_ID))
        self.assertFalse(scheduling.should_schedule_after_battle(self.config))

    def test_previous_day_and_other_server_record_do_not_suppress_claims(self):
        """Historical receipt isolation survives without enabling old claims."""
        self.config.values[scheduling._checked_path(self.config, EVENT_ID)] = datetime.now() - timedelta(days=2)
        self.assertFalse(scheduling.is_activity_checked_today(self.config, EVENT_ID))
        self.assertFalse(scheduling.should_schedule_after_battle(self.config))
        scheduling.mark_activity_checked(self.config, EVENT_ID)
        self.assertTrue(scheduling.is_activity_checked_today(self.config, EVENT_ID))
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
        event = load_info().current('koharu_raffle', 'OVERSEA', NOW)
        self.assertEqual(event.start.isoformat(), '2026-09-17T11:00:00+08:00')
        self.assertEqual(event.end.isoformat(), '2026-10-08T11:00:00+08:00')
        ids = lambda at: {e.event_id for e in calendar.active_activities(self.config, at)}  # noqa: E731
        self.assertFalse(event.contains(event.start - timedelta(seconds=1)))
        self.assertTrue(event.contains(event.start))
        self.assertFalse(event.contains(event.end))
        self.assertNotIn(EVENT_ID, ids(event.start - timedelta(seconds=1)))
        self.assertNotIn(EVENT_ID, ids(event.start))
        self.assertNotIn(EVENT_ID, ids(event.end))

    def test_cn_is_not_enabled_by_dates_alone(self):
        event = calendar.ActivityWindow(EVENT_ID, 'Koharu', 'koharu_raffle', 'CN', NOW, NOW + timedelta(days=1))
        server.set_lang('cn')
        self.config.Emulator_PackageName = 'com.zlongame.cn.epicseven'
        with patch.object(calendar, 'load_calendar', return_value=(event,)):
            self.assertEqual(calendar.active_activities(self.config, NOW), ())
            self.assertFalse(scheduling.should_schedule_after_battle(self.config))

    def test_dispatch_rechecks_empty_queue_but_skips_all_done(self):
        """The archived dispatcher ignores both unfinished and completed work."""
        event = calendar.ActivityWindow(EVENT_ID, 'Koharu', 'koharu_raffle', 'OVERSEA',
                                        NOW, NOW + timedelta(days=1))
        events = (event,)
        with patch('tasks.activity.entry.active_activities', return_value=events), patch(
            'tasks.activity.entry.CommonActivityBatch'
        ) as task:
            task.ACTIVITIES = {'koharu_raffle': None}
            task.return_value.run.return_value = True
            self.assertTrue(LimitedActivityEntry(self.config).run())
            task.assert_not_called()
            scheduling.mark_activity_checked(self.config, EVENT_ID)
            self.assertTrue(LimitedActivityEntry(self.config).run())
            task.assert_not_called()

    def test_generated_config_upgrade_keeps_disabled_choice(self):
        """Retire either old switch value while preserving receipts and other choices."""
        from module.config.config_updater import ConfigUpdater

        receipts = {EVENT_ID: {'OVERSEA': '2026-10-07 12:00:00'}}
        for task in ('SpecialActivity', 'LimitedActivity'):
            for enabled in (True, False):
                with self.subTest(task=task, enabled=enabled):
                    old = {'SpecialActivity': {'ActivityRuntime': {'CheckedEvents': receipts}}}
                    old.setdefault(task, {})[task] = {
                        'GetKoharuRaffleReward': enabled, 'GetFreeGacha': False,
                    }
                    result = ConfigUpdater().config_update(old)
                    for owner in ('SpecialActivity', 'LimitedActivity'):
                        self.assertNotIn('GetKoharuRaffleReward', result[owner][owner])
                    self.assertFalse(result['LimitedActivity']['LimitedActivity']['GetFreeGacha'])
                    self.assertEqual(result['LimitedActivity']['LimitedActivity']['GetMeirinReward'], enabled)
                    self.assertEqual(result['SpecialActivity']['ActivityRuntime']['CheckedEvents'], receipts)
                    self.assertEqual(ConfigUpdater().config_update(result), result)
        archived = json.loads((WORKTREE / 'tasks/activity/legacy/koharu_raffle_2026_09_17/i18n.json')
                              .read_text(encoding='utf-8'))
        for path in Path('module/config/i18n').glob('*.json'):
            self.assertNotIn('GetKoharuRaffleReward', json.loads(path.read_text(encoding='utf-8'))['LimitedActivity'])
            texts = archived[path.stem]
            for key in ('name', 'help'):
                self.assertTrue(texts[key])
                self.assertNotIn('SpecialActivity.GetKoharuRaffleReward', texts[key])

    def test_future_dates_do_not_reactivate_archived_flow(self):
        from dataclasses import replace
        from types import SimpleNamespace

        start = NOW + timedelta(days=30)
        period = replace(load_info().current('koharu_raffle', 'OVERSEA', NOW),
                         start=start, end=start + timedelta(days=7))
        with patch.object(calendar, 'load_info', return_value=SimpleNamespace(periods=(period,))):
            self.assertEqual(calendar.load_calendar(), ())
            self.assertEqual(calendar.active_activities(self.config, start), ())
            self.assertIsNone(calendar.next_activity_start(self.config, NOW))

    def test_production_imports_do_not_load_archived_flow(self):
        code = """
import sys
from module.config import server
server.set_lang(sys.argv[1])
from tasks.activity.limited_activity import LimitedActivityEntry
from tasks.activity.common_activity import CommonActivityBatch
from tasks.activity.calendar import SUPPORTED_MODES
assert 'koharu_raffle' not in SUPPORTED_MODES
assert 'koharu_raffle' not in LimitedActivityEntry.ACTIVITY_MODES
assert 'koharu_raffle' not in LimitedActivityEntry.COMMON_ACTIVITY_OPTIONS
assert 'koharu_raffle' not in CommonActivityBatch.ACTIVITIES
assert not any('koharu_raffle' in name for name in sys.modules)
assert 'tasks.activity.assets.assets_activity_special_26_9_17' not in sys.modules
"""
        for lang in ('cn', 'global_cn', 'global_en'):
            with self.subTest(lang=lang):
                result = subprocess.run([sys.executable, '-X', 'utf8', '-B', '-c', code, lang], cwd=WORKTREE,
                                        capture_output=True, text=True, encoding='utf-8', timeout=30, check=False)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
