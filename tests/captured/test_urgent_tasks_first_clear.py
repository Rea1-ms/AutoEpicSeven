# ruff: noqa: E402
from module.config import server as _test_server
_test_server.set_lang("global_cn")

"""Offline regressions for gray repeat controls before Urgent Tasks fast combat."""
import unittest
from pathlib import Path
from types import SimpleNamespace

WORKTREE = Path(__file__).resolve().parents[2]
import module.config.server as server
from module.exception import RequestHumanTakeover
from tasks.dungeon.repeat import CombatRepeatMixin
from tasks.dungeon.urgent_tasks import UrgentTasksNavigateMixin
from tests.support.history_september_runtime_fixes import Prepare
from tests.support.history_fixtures import build_combat


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




    def test_screenshot_fast_state_decides_whether_first_normal_clear_is_needed(self):
        server.set_lang('global_cn')
        for name, normal_count in [('superior-fast-off.png', 1), ('superior-fast-on.png', 0)]:
            with self.subTest(name=name):
                combat = build_combat(name, 'Superior')
                combat.config.Emulator_PackageName = 'com.stove.epic7.google'
                combat.device.screenshot = lambda: None
                combat.device.screenshot()
                daily = Daily(pet=False)
                daily._is_repeat_combat_unavailable = combat._is_repeat_combat_unavailable
                daily._is_fast_combat_on = combat._is_fast_combat_on
                self.assertEqual(daily._run_urgent_tasks_daily(), (True, 5))
                self.assertEqual(daily.normal_clears, normal_count)
                self.assertEqual(daily.steps, ['normal'] * normal_count + ['fast_prepare', 'fast'])


class EnablePrepare(Prepare):
    def _is_fast_combat_off(self):
        return self.frame == 'off'
    def _is_prepare_page(self):
        return self.frame in ('off','ready','unknown_toggle')
    def _ensure_fast_combat_state(self, enabled):
        if self.frame == 'off':
            self.actions.append('enable')
        return self.frame == 'ready'




class ScreenshotAndPolicyTests(unittest.TestCase):
    def setUp(self):
        server.set_lang('global_cn')
    def test_real_gray_repeat_is_detected_in_fast_off_and_on_screenshots(self):
        for name,enabled in [('superior-fast-off.png',False),('superior-fast-on.png',True)]:
            combat = build_combat(name,'Superior')
            combat.config.Emulator_PackageName = 'com.stove.epic7.google'
            self.assertTrue(combat._is_prepare_page())
            self.assertTrue(combat._is_repeat_combat_unavailable())
            self.assertEqual(combat._is_fast_combat_on(),enabled)
    def test_ordinary_repeat_still_raises_when_gray(self):
        probe = SimpleNamespace(_is_repeat_combat_unavailable=lambda: True)
        with self.assertRaises(RequestHumanTakeover):
            CombatRepeatMixin._raise_if_repeat_combat_unavailable(probe)
        probe._is_repeat_combat_unavailable = lambda: False
        CombatRepeatMixin._raise_if_repeat_combat_unavailable(probe)
