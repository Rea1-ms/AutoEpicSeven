# ruff: noqa: E402
from module.config import server as _test_server

_test_server.set_lang("global_cn")

"""Offline activity batch lifecycle: one device, one final return to main."""
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

WORKTREE = Path(__file__).resolve().parents[2]
WORKTREE_ROOT = WORKTREE

import module.config.server as server
from tasks.base.page import page_common_activity, page_main
from tasks.base.ui import UI
from tasks.activity.limited_activity import LimitedActivityEntry
from tasks.activity.entry import SpecialActivityEntry
from tasks.activity.common_activity import CommonActivityBatch
from tasks.activity.free_gacha_20 import FreeGacha20
from tasks.activity.legacy.e7wc_battle_gate_2026_09_12.e7wc_battle_gate import E7wcBattleGate
from tasks.activity.koharu_raffle import KoharuRaffle
from tests.support.history_e7wc_battle_gate import HISTORICAL_ACTIVITIES

MODES = ('free_gacha_20', 'koharu_raffle')
CLASSES = (FreeGacha20, E7wcBattleGate, KoharuRaffle)


class Config:
    Emulator_PackageName = 'com.stove.epic7.google'
    Emulator_GameLanguage = 'cn'
    LimitedActivity_GetFreeGacha = True
    LimitedActivity_GetE7wcBattleGateReward = True
    LimitedActivity_GetKoharuRaffleReward = True

    def __init__(self):
        self.delays = []

    def task_delay(self, **kwargs):
        self.delays.append(kwargs)


class Device:
    instances = []

    def __init__(self, config=None):
        self.page = page_main
        self.instances.append(self)

    def app_is_running(self):
        return True


