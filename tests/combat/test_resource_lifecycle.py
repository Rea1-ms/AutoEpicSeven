"""Consumption invalidates observations only when a real control is dispatched."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from module.config import server

server.set_lang("global_cn")

from tasks.base.resource_bar import ResourceBarValue
from tasks.base.resource_scheduling import ResourceCoordinator
from tasks.dungeon.dungeon import Combat
from tasks.dungeon.repeat import SERVER_REPEAT_RESOURCE_SPECS
from tests.support.interaction import InteractionReplay, scene
from tests.support.offline import ControlledClock
from tests.support.resources import ResourceConfig, resource_values


class ResourceLifecycleTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")
        self.clock = ControlledClock()
        self.clock.__enter__()
        self.addCleanup(self.clock.__exit__, None, None, None)

    def replay(self, frames):
        replay = InteractionReplay(Combat, frames, self.clock, guards=False)
        task = replay.task
        task.config = ResourceConfig()
        task.config.task.command = "Combat"
        task.config.Combat_Domain = "Hunt"
        task.config.Combat_HuntBoss = "Wyvern"
        task.config.Combat_HuntGrade = "Hell"
        task.config.Combat_BurnoutMode = "Burnout"
        task.config.stored = SimpleNamespace(
            Stamina=SimpleNamespace(value=415, total=360),
            Skystone=SimpleNamespace(value=100),
        )
        task._raise_if_package_full = lambda: None
        task._handle_dungeon_additional = lambda: False
        task._handle_dungeon_network_error = lambda **kwargs: False
        task.handle_ui_recovery = lambda: False
        task._is_fast_combat_locked = lambda: False
        task._combat_supports_fast_combat = lambda: True
        task._detect_auto_combat_state = lambda: None
        return replay

    def observe(self, replay, stamina=80):
        coordinator = ResourceCoordinator(replay.task.config)
        coordinator.observe(resource_values(stamina=(stamina, 360), leif=62), "Main")
        return coordinator

    def assert_invalid_before_click(self, replay, names):
        coordinator = self.observe(replay)
        click = replay.click

        def checked(button):
            if button.name in names:
                self.assertIsNone(coordinator.observation("stamina"))
                self.assertIsNone(coordinator.observation("leif"))
            click(button)

        replay.click = checked
        return coordinator

    def test_fast_start_invalidates_before_dispatch_and_preserves_retry_interval(self):
        prepare = scene("REPEAT_COMBAT_MENU", "FAST_COMBAT_ON", "COMBAT_START")
        replay = self.replay(
            [prepare] * 25
            + [scene("FAST_COMBAT_RESULT_CLOSE")] * 3
            + [scene("REPEAT_COMBAT_MENU", "FAST_COMBAT_ON")] * 5
        )
        coordinator = self.assert_invalid_before_click(replay, {"COMBAT_START"})
        self.assertTrue(replay.task._run_fast_combat())
        replay.assert_retried(self, "COMBAT_START", replay.task.COMBAT_START_INTERVAL_SECONDS)
        self.assertTrue(replay.clicks("FAST_COMBAT_RESULT_CLOSE"))
        self.assertIsNone(coordinator.observation("stamina"))

    def test_normal_start_invalidates_before_dispatch_and_preserves_retry_interval(self):
        prepare = scene("REPEAT_COMBAT_MENU", "FAST_COMBAT_OFF", "COMBAT_START")
        replay = self.replay(
            [prepare] * 25 + [scene("COMBAT_RESULT_CLEAR")] * 4 + [scene(page="done")]
        )
        self.assert_invalid_before_click(replay, {"COMBAT_START"})
        self.assertTrue(replay.task._run_normal_combat(lambda: replay.frame["page"] == "done"))
        replay.assert_retried(self, "COMBAT_START", replay.task.COMBAT_START_INTERVAL_SECONDS)
        self.assertEqual(replay.index, len(replay.frames) - 1)

    def test_repeat_start_invalidates_before_dispatch_and_preserves_retry_interval(self):
        menu = scene("REPEAT_COMBAT_MENU_CHECK", "REPEAT_START")
        replay = self.replay([menu] * 8 + [scene("REPEAT_COMBAT_CHECK")])
        self.assert_invalid_before_click(replay, {"REPEAT_START"})
        self.assertTrue(replay.task._run_repeat_combat())
        replay.assert_retried(self, "REPEAT_START", replay.task.COMBAT_START_INTERVAL_SECONDS)
        self.assertEqual(replay.index, len(replay.frames) - 1)

    def test_resumed_repeat_confirmation_invalidates_before_dispatch(self):
        replay = self.replay([scene("POPUP_CONFIRM")] * 7 + [scene("REPEAT_COMBAT_CHECK")])
        self.assert_invalid_before_click(replay, {"POPUP_CONFIRM"})
        self.assertTrue(replay.task._run_repeat_combat())
        replay.assert_retried(self, "POPUP_CONFIRM", 1)
        self.assertEqual(replay.clicks("REPEAT_START"), [])

    def test_repeat_unknown_and_missing_start_do_not_invalidate_observations(self):
        for frame in (scene(), scene("REPEAT_COMBAT_MENU_CHECK")):
            with self.subTest(buttons=frame["buttons"]):
                replay = self.replay([frame] * 245)
                coordinator = self.observe(replay, stamina=8)
                before = coordinator.observation("stamina")
                self.assertFalse(replay.task._run_repeat_combat())
                self.assertEqual(replay.actions, [])
                self.assertEqual(coordinator.observation("stamina"), before)
                self.assertEqual(coordinator.observation("leif")["value"], 62)

    def test_fast_missing_start_does_not_invalidate_observations(self):
        replay = self.replay([scene("FAST_COMBAT_ON")] * 245)
        coordinator = self.observe(replay, stamina=8)
        before = coordinator.observation("stamina")
        self.assertFalse(replay.task._run_fast_combat())
        self.assertEqual(replay.actions, [])
        self.assertEqual(coordinator.observation("stamina"), before)

    def test_eight_stamina_run_keeps_fresh_snapshot_without_starting_combat(self):
        replay = self.replay([scene("REPEAT_COMBAT_MENU_CHECK")])
        task = replay.task
        coordinator = self.observe(replay, stamina=415)
        parsed = {
            key: ResourceBarValue(SERVER_REPEAT_RESOURCE_SPECS[key], str(value), value)
            for key, value in {"stamina": 8, "leif": 62}.items()
        }
        task._rune_balance_enabled = lambda: False
        task._new_daily_combat_budget = lambda: SimpleNamespace(active=False)
        task._prepare_background_repeat_check_context = lambda: None
        task._adopt_existing_background_repeat_combat = lambda: False
        task._urgent_tasks_enabled = lambda: False
        task._prepare_rune_balance_target = lambda: None
        task._is_in_dungeon_context = lambda: True
        task._dungeon_navigate = lambda **kwargs: True
        task._ocr_server_repeat_resources = lambda: parsed
        task._prepare_fast_combat = Mock(return_value=("no_stamina", 0))
        task._run_fast_combat = Mock()
        task._run_repeat_combat = Mock()
        task._leave_to_main = Mock(return_value=True)
        task._combat_should_call_mission_reward = lambda: False
        task._combat_delay_after_settled = Mock()

        self.assertTrue(task.run())
        task._prepare_fast_combat.assert_called_once_with(
            stamina=8, use_max=False, skip_first_screenshot=True,
        )
        task._run_fast_combat.assert_not_called()
        task._run_repeat_combat.assert_not_called()
        task._combat_delay_after_settled.assert_called_once_with()
        self.assertEqual(coordinator.observation("stamina")["value"], 8)
        self.assertEqual(coordinator.observation("stamina")["total"], 0)
        self.assertEqual(coordinator.observation("stamina")["source"], "server_repeat")
        self.assertEqual(task.config.stored.Stamina.value, 8)
        self.assertEqual(task.config.stored.Stamina.total, 360)
        self.assertEqual(replay.actions, [])

    def test_server_panel_snapshot_updates_dashboard_without_closing(self):
        replay = self.replay([scene("REPEAT_COMBAT_MENU_CHECK")])
        task = replay.task
        parsed = {
            key: ResourceBarValue(SERVER_REPEAT_RESOURCE_SPECS[key], str(value), value)
            for key, value in {"stamina": 8, "leif": 62}.items()
        }
        task._ocr_server_repeat_resources = lambda: parsed
        result = task._update_prepare_resource_snapshot()
        self.assertEqual(result, parsed)
        self.assertEqual(task.config.stored.Stamina.value, 8)
        self.assertEqual(task.config.stored.Stamina.total, 360)
        self.assertEqual(ResourceCoordinator(task.config).observation("stamina")["value"], 8)
        self.assertEqual(ResourceCoordinator(task.config).observation("leif")["value"], 62)
        self.assertEqual(replay.actions, [])
        self.assertEqual(replay.index, 0)
