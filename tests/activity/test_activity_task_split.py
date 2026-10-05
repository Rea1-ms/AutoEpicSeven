# ruff: noqa: E402
from module.config import server as _test_server

_test_server.set_lang("global_cn")

"""Task split regressions using the real config binding and scheduler queue."""
import ast
import json
import unittest
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

WORKTREE = Path(__file__).resolve().parents[2]
WORKTREE_ROOT = WORKTREE

import module.config.server as server
from module.config.config import AzurLaneConfig
from module.config.config_updater import ConfigUpdater
from module.config.deep import deep_set
from tasks.activity import calendar, scheduling
from tasks.activity.common_activity import CommonActivityBatch
from tasks.activity.entry import SpecialActivityEntry
from tasks.activity.huche_shop import HucheShop
from tasks.activity.limited_activity import LimitedActivityEntry
from tasks.base.ui import UI

NOW = datetime(2026, 9, 23, 12, tzinfo=calendar.EVENT_TIMEZONE)


def old_config(enabled=True):
    return {
        'Alas': {'Emulator': {'PackageName': 'OVERSEA-Play', 'GameLanguage': 'cn'}},
        'SpecialActivity': {
            'Scheduler': {'Enable': enabled, 'NextRun': '2026-09-23 11:00:00', 'ServerUpdate': '02:00'},
            'SpecialActivity': {'GetFreeGacha': True, 'GetE7wcBattleGateReward': False,
                                'GetKoharuRaffleReward': True, 'BuyHucheMysticMedals': True},
            'ActivityRuntime': {'CheckedEvents': {
                'huche_shop_2026_09_17_regular_mystic': {'OVERSEA': '2026-09-20 12:00:00'},
                'free_gacha_20_2026_08_27': {'OVERSEA': '2026-09-23 03:00:00'}},
                'FreeGacha20CheckedAt': '2026-09-23 03:00:00'},
        },
    }


class MemoryConfig(AzurLaneConfig):
    """Keep real task binding, delay and queue behavior, with no disk writes."""
    def __init__(self, data):
        self.bound = {}
        self.modified = {}
        self.overridden = {}
        self.auto_update = False
        self.is_template_config = False
        self.data = data
        self.init_task('LimitedActivity')

    def load(self):
        self.save()

    def save(self):
        for key, value in self.modified.items():
            deep_set(self.data, key, value)
        self.modified.clear()

    def update(self):
        self.save()
        self.bind(self.task)


class MigrationTests(unittest.TestCase):
    def test_old_enabled_disabled_settings_and_receipts_survive(self):
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                old = old_config(enabled)
                result = ConfigUpdater().config_update(old)
                for task in ('LimitedActivity', 'SpecialActivity'):
                    self.assertEqual(result[task]['Scheduler']['Enable'], enabled)
                    self.assertEqual(result[task]['Scheduler']['NextRun'], datetime(2026, 9, 23, 11))
                    self.assertEqual(result[task]['Scheduler']['ServerUpdate'], '02:00')
                    self.assertEqual(result[task]['Scheduler']['Command'], task)
                self.assertEqual(result['LimitedActivity']['LimitedActivity'], {
                    'GetFreeGacha': True, 'GetKoharuRaffleReward': True})
                self.assertEqual(result['SpecialActivity']['SpecialActivity'], {'BuyHucheMysticMedals': True})
                self.assertEqual(result['SpecialActivity']['ActivityRuntime']['CheckedEvents'],
                                 old['SpecialActivity']['ActivityRuntime']['CheckedEvents'])
                self.assertEqual(ConfigUpdater().config_update(result), result)

    def test_explicit_new_choices_are_not_overwritten(self):
        old = old_config()
        old['LimitedActivity'] = {
            'Scheduler': {'Enable': False, 'NextRun': '2026-09-24 03:00:00', 'ServerUpdate': '03:00'},
            'LimitedActivity': {'GetFreeGacha': False, 'GetE7wcBattleGateReward': True,
                                'GetKoharuRaffleReward': False}}
        result = ConfigUpdater().config_update(old)['LimitedActivity']
        self.assertFalse(result['Scheduler']['Enable'])
        self.assertEqual(result['Scheduler']['NextRun'], datetime(2026, 9, 24, 3))
        # ServerUpdate is hidden and follows the existing schema/server override.
        self.assertEqual(result['Scheduler']['ServerUpdate'], '02:00')
        self.assertEqual(result['LimitedActivity'], {'GetFreeGacha': False, 'GetKoharuRaffleReward': False})

    def test_fresh_profile_does_not_enable_tasks_or_spending(self):
        result = ConfigUpdater().config_update({})
        self.assertFalse(result['LimitedActivity']['Scheduler']['Enable'])
        self.assertFalse(result['SpecialActivity']['Scheduler']['Enable'])
        self.assertFalse(result['SpecialActivity']['SpecialActivity']['BuyHucheMysticMedals'])

    def test_menu_callbacks_and_translations_are_ready(self):
        menu = json.loads((WORKTREE / 'module/config/argument/menu.json').read_text(encoding='utf-8'))
        for task in ('LimitedActivity', 'SpecialActivity'):
            self.assertIn(task, menu['Daily']['tasks'])
            for path in (WORKTREE / 'module/config/i18n').glob('*.json'):
                texts = json.loads(path.read_text(encoding='utf-8'))
                self.assertTrue(texts['Task'][task]['name'])
                self.assertTrue(texts[task]['_info']['help'])
                self.assertNotIn(task + '.', texts['Task'][task]['name'])
        code = ast.parse((WORKTREE / 'aes.py').read_text(encoding='utf-8'))
        methods = {node.name for node in ast.walk(code) if isinstance(node, ast.FunctionDef)}
        self.assertTrue({'limited_activity', 'special_activity'} <= methods)


