# ruff: noqa: E402
"""Current inventory-counter rules; obsolete contracts stay in the inventory."""

import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.config import server

server.set_lang('global_cn')

from tasks.base.resource_bar import ResourceBarMixin
from tasks.base.resource_scheduling import (
    RESOURCE_STATE_PATH,
    ResourceCoordinator,
    local_now,
)
from tasks.item.data_update import DataUpdate, E7DigitCounter, normalize_e7_counter_text
from tests.support.resources import ResourceConfig, resource_values


class LegacyRuleTests(unittest.TestCase):
    def test_normalize_e7_counter_text(self):
        self.assertEqual(normalize_e7_counter_text(" 1,026 / 1,000 "), "1026/1000")
        self.assertEqual(normalize_e7_counter_text("250／500"), "250/500")
        self.assertEqual(normalize_e7_counter_text("8O6/1OOO"), "806/1000")

    def test_parse_e7_counter_text(self):
        # Keep the migration ID while exercising the actual inventory reader,
        # counter postprocessor, and parser instead of a parallel text helper.
        updater = DataUpdate.__new__(DataUpdate)
        updater.config = SimpleNamespace(Emulator_GameLanguage="auto")
        updater.device = SimpleNamespace(image=object())
        for raw, expected in (
            ("1026/1000", (1026, -26, 1000)),
            ("806/1000", (806, 194, 1000)),
            ("250/500", (250, 250, 500)),
            (" 1,026 ／ 1,000 ", (1026, -26, 1000)),
            ("8O6/1OOO", (806, 194, 1000)),
            ("invalid", (0, 0, 0)),
        ):
            with self.subTest(raw=raw), patch.object(
                E7DigitCounter, "ocr_single_line", autospec=True,
                side_effect=lambda reader, image: reader.format_result(reader.after_process(raw)),
            ):
                self.assertEqual(updater._ocr_equipment_inventory_count(), expected)