class BatchTests(unittest.TestCase):
    def run_batch(self, modes=MODES, checked=(), disabled=(), failed=None, device=None, entry_cls=LimitedActivityEntry):
        server.set_lang('global_cn')
        Device.instances = []
        config = Config()
        for mode in disabled:
            setattr(config, LimitedActivityEntry.COMMON_ACTIVITY_OPTIONS[mode], False)
        events = [SimpleNamespace(mode=mode, event_id=mode, end='offline') for mode in modes]
        actions, routes, devices = [], [], []

        def route(activity, page, **kwargs):
            if activity.device.page is not page:
                routes.append((activity.device.page, page))
            actions.append(('route', page))
            activity.device.page = page

        def claim(activity, **kwargs):
            assert kwargs == {'navigate': False}
            devices.append(activity.device)
            actions.append(('claim', activity.activity_id))
            return activity.activity_id != failed

        def run_pending(activity, pending):
            return all(worker.run_claim(navigate=False) for _, _, worker in pending.values())

        class Legacy:
            def __init__(self, config, device=None, task=None):
                self.device = device if device is not None else Device(config)

            def run(self):
                actions.append(('legacy', None))
                self.device.page = page_main
                return True

        with ExitStack() as stack:
            if 'e7wc_battle_gate' in modes:
                # Keep the original three-tab assertion using explicit historical
                # input; the production registry and dispatcher stay unchanged.
                stack.enter_context(patch.object(CommonActivityBatch, 'ACTIVITIES', HISTORICAL_ACTIVITIES))
                stack.enter_context(patch.object(LimitedActivityEntry, 'ACTIVITY_MODES', modes))
            stack.enter_context(patch('module.base.base.Device', Device))
            stack.enter_context(patch('tasks.activity.entry.active_activities', return_value=events))
            stack.enter_context(patch('tasks.activity.entry.is_activity_checked_in_window',
                                      side_effect=lambda config, event: event.event_id in checked))
            next_check = stack.enter_context(patch('tasks.activity.entry.delay_next_activity_check'))
            stack.enter_context(patch.object(UI, 'ui_goto', route))
            stack.enter_context(patch.object(CommonActivityBatch, '_run_pending', run_pending))
            for cls in CLASSES:
                stack.enter_context(patch.object(cls, 'run_claim', claim))
            stack.enter_context(patch('tasks.activity.legacy.summer_2026_06_25.special_activity.SpecialActivity', Legacy))
            entry = entry_cls(config=config, device=device)
            result = entry.run()
        return SimpleNamespace(result=result, entry=entry, actions=actions, routes=routes,
                               devices=devices, config=config, next_check=next_check)

    def test_three_claims_stay_on_event_page_then_return_main_once(self):
        modes = ('free_gacha_20', 'e7wc_battle_gate', 'koharu_raffle')
        run = self.run_batch(modes=modes)
        self.assertTrue(run.result)
        self.assertEqual(run.routes, [(page_main, page_common_activity), (page_common_activity, page_main)])
        self.assertEqual([value for action, value in run.actions if action == 'claim'], list(modes))
        self.assertEqual(run.actions[-1], ('route', page_main))
        self.assertEqual(sum(value is page_main for action, value in run.actions if action == 'route'), 1)
        run.next_check.assert_called_once_with(run.config, task="LimitedActivity")

    def test_device_created_once_and_reused_when_not_supplied(self):
        run = self.run_batch()
        self.assertEqual(len(Device.instances), 1)
        self.assertTrue(all(device is run.entry.device for device in run.devices))

    def test_supplied_device_is_reused(self):
        device = Device()
        run = self.run_batch(device=device)
        self.assertEqual(Device.instances, [])
        self.assertTrue(all(actual is device for actual in run.devices))
        self.assertIs(device.page, page_main)

    def test_no_active_events_do_not_create_device_or_navigate(self):
        run = self.run_batch(modes=())
        self.assertTrue(run.result)
        self.assertEqual(run.actions, [])
        self.assertEqual(Device.instances, [])
        run.next_check.assert_called_once()

    def test_all_checked_do_not_create_device_or_navigate(self):
        run = self.run_batch(checked=MODES)
        self.assertTrue(run.result)
        self.assertEqual(run.actions, [])
        self.assertEqual(Device.instances, [])
        run.next_check.assert_called_once()

    def test_all_disabled_do_not_create_device_or_navigate(self):
        run = self.run_batch(disabled=MODES)
        self.assertTrue(run.result)
        self.assertEqual(run.actions, [])
        self.assertEqual(Device.instances, [])
        run.next_check.assert_called_once()

    def test_skipped_tail_does_not_lose_final_return(self):
        modes = ('free_gacha_20', 'e7wc_battle_gate', 'koharu_raffle')
        run = self.run_batch(modes=modes, checked=(modes[1],), disabled=(modes[2],))
        self.assertEqual([value for action, value in run.actions if action == 'claim'], [MODES[0]])
        self.assertEqual(run.routes, [(page_main, page_common_activity), (page_common_activity, page_main)])

    def test_middle_failure_stops_remaining_claims_and_keeps_retry(self):
        modes = ('free_gacha_20', 'e7wc_battle_gate', 'koharu_raffle')
        run = self.run_batch(modes=modes, failed=modes[1])
        self.assertFalse(run.result)
        self.assertEqual([value for action, value in run.actions if action == 'claim'], list(modes[:2]))
        self.assertEqual(run.routes, [(page_main, page_common_activity)])
        self.assertEqual(run.config.delays[-1], {'success': False})
        run.next_check.assert_not_called()

    def test_first_failure_does_not_navigate_away_or_run_later_events(self):
        run = self.run_batch(failed=MODES[0])
        self.assertFalse(run.result)
        self.assertEqual([value for action, value in run.actions if action == 'claim'], [MODES[0]])
        self.assertEqual(run.routes, [(page_main, page_common_activity)])

    def test_legacy_keeps_its_own_exit_without_an_extra_return(self):
        run = self.run_batch(modes=(MODES[0], 'legacy'), entry_cls=SpecialActivityEntry)
        self.assertTrue(run.result)
        self.assertEqual(run.actions, [('legacy', None)])
        self.assertIs(run.entry.device.page, page_main)
        run.next_check.assert_called_once()