class SchedulingTests(unittest.TestCase):
    def setUp(self):
        server.set_lang('global_cn')
        self.config = MemoryConfig(ConfigUpdater().config_update(old_config()))
        clock = patch.object(calendar, 'datetime', wraps=datetime)
        clock.start().now.return_value = NOW
        self.addCleanup(clock.stop)
        self.events = calendar.active_activities(self.config, NOW)

    def test_each_task_only_sees_its_entrances(self):
        limited = calendar.active_activities(self.config, NOW, task='LimitedActivity')
        special = calendar.active_activities(self.config, NOW, task='SpecialActivity')
        self.assertEqual({e.mode for e in limited}, {'free_gacha_20', 'koharu_raffle'})
        self.assertEqual({e.mode for e in special}, {'huche_shop'})
        self.assertEqual(set(limited) | set(special), set(self.events))

    def test_opening_and_refresh_are_scoped_to_own_task(self):
        launch = NOW + timedelta(hours=2)
        future = calendar.ActivityWindow('next_common', 'Next', 'free_gacha_20', 'OVERSEA', launch,
                                         launch + timedelta(days=1))
        with patch.object(calendar, 'load_calendar', return_value=(*self.events, future)), patch.object(
                scheduling, 'aware_time', return_value=NOW), patch.object(self.config, 'task_delay') as delay:
            scheduling.delay_next_activity_check(self.config, task='LimitedActivity')
            # Calendar dates are +08:00; scheduler targets use the host's local
            # naive time, which is UTC on CI and +08:00 on the Windows host.
            delay.assert_called_with(server_update=True, target=launch.astimezone().replace(tzinfo=None), task='LimitedActivity')
            self.config.init_task('SpecialActivity')
            scheduling.delay_next_activity_check(self.config, task='SpecialActivity')
            delay.assert_called_with(server_update=True,
                                     target=NOW.replace(hour=23).astimezone().replace(tzinfo=None), task='SpecialActivity')

    def test_receipt_uses_limited_daily_reset_and_fixed_stock_is_preserved(self):
        deep_set(self.config.data, 'LimitedActivity.Scheduler.ServerUpdate', '02:00')
        deep_set(self.config.data, 'SpecialActivity.Scheduler.ServerUpdate', '11:00')
        def reset(value):
            return datetime(2026, 9, 23, 2 if value == '02:00' else 11)
        event = next(e for e in self.events if e.mode == 'free_gacha_20')
        with patch.object(scheduling, 'get_server_last_update', side_effect=reset) as last:
            self.assertTrue(scheduling.is_activity_checked_in_window(self.config, event))
            last.assert_called_once_with('02:00')
        shop = next(e for e in self.events if e.mode == 'huche_shop')
        self.assertTrue(scheduling.is_activity_checked_since(self.config,
            shop.event_id + '_regular_mystic', shop.start, now=NOW))

    def test_after_battle_only_wakes_enabled_unfinished_limited_task(self):
        self.config.init_task('Combat')
        with patch.object(self.config, 'task_call') as call:
            scheduling.schedule_activity_after_battle(self.config)
            call.assert_called_once_with('LimitedActivity', force_call=False)
            call.reset_mock()
            deep_set(self.config.data, 'LimitedActivity.LimitedActivity.GetKoharuRaffleReward', False)
            self.assertTrue(self.config.LimitedActivity_GetKoharuRaffleReward)
            scheduling.schedule_activity_after_battle(self.config)
            call.assert_not_called()
            deep_set(self.config.data, 'LimitedActivity.LimitedActivity.GetKoharuRaffleReward', True)
            deep_set(self.config.data, 'LimitedActivity.Scheduler.Enable', False)
            scheduling.schedule_activity_after_battle(self.config)
            call.assert_not_called()

    def test_no_current_events_do_not_delay_the_other_task(self):
        before = deepcopy(self.config.data['SpecialActivity']['Scheduler'])
        with patch('tasks.activity.entry.active_activities', return_value=()), patch.object(
                scheduling, 'next_activity_start', return_value=None), patch.object(
                scheduling, 'active_activities', return_value=()):
            self.assertTrue(LimitedActivityEntry(self.config).run())
        self.assertEqual(self.config.data['SpecialActivity']['Scheduler'], before)
        self.assertGreater(self.config.data['LimitedActivity']['Scheduler']['NextRun'], datetime.now())

    def test_failure_preserves_other_task_due_time_and_queue(self):
        for failing_task in ('LimitedActivity', 'SpecialActivity'):
            with self.subTest(failing_task=failing_task):
                config = MemoryConfig(ConfigUpdater().config_update(old_config()))
                for task, data in config.data.items():
                    if 'Scheduler' in data:
                        data['Scheduler']['Enable'] = task in ('LimitedActivity', 'SpecialActivity')
                        data['Scheduler']['NextRun'] = datetime.now() - timedelta(minutes=1)
                other = 'SpecialActivity' if failing_task == 'LimitedActivity' else 'LimitedActivity'
                before = config.data[other]['Scheduler']['NextRun']
                config.init_task(failing_task)
                device = SimpleNamespace(app_is_running=lambda: True)
                target = CommonActivityBatch if failing_task == 'LimitedActivity' else HucheShop
                entry = LimitedActivityEntry if failing_task == 'LimitedActivity' else SpecialActivityEntry
                method = '_run_pending' if failing_task == 'LimitedActivity' else 'run_purchase'
                with patch.object(target, method, return_value=False), patch.object(UI, 'ui_goto'), patch(
                        'tasks.activity.entry.is_activity_checked_in_window', return_value=False), patch(
                        'tasks.activity.huche_shop.is_activity_checked_in_window', return_value=False):
                    self.assertFalse(entry(config, device=device).run())
                self.assertEqual(config.data[other]['Scheduler']['NextRun'], before)
                self.assertGreater(config.data[failing_task]['Scheduler']['NextRun'], datetime.now())
                config.get_next_task()
                self.assertEqual([task.command for task in config.pending_task], [other])
                config.init_task(other)
                success_target = HucheShop if other == 'SpecialActivity' else CommonActivityBatch
                success_entry = SpecialActivityEntry if other == 'SpecialActivity' else LimitedActivityEntry
                with patch.object(success_target, 'run', return_value=True) as run, patch.object(UI, 'ui_goto'), patch(
                        'tasks.activity.entry.is_activity_checked_in_window', return_value=False):
                    self.assertTrue(success_entry(config, device=device).run())
                    run.assert_called_once()
                self.assertEqual(config.task.command, other)