class ResourceCoordinationTests(unittest.TestCase):
    def setUp(self):
        self.config = ResourceConfig()
        self.sync = ResourceCoordinator(self.config)

    def wait(self, task="Arena", key="arena_flag", need=5):
        self.sync.wait(task, key, need)
        return self.config.cross_get(f"{task}.Scheduler.NextRun")

    def next_run(self, task="Arena"):
        return self.config.cross_get(f"{task}.Scheduler.NextRun")

    def test_store_five_flags_wake_arena_once_and_restore_full_scan(self):
        full = self.next_run("DataUpdate")
        original = self.wait()
        self.sync.request("Store")
        self.assertTrue(self.sync.lightweight_refresh())
        self.sync.observe(resource_values(arena_flag=(5, 5), stamina=(0, 336), skystone=100), "Main")
        target = self.next_run()
        self.assertLess(target, original)
        self.assertLessEqual(target, local_now())
        self.assertEqual(self.next_run("DataUpdate"), full)
        self.assertFalse(self.sync.pending)
        self.sync.observe(resource_values(arena_flag=(5, 5)), "Arena")
        self.assertEqual(self.next_run(), target)

    def test_partial_flags_shorten_recovery_without_starting_immediately(self):
        original = self.wait()
        self.sync.observe(resource_values(arena_flag=(1, 5)), "Main")
        self.assertLess(self.next_run(), original)
        self.assertAlmostEqual((self.next_run() - local_now()).total_seconds(), 4 * 3600, delta=3)

    def test_stamina_below_server_batch_only_shortens_wait(self):
        self.wait("Combat", "stamina", 80)
        self.sync.observe(resource_values(stamina=(40, 336)), "Main")
        self.assertAlmostEqual((self.next_run("Combat") - local_now()).total_seconds(), 40 * 240, delta=3)
        self.sync.observe(resource_values(stamina=(80, 336)), "Main")
        self.assertLessEqual(self.next_run("Combat"), local_now())

    def test_server_repeat_keeps_full_unit_when_natural_cap_is_too_low(self):
        from tasks.dungeon.dungeon import Combat

        task = object.__new__(Combat)
        task.config = self.config
        self.config.task.command = "Combat"
        self.config.stored = SimpleNamespace(Stamina=SimpleNamespace(total=50))
        task._combat_burnout_enabled = Mock(return_value=True)
        task._combat_burnout_batch_need = Mock(return_value=80)
        task._uses_server_repeat_combat = Mock(return_value=True)
        task._combat_burnout_refresh_status = Mock()
        self.assertTrue(task._combat_burnout_schedule())
        original = self.next_run("Combat")
        self.sync.observe(resource_values(stamina=(60, 50)), "Main")
        self.assertEqual(self.next_run("Combat"), original)
        self.sync.observe(resource_values(stamina=(80, 50)), "Main")
        self.assertLessEqual(self.next_run("Combat"), local_now())

    def test_failed_read_keeps_request_and_original_consumer_time(self):
        original = self.wait()
        self.sync.request("PetsGift")
        self.sync.observe(None, "Main")
        self.assertEqual(self.next_run(), original)
        self.assertEqual(self.sync.pending, {"arena_flag", "stamina", "skystone"})

    def test_partial_observation_clears_only_corresponding_request(self):
        self.sync.request("Mail")
        self.sync.observe(resource_values(stamina=(120, 336)), "Combat")
        self.assertEqual(self.sync.pending, {"arena_flag", "skystone"})

    def test_empty_pet_reward_does_not_create_arena_or_combat_runs(self):
        arena = self.wait()
        combat = self.wait("Combat", "stamina", 80)
        # Start with the same resource-based deadlines that the fresh zero
        # observation would predict, rather than an artificial longer wait.
        self.config.task_delay(task="Arena", minute=300)
        self.config.task_delay(task="Combat", minute=320)
        arena = self.wait()
        combat = self.wait("Combat", "stamina", 80)
        self.sync.request("PetsGift")
        self.sync.observe(resource_values(arena_flag=(0, 5), stamina=(0, 336), skystone=100), "Main")
        self.assertEqual(self.next_run(), arena)
        self.assertEqual(self.next_run("Combat"), combat)

    def test_disabled_consumer_is_not_enabled_by_resources(self):
        original = self.wait()
        self.config.cross_set("Arena.Scheduler.Enable", False)
        self.sync.observe(resource_values(arena_flag=(5, 5)), "Main")
        self.assertEqual(self.next_run(), original)
        self.assertFalse(self.config.is_task_enabled("Arena"))

    def test_failure_or_user_time_edit_invalidates_old_resource_wait(self):
        self.wait()
        target = local_now().replace(microsecond=0) + timedelta(minutes=30)
        self.config.cross_set("Arena.Scheduler.NextRun", target)
        self.sync.observe(resource_values(arena_flag=(5, 5)), "Main")
        self.assertEqual(self.next_run(), target)

    def test_route_edit_invalidates_resource_wait(self):
        original = self.wait()
        self.config.cross_set("Arena.Arena.NPCCombat", False)
        self.sync.observe(resource_values(arena_flag=(5, 5)), "Main")
        self.assertEqual(self.next_run(), original)

    def test_task_begin_prevents_self_rescheduling(self):
        original = self.wait()
        self.sync.begin("Arena")
        self.sync.observe(resource_values(arena_flag=(5, 5)), "Arena")
        self.assertEqual(self.next_run(), original)

    def test_fixed_quota_without_resource_wait_is_not_reopened(self):
        original = self.next_run()
        self.sync.observe(resource_values(arena_flag=(50, 5)), "Main")
        self.assertEqual(self.next_run(), original)

    def test_active_background_repeat_keeps_settlement_schedule(self):
        original = self.wait("Combat", "stamina", 80)
        self.config.cross_set("Combat.CombatRuntime.Session", {"active": True, "mode": "repeat_server"})
        self.sync.observe(resource_values(stamina=(800, 336)), "Main")
        self.assertEqual(self.next_run("Combat"), original)

    def test_pending_payment_on_same_server_blocks_replacement(self):
        original = self.wait()
        self.config.cross_set("MissionReward.MissionReward.DailyBudget", {"contexts": {
            "old-day": {"server": self.sync.server, "executors": {"Arena": {"pending": {"count": 1}}}},
        }})
        self.sync.observe(resource_values(arena_flag=(5, 5)), "Main")
        self.assertEqual(self.next_run(), original)

    def test_data_update_disabled_does_not_block_local_resource_sync(self):
        self.config.cross_set("DataUpdate.Scheduler.Enable", False)
        self.wait()
        full = self.next_run("DataUpdate")
        self.sync.request("Mail")
        self.sync.observe(resource_values(arena_flag=(5, 5), stamina=(0, 336), skystone=10), "Main")
        self.assertLessEqual(self.next_run(), local_now())
        self.assertEqual(self.next_run("DataUpdate"), full)
        self.assertFalse(self.config.is_task_enabled("DataUpdate"))

    def test_failure_cooldown_survives_further_gifts_and_full_due_is_kept(self):
        full = self.next_run("DataUpdate")
        self.sync.request("Store")
        self.sync.finish_refresh()
        retry = self.next_run("DataUpdate")
        self.sync.request("PetsGift")
        self.assertEqual(self.next_run("DataUpdate"), retry)
        self.assertEqual(self.config.cross_get(RESOURCE_STATE_PATH)["servers"][self.sync.server]["full_due"], full.isoformat())

    def test_language_and_server_changes_do_not_reuse_resource_evidence(self):
        self.sync.observe(resource_values(arena_flag=(12, 5)), "Main")
        self.assertEqual(self.sync.observation("arena_flag")["value"], 12)
        self.config.Emulator_GameLanguage = "en"
        self.assertIsNone(ResourceCoordinator(self.config).observation("arena_flag"))
        self.config.Emulator_PackageName = "com.zlongame.cn.epicseven"
        self.assertIsNone(ResourceCoordinator(self.config).observation("arena_flag"))

    def test_local_estimate_invalidates_evidence_without_faking_a_read_time(self):
        self.sync.observe(resource_values(stamina=(336, 336)), "Main")
        at = self.sync.observation("stamina")["at"]
        self.sync.invalidate("stamina")
        self.assertIsNone(self.sync.observation("stamina"))
        stored = self.config.cross_get(RESOURCE_STATE_PATH)["servers"][self.sync.server]["observations"]["stamina"]
        self.assertEqual(stored["at"], at)

    def test_zero_overflow_and_repeat_observation_revision_are_preserved(self):
        self.sync.observe(resource_values(stamina=(0, 336), arena_flag=(26, 5)), "Main")
        self.assertEqual(self.sync.observation("stamina")["value"], 0)
        self.assertEqual(self.sync.observation("arena_flag")["value"], 26)
        revision = self.sync.observation("arena_flag")["revision"]
        self.sync.observe(resource_values(arena_flag=(26, 5)), "Arena")
        self.assertEqual(self.sync.observation("arena_flag")["revision"], revision)

    def test_lightweight_data_update_skips_inventory_and_arena_detours(self):
        self.sync.request("Store")
        updater = object.__new__(DataUpdate)
        updater.config = self.config
        updater.device = SimpleNamespace(image=object(), app_is_running=lambda: True)
        updater._update_equipment_inventory = Mock()
        updater._update_combat_status = Mock()
        updater._update_arena_status = Mock()
        updater.ui_goto = Mock(side_effect=lambda *args, **kwargs: self.sync.observe(
            resource_values(arena_flag=(5, 5), stamina=(80, 336), skystone=1), "Main",
        ))
        self.assertTrue(updater.run())
        updater._update_equipment_inventory.assert_not_called()
        updater._update_arena_status.assert_not_called()
        updater._update_combat_status.assert_not_called()

    def test_full_scan_due_is_not_replaced_by_lightweight_mode(self):
        self.config.cross_set("DataUpdate.Scheduler.NextRun", local_now() - timedelta(minutes=1))
        self.sync.request("Store")
        self.assertFalse(self.sync.lightweight_refresh())

    def test_login_sync_does_not_turn_lightweight_refresh_into_full_scan(self):
        self.sync.request("Store")
        updater = object.__new__(DataUpdate)
        updater.config = self.config
        updater.device = SimpleNamespace(image=object(), app_is_running=lambda: False)
        updater.ui_goto = Mock()
        updater._update_equipment_inventory = Mock(return_value=True)
        updater._update_combat_status = Mock(return_value=True)
        updater._update_arena_status = Mock(return_value=True)
        with patch("tasks.login.login.Login") as login:
            login.return_value.app_start.side_effect = lambda: self.sync.observe(
                resource_values(arena_flag=(5, 5), stamina=(80, 336), skystone=1), "Main",
            )
            self.assertTrue(updater.run())
        updater._update_equipment_inventory.assert_not_called()
        updater._update_combat_status.assert_not_called()
        updater._update_arena_status.assert_not_called()

    def test_common_resource_write_updates_evidence_and_consumer_time(self):
        self.wait()
        reader = ResourceBarMixin()
        reader.config = self.config
        counter = SimpleNamespace(set=Mock())
        self.config.stored = SimpleNamespace(ArenaFlag=counter)
        self.assertTrue(reader.write_resource_bar_status(resource_values(arena_flag=(5, 5)), source="Arena"))
        counter.set.assert_called_once_with(5, 5)
        self.assertLessEqual(self.next_run(), local_now())

    def test_home_refresh_requires_fresh_unobscured_page(self):
        self.sync.request("PetsGift")
        reader = ResourceBarMixin()
        reader.config = self.config
        reader.device = SimpleNamespace(screenshot=Mock())
        reader.is_in_main = Mock(return_value=False)
        reader.inspect_resource_bar_status = Mock()
        self.assertFalse(reader.sync_pending_resources())
        reader.device.screenshot.assert_called_once()
        reader.inspect_resource_bar_status.assert_not_called()

    def test_language_change_is_persisted_before_switching_back(self):
        self.sync.observe(resource_values(stamina=(100, 336)), "Main")
        self.config.Emulator_GameLanguage = "en"
        self.assertIsNone(ResourceCoordinator(self.config).observation("stamina"))
        self.config.Emulator_GameLanguage = "cn"
        self.assertIsNone(ResourceCoordinator(self.config).observation("stamina"))

    def test_pending_pet_gift_is_saved_before_click_without_daily_progress(self):
        from tasks.base.ui import UI

        task = object.__new__(UI)
        task.config = self.config
        task.appear = Mock(return_value=False)
        task.device = SimpleNamespace(click=Mock())
        self.config.task_call = Mock()
        self.assertFalse(task.handle_menu_pets_gift())
        self.assertFalse(self.sync.pending)
        task.appear.return_value = True
        task.device.click.side_effect = lambda button: self.assertEqual(
            self.sync.pending, {"arena_flag", "stamina", "skystone"},
        )
        self.assertTrue(task.handle_menu_pets_gift())
        self.config.task_call.assert_not_called()

    def test_mail_marks_refresh_before_receive_click(self):
        from tasks.mail.mail import Mail

        task = object.__new__(Mail)
        task.config = self.config
        task._ocr_remaining_mail_count_state = Mock(return_value=object())
        task._receive_available = Mock(return_value=True)
        task.interval_is_reached = Mock(return_value=True)
        task.interval_reset = Mock()
        task._handle_mail_reward_flow = Mock(return_value=False)
        task.device = SimpleNamespace(click=Mock(side_effect=lambda button: self.assertTrue(self.sync.pending)))
        self.assertFalse(task._claim_top_mail(object()))
        self.assertTrue(self.sync.pending)

    def test_partial_store_failure_keeps_pending_refresh(self):
        from tasks.store.current import CurrentStore
        from tasks.store.purchase import ItemPurchasePlan

        task = object.__new__(CurrentStore)
        task.config = self.config
        item = ItemPurchasePlan("test-item", object(), single_purchase=True)
        task._purchase_friendship_gift = Mock(side_effect=lambda plan: self.assertTrue(self.sync.pending) or False)
        self.assertFalse(task._purchase_item(item))
        self.assertEqual(self.sync.pending, {"arena_flag", "stamina", "skystone"})
