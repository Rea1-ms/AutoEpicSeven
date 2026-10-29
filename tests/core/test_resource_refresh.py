"""Fresh resource observations drive burnout, never an old dashboard value."""

import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.config import server
from module.config.stored.classes import StoredStamina

server.set_lang("global_cn")

from tasks.base.resource_bar import ResourceBarSpec, ResourceBarValue
from tasks.base.resource_scheduling import RESOURCE_STATE_PATH, ResourceCoordinator
from tasks.dungeon.dungeon import Combat
from tests.support.resources import ResourceConfig, resource_values


class ResourceRefreshTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 26, 13, 26, 44)
        for name in ("tasks.base.resource_scheduling.local_now", "tasks.dungeon.burnout.local_now",
                     "tests.support.resources.local_now", "module.config.stored.classes.now"):
            mocked = patch(name, side_effect=lambda: self.now)
            mocked.start()
            self.addCleanup(mocked.stop)
        self.config = ResourceConfig()
        self.config.task.command = "Combat"
        self.config.auto_update = False
        self.config.cross_set("DataUpdate.Dashboard.Stamina", {
            "value": 415, "total": 360, "time": self.now - timedelta(minutes=84),
        })
        stamina = StoredStamina("DataUpdate.Dashboard.Stamina")
        stamina._bind(self.config)
        self.config.stored = SimpleNamespace(Stamina=stamina)
        self.sync = ResourceCoordinator(self.config)
        self.task = object.__new__(Combat)
        self.task.config = self.config
        self.task._combat_burnout_enabled = Mock(return_value=True)
        self.task._combat_burnout_batch_need = Mock(return_value=80)
        self.task._uses_server_repeat_combat = Mock(return_value=True)
        self.task.ocr_resource_bar_status = Mock(return_value=None)

    def next_run(self, task="Combat"):
        return self.config.cross_get(f"{task}.Scheduler.NextRun")

    def repeat_amount(self, value):
        parsed = {"stamina": ResourceBarValue(ResourceBarSpec("stamina", "int"), str(value), value)}
        self.task.write_resource_bar_status(parsed, source="server_repeat")

    def test_eight_stamina_replaces_old_415_and_waits_for_80(self):
        self.sync.observe(resource_values(stamina=(415, 360)), "Combat")
        self.repeat_amount(8)
        self.assertTrue(self.task._combat_burnout_schedule())
        self.assertEqual(self.config.stored.Stamina.value, 8)
        self.assertEqual(self.config.stored.Stamina.total, 360)
        self.assertEqual(self.config.stored.Stamina.time, self.now)
        self.assertEqual(self.next_run(), self.now + timedelta(minutes=72 * 4))
        self.assertFalse(self.sync.pending)

    def test_no_observation_never_uses_old_dashboard_to_schedule_one_minute(self):
        full = self.next_run("DataUpdate")
        self.assertTrue(self.task._combat_burnout_schedule())
        self.assertEqual(self.next_run(), self.now + timedelta(minutes=30))
        self.assertEqual(self.next_run("DataUpdate"), self.now)
        self.assertEqual(self.sync.pending, {"stamina"})
        self.assertEqual(self.config.cross_get(RESOURCE_STATE_PATH)["servers"][self.sync.server]["full_due"], full.isoformat())

    def test_consumed_expired_future_and_pending_amounts_cannot_authorize_a_run(self):
        for state in ("consumed", "expired", "future", "pending"):
            with self.subTest(state=state):
                self.sync.observe(resource_values(stamina=(415, 360)), "Main")
                if state == "consumed":
                    self.sync.invalidate("stamina")
                elif state in {"expired", "future"}:
                    runtime = self.config.cross_get(RESOURCE_STATE_PATH)
                    item = runtime["servers"][self.sync.server]["observations"]["stamina"]
                    item["at"] = (self.now + timedelta(seconds=-301 if state == "expired" else 1)).isoformat()
                else:
                    self.sync.request("Mail", keys=("stamina",))
                self.assertTrue(self.task._combat_burnout_schedule())
                self.assertEqual(self.next_run(), self.now + timedelta(minutes=30))
                self.assertIn("stamina", self.sync.pending)

    def test_unknown_capacity_does_not_borrow_unpartitioned_dashboard_cap(self):
        self.repeat_amount(8)
        self.task._combat_burnout_schedule()
        self.assertEqual(self.next_run(), self.now + timedelta(minutes=30))
        self.assertEqual(self.sync.observation("stamina"), None)
        self.assertEqual(self.config.stored.Stamina.value, 8)

    def test_sufficient_integer_amount_does_not_require_unknown_capacity(self):
        self.repeat_amount(80)
        self.task._combat_burnout_schedule()
        self.assertEqual(self.next_run(), self.now + timedelta(minutes=1))

    def test_zero_is_fresh_evidence_and_waits_for_full_batch(self):
        self.sync.observe(resource_values(stamina=(0, 360)), "Main")
        self.repeat_amount(0)
        self.task._combat_burnout_schedule()
        self.assertEqual(self.next_run(), self.now + timedelta(minutes=320))

    def test_capacity_below_batch_does_not_block_sufficient_overflow(self):
        self.sync.observe(resource_values(stamina=(80, 50)), "Main")
        self.task._combat_burnout_schedule()
        self.assertEqual(self.next_run(), self.now + timedelta(minutes=1))

    def test_new_partial_main_stamina_takes_precedence_over_menu(self):
        self.sync.observe(resource_values(stamina=(8, 360)), "server_repeat")
        self.task.ocr_resource_bar_status.return_value = resource_values(stamina=(80, 360))
        self.task._combat_burnout_schedule()
        self.assertEqual(self.next_run(), self.now + timedelta(minutes=1))
        self.assertEqual(self.config.stored.Stamina.value, 80)

    def test_skystone_only_success_does_not_count_as_stamina_refresh(self):
        self.task.ocr_resource_bar_status.return_value = resource_values(skystone=2122)
        self.assertFalse(self.task._combat_burnout_refresh_status())
        self.assertEqual(self.sync.observation("skystone")["value"], 2122)
        self.task._combat_burnout_schedule()
        self.assertEqual(self.next_run(), self.now + timedelta(minutes=30))

    def test_recovery_keeps_original_observation_time_and_partial_tick(self):
        self.sync.observe(resource_values(stamina=(8, 360)), "Main")
        recorded_at = self.now
        self.now += timedelta(minutes=2)
        self.task._combat_burnout_schedule()
        self.assertEqual(self.next_run(), recorded_at + timedelta(minutes=288))

    def test_capacity_survives_integer_reads_without_faking_their_total(self):
        self.sync.observe(resource_values(stamina=(415, 360)), "Main")
        self.sync.invalidate("stamina")
        for value in (8, 9):
            self.repeat_amount(value)
            item = self.sync.observation("stamina")
            self.assertEqual((item["value"], item["total"], item["capacity"]), (value, 0, 360))
        self.sync.observe(resource_values(stamina=(9, 370)), "Main")
        self.repeat_amount(10)
        self.assertEqual(self.sync.observation("stamina")["capacity"], 370)

    def test_capacity_is_not_shared_between_servers_or_languages(self):
        for attribute, changed in (("Emulator_GameLanguage", "en"), ("Emulator_PackageName", "com.zlongame.cn.epicseven")):
            with self.subTest(attribute=attribute):
                original = getattr(self.config, attribute)
                self.sync.observe(resource_values(stamina=(415, 360)), "Main")
                setattr(self.config, attribute, changed)
                self.repeat_amount(8)
                self.assertEqual(ResourceCoordinator(self.config).observation("stamina")["capacity"], 0)
                setattr(self.config, attribute, original)

    def test_legacy_observation_total_migrates_to_separate_capacity(self):
        self.sync.observe(resource_values(stamina=(415, 360)), "Main")
        runtime = self.config.cross_get(RESOURCE_STATE_PATH)
        runtime["servers"][self.sync.server]["observations"]["stamina"].pop("capacity")
        self.repeat_amount(8)
        self.assertEqual(self.sync.observation("stamina")["capacity"], 360)

    def test_unknown_retry_keeps_refresh_failure_cooldown(self):
        self.task._combat_burnout_schedule()
        self.sync.finish_refresh()
        retry = self.next_run("DataUpdate")
        self.now += timedelta(minutes=1)
        self.task._combat_burnout_schedule()
        self.assertEqual(self.next_run("DataUpdate"), retry)

    def test_integer_observation_shortens_known_resource_wait(self):
        self.sync.observe(resource_values(stamina=(0, 360)), "Main")
        self.sync.wait("Combat", "stamina", 80)
        self.repeat_amount(8)
        self.assertEqual(self.next_run(), self.now + timedelta(minutes=288))

    def test_partial_shop_snapshot_without_required_currency_remains_unknown(self):
        from tasks.activity.legacy.huche_shop_2026_09_17.huche_shop import HucheShop
        from tasks.store.friendship_gift import FriendshipGiftMixin

        reader = FriendshipGiftMixin()
        reader.inspect_resource_bar_status = Mock(return_value=SimpleNamespace(final=resource_values(skystone=100)))
        self.assertIsNone(reader._balance())
        reader = object.__new__(HucheShop)
        reader.inspect_resource_bar_status = Mock(return_value=SimpleNamespace(final=resource_values(gold=100)))
        self.assertIsNone(reader.read_balance())
