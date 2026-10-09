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
from tests.support.history_fixtures import build_combat


from tests.support.history_urgent_tasks_first_clear import Daily

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
