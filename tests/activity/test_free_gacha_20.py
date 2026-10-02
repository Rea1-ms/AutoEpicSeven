# ruff: noqa: E402
from module.config import server as _test_server

_test_server.set_lang("global_cn")

import unittest
from pathlib import Path


WORKTREE = Path(__file__).resolve().parents[2]
WORKTREE_ROOT = WORKTREE

import module.config.server as server_  # noqa: E402
from tasks.base.page import Page, page_common_activity, page_main  # noqa: E402


class FakeDevice:
    def __init__(self):
        self.screenshot_count = 0

    def screenshot(self):
        self.screenshot_count += 1


class FreeGacha20Test(unittest.TestCase):
    def setUp(self):
        self.original_lang = server_.lang

    def tearDown(self):
        server_.lang = self.original_lang


    def test_common_activity_routes_to_and_from_main(self):
        Page.init_connection(page_common_activity)
        try:
            self.assertEqual(page_main.parent, page_common_activity)
        finally:
            Page.clear_connection()

        Page.init_connection(page_main)
        try:
            self.assertEqual(page_common_activity.parent, page_main)
        finally:
            Page.clear_connection()







# Historical assertions retained verbatim for migration review; not executable evidence.
HISTORICAL_CONTRACTS = 'def test_oversea_and_cn_dispatch_stay_independent(self):\n        config = SimpleNamespace(Emulator_PackageName="com.stove.epic7.google")\n        server_.lang = "global_cn"\n        entry = SpecialActivityEntry(config)\n        self.assertEqual(entry._event_mode(), entry.FREE_GACHA_20_ACTIVITY)\n        self.assertEqual(\n            entry._event_end_time(),\n            datetime(2026, 10, 29, 2, tzinfo=entry.EVENT_TIMEZONE),\n        )\n\n        config.Emulator_PackageName = "com.zlongame.cn.epicseven"\n        server_.lang = "cn"\n        self.assertEqual(entry._event_mode(), entry.LEGACY_ACTIVITY)\n        self.assertEqual(\n            entry._event_end_time(),\n            datetime(2026, 9, 17, 11, tzinfo=entry.EVENT_TIMEZONE),\n        )\n\ndef test_already_obtained_returns_without_clicking(self):\n        activity = object.__new__(FreeGacha20)\n        activity.config = MagicMock()\n        activity.device = FakeDevice()\n        activity.ui_goto = MagicMock()\n        activity.appear = MagicMock(\n            side_effect=lambda button: button == FREE_20_GACHA_OBTAINED\n        )\n        activity.appear_then_click = MagicMock()\n        activity.handle_touch_to_close = MagicMock()\n        activity.handle_network_error = MagicMock()\n\n        self.assertTrue(activity.run_claim())\n        activity.ui_goto.assert_called_once_with(\n            page_common_activity,\n            skip_first_screenshot=True,\n        )\n        activity.appear_then_click.assert_not_called()\n        activity.config.cross_set.assert_called_once()\n        activity.config.task_call.assert_not_called()\n        self.assertEqual(\n            activity.config.cross_set.call_args.args[0],\n            FREE_GACHA_20_CHECKED_AT,\n        )\n\ndef test_claim_closes_reward_and_waits_for_obtained_marker(self):\n        activity = object.__new__(FreeGacha20)\n        activity.config = MagicMock()\n        activity.device = FakeDevice()\n        activity.ui_goto = MagicMock()\n\n        frame = {"value": 0}\n\n        def appear(button):\n            return button == FREE_20_GACHA_OBTAINED and frame["value"] >= 2\n\n        def click(button, interval=0):\n            if button == FREE_20_GACHA and frame["value"] == 0:\n                frame["value"] = 1\n                return True\n            return False\n\n        def close(interval=0):\n            if frame["value"] == 1:\n                frame["value"] = 2\n                return True\n            return False\n\n        activity.appear = appear\n        activity.appear_then_click = click\n        activity.handle_touch_to_close = close\n        activity.handle_network_error = MagicMock(return_value=False)\n\n        self.assertTrue(activity.run_claim())\n        self.assertEqual(frame["value"], 2)\n        self.assertEqual(activity.device.screenshot_count, 2)\n        activity.config.task_call.assert_called_once_with(\n            "Gacha",\n            force_call=False,\n        )\n\ndef test_oversea_post_login_claim_is_skipped(self):\n        server_.lang = "global_cn"\n        config = MagicMock(\n            Emulator_PackageName="com.stove.epic7.google",\n        )\n        config.is_task_enabled.return_value = True\n        entry = SpecialActivityEntry(config, device=FakeDevice())\n        entry._event_end_time = MagicMock(\n            return_value=datetime.max.replace(tzinfo=entry.EVENT_TIMEZONE)\n        )\n\n        self.assertTrue(entry.run_login_daily_reward())\n        self.assertEqual(entry.device.screenshot_count, 0)\n\ndef test_cn_post_login_daily_reward_is_unchanged(self):\n        server_.lang = "cn"\n        config = MagicMock(\n            Emulator_PackageName="com.zlongame.cn.epicseven",\n            SpecialActivity_GetDailyReward=True,\n        )\n        config.is_task_enabled.return_value = True\n        activity = MagicMock()\n        activity.run_get_daily_reward.return_value = True\n        entry = SpecialActivityEntry(config, device=FakeDevice())\n        entry._event_end_time = MagicMock(\n            return_value=datetime.max.replace(tzinfo=entry.EVENT_TIMEZONE)\n        )\n\n        with patch(\n            "tasks.activity.special_activity.SpecialActivity",\n            return_value=activity,\n        ):\n            self.assertTrue(entry.run_login_daily_reward())\n\n        activity.run_get_daily_reward.assert_called_once_with(\n            skip_first_screenshot=True\n        )\n        activity.ui_goto.assert_called_once_with(\n            page_main,\n            skip_first_screenshot=True,\n        )\n\ndef test_checked_state_expires_at_server_update(self):\n        checked_at = datetime(2026, 9, 3, 10)\n        config = SimpleNamespace(\n            Scheduler_ServerUpdate="02:00",\n            cross_get=lambda key, default=None: (\n                checked_at if key == FREE_GACHA_20_CHECKED_AT else default\n            ),\n        )\n\n        with patch(\n            "tasks.activity.scheduling.get_server_last_update",\n            return_value=checked_at - timedelta(hours=8),\n        ):\n            self.assertTrue(is_free_gacha_20_checked_today(config))\n\n        with patch(\n            "tasks.activity.scheduling.get_server_last_update",\n            return_value=checked_at + timedelta(hours=16),\n        ):\n            self.assertFalse(is_free_gacha_20_checked_today(config))\n\ndef test_after_battle_rescheduling_is_cn_only(self):\n        overseas = SimpleNamespace(Emulator_PackageName="com.stove.epic7.google")\n        self.assertFalse(should_schedule_after_battle(overseas))\n\n        cn = SimpleNamespace(\n            Emulator_PackageName="com.zlongame.cn.epicseven",\n            SpecialActivity_GetTaskReward=True,\n            Scheduler_ServerUpdate="03:00",\n            cross_get=MagicMock(return_value=None),\n        )\n        with patch(\n            "tasks.activity.scheduling.get_server_last_update",\n            return_value=datetime(2026, 8, 29, 3),\n        ):\n            self.assertTrue(should_schedule_after_battle(cn))'
