# ruff: noqa: E402
from module.config import server as _test_server

_test_server.set_lang("global_cn")

"""Offline regressions for gray repeat controls before Urgent Tasks fast combat."""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

WORKTREE = Path(__file__).resolve().parents[2]
PROJECT_ROOT = WORKTREE
from tasks.dungeon.urgent_tasks import UrgentTasksNavigateMixin
from tests.support.history_september_runtime_fixes import Prepare, timers


class Daily(UrgentTasksNavigateMixin):
    def __init__(self, remaining=5, gray=True, fast_status='ready', pet=True, normal_success=True):
        self.remaining = remaining
        self.gray = gray
        self.fast_status = fast_status
        self.pet = pet
        self.normal_success = normal_success
        self.normal_clears = 0
        self.checked = 0
        self.steps = []
        self.fast_calls = []
        self.stamina = 500
        self.unlock_after_normal = False
        self.fast_enabled = True
        self.fast_on = False
        self.device = SimpleNamespace(screenshot=lambda: None)
        self.device.screenshot()
    def _navigate_urgent_tasks(self, **kwargs):
        self._urgent_tasks_remaining_count = self.remaining
        self._urgent_tasks_unavailable = False
        self._urgent_tasks_daily_complete = self.remaining == 0
        return True
    def _mark_urgent_tasks_checked(self):
        self.checked += 1
    def _exchange_urgent_tasks_rewards(self, **kwargs):
        return True
    def _read_urgent_tasks_stamina(self, **kwargs):
        return self.stamina
    def _combat_stage_stamina_cost(self):
        return 30
    def _is_repeat_combat_unavailable(self):
        return self.gray
    def _is_fast_combat_on(self):
        return self.fast_on
    def _combat_should_use_fast(self):
        return self.fast_enabled
    def _prepare_fast_combat(self, **kwargs):
        self.steps.append('fast_prepare')
        self.fast_calls.append(kwargs)
        return self.fast_status, self.remaining if self.fast_status == 'ready' else 0
    def _run_fast_combat(self, **kwargs):
        self.steps.append('fast')
        self.remaining = 0
        return True
    def _urgent_tasks_prepare_has_pet(self, **kwargs):
        self.steps.append('pet')
        return self.pet
    def _server_repeat_target_leif_count(self, stamina):
        return 1
    def _prepare_repeat_combat(self, **kwargs):
        self.steps.append('repeat_prepare')
        return True
    def _run_repeat_combat(self, **kwargs):
        self.steps.append('repeat')
        return True
    def _is_urgent_tasks_detail_page(self):
        return True
    def _run_normal_combat(self, **kwargs):
        self.steps.append('normal')
        if not self.normal_success:
            return False
        self.normal_clears += 1
        self.remaining -= 1
        if self.unlock_after_normal:
            self.gray = False
        return True


class DailyTests(unittest.TestCase):
    def test_gray_five_attempts_runs_normal_before_fast_even_with_pet(self):
        daily = Daily()
        self.assertEqual(daily._run_urgent_tasks_daily(), (True,5))
        self.assertEqual(daily.steps, ['normal','fast_prepare','fast'])
        self.assertEqual(daily.fast_calls[0]['stamina'],120)
        self.assertTrue(daily.fast_calls[0]['fallback_on_enable_timeout'])
        self.assertEqual(daily.checked,1)
    def test_restart_with_hint_already_dismissed_still_clears_normally(self):
        daily = Daily()
        daily._urgent_tasks_times_hint_dismissed = True
        daily._urgent_tasks_repeat_started = True
        self.assertEqual(daily._run_urgent_tasks_daily(), (True,5))
        self.assertEqual(daily.steps[0],'normal')
        self.assertFalse(daily._urgent_tasks_repeat_started)
    def test_gray_stays_gray_after_clear_but_fast_can_run_without_pet(self):
        daily = Daily(pet=False, remaining=3)
        self.assertEqual(daily._run_urgent_tasks_daily(), (True,3))
        self.assertTrue(daily.gray)
        self.assertEqual(daily.normal_clears,1)
        self.assertEqual(daily.steps,['normal','fast_prepare','fast'])
        self.assertEqual(daily.fast_calls[0]['stamina'],60)
    def test_failed_fast_enable_uses_normal_for_remaining_attempts(self):
        daily = Daily(fast_status='fallback')
        self.assertEqual(daily._run_urgent_tasks_daily(), (True,5))
        self.assertEqual(daily.normal_clears,5)
        self.assertEqual(len(daily.fast_calls),1)
        self.assertNotIn('repeat',daily.steps)
    def test_failed_fast_enable_does_not_switch_to_repeat_even_if_unlocked(self):
        daily = Daily(fast_status='fallback')
        daily.unlock_after_normal = True
        self.assertEqual(daily._run_urgent_tasks_daily(), (True,5))
        self.assertEqual(daily.normal_clears,5)
        self.assertNotIn('repeat_prepare',daily.steps)
    def test_available_repeat_does_not_force_first_normal_clear(self):
        daily = Daily(gray=False)
        self.assertEqual(daily._run_urgent_tasks_daily(), (True,5))
        self.assertEqual(daily.steps,['fast_prepare','fast'])
    def test_normal_clear_failure_does_not_try_fast_or_mark_checked(self):
        daily = Daily(normal_success=False)
        self.assertEqual(daily._run_urgent_tasks_daily(), (False,0))
        self.assertEqual(daily.steps,['normal'])
        self.assertEqual(daily.checked,0)
    def test_counter_or_resource_failure_is_not_silently_replaced_by_normal(self):
        daily = Daily(fast_status='failed')
        self.assertEqual(daily._run_urgent_tasks_daily(), (False,1))
        self.assertEqual(daily.normal_clears,1)
        self.assertEqual(daily.checked,0)
    def test_fast_disabled_preserves_normal_completion(self):
        daily = Daily(pet=False)
        daily.fast_enabled = False
        self.assertEqual(daily._run_urgent_tasks_daily(), (True,5))
        self.assertEqual(daily.normal_clears,5)
        self.assertEqual(daily.fast_calls,[])
    def test_no_stamina_does_not_launch_first_clear(self):
        daily = Daily()
        daily.stamina = 29
        self.assertEqual(daily._run_urgent_tasks_daily(), (False,0))
        self.assertEqual(daily.steps,[])
        self.assertEqual(daily.checked,0)

    def test_gray_repeat_with_fast_already_on_skips_first_normal_clear(self):
        for pet in (False, True):
            with self.subTest(pet=pet):
                daily = Daily(pet=pet)
                daily.fast_on = True
                self.assertEqual(daily._run_urgent_tasks_daily(), (True, 5))
                self.assertEqual(daily.steps, ['fast_prepare', 'fast'])
                self.assertEqual(daily.normal_clears, 0)
                self.assertEqual(daily.fast_calls[0]['stamina'], 150)
                self.assertTrue(daily.fast_calls[0]['fallback_on_enable_timeout'])
                self.assertEqual(daily.checked, 1)

    def test_fast_already_on_still_respects_disabled_fast_setting(self):
        daily = Daily(pet=False)
        daily.fast_on = True
        daily.fast_enabled = False
        self.assertEqual(daily._run_urgent_tasks_daily(), (True, 5))
        self.assertEqual(daily.normal_clears, 5)
        self.assertEqual(daily.fast_calls, [])

    def test_fast_already_on_but_exhausted_uses_normal_for_remaining_attempts(self):
        daily = Daily(pet=False, fast_status='fallback')
        daily.fast_on = True
        self.assertEqual(daily._run_urgent_tasks_daily(), (True, 5))
        self.assertEqual(daily.steps[0], 'fast_prepare')
        self.assertEqual(daily.normal_clears, 5)
        self.assertEqual(len(daily.fast_calls), 1)



class EnablePrepare(Prepare):
    def _is_fast_combat_off(self):
        return self.frame == 'off'
    def _is_prepare_page(self):
        return self.frame in ('off','ready','unknown_toggle')
    def _ensure_fast_combat_state(self, enabled):
        if self.frame == 'off':
            self.actions.append('enable')
        return self.frame == 'ready'


class EnableTests(unittest.TestCase):
    def prepare(self, frames, fallback=True):
        prepare = EnablePrepare(frames)
        with patch('tasks.dungeon.prepare.Timer', timers(lambda: prepare.index)):
            result = prepare._prepare_fast_combat(100, fallback_on_enable_timeout=fallback)
        return prepare, result
    def test_disabled_off_toggle_has_bounded_retries(self):
        prepare,result = self.prepare(['off'] * 5)
        self.assertEqual(result,('fallback',0))
        self.assertEqual(prepare.index,3)
        self.assertEqual(prepare.actions,['enable'] * 3)
    def test_late_on_after_loading_is_accepted(self):
        prepare,result = self.prepare(['off','loading','loading','loading','ready'])
        self.assertEqual(result,('ready',5))
        self.assertEqual(prepare.actions,['enable',('count',5)])
    def test_unknown_toggle_does_not_consume_first_enable_attempt(self):
        prepare,result = self.prepare(['unknown_toggle'] * 4 + ['off','ready'])
        self.assertEqual(result,('ready',5))
        self.assertEqual(prepare.actions,['enable',('count',5)])
    def test_ordinary_prepare_keeps_existing_timeout_failure(self):
        _,result = self.prepare(['off'] * 20, fallback=False)
        self.assertEqual(result,('failed',0))
