# ruff: noqa: E402
from module.config import server as _test_server

_test_server.set_lang("global_cn")

"""Offline screenshots, single-click claiming, and overseas calendar regressions."""
import json
import subprocess
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

WORKTREE = Path(__file__).resolve().parents[2]
PROJECT_ROOT = WORKTREE

import module.config.server as server
from module.game_info.catalog import load_info
from tasks.activity import calendar, scheduling
from tasks.activity.legacy.e7wc_battle_gate_2026_09_12.e7wc_battle_gate import (
    E7wcBattleGate, E7WC_BATTLE_GATE_CHECK, E7WC_LEFT_REWARD_AVAILABLE, E7WC_RIGHT_REWARD_AVAILABLE,
)
from tasks.activity.limited_activity import LimitedActivityEntry

SCREENSHOTS = Path(__file__).parent / "screenshots/e7wc_battle_gate"
KOHARU = Path("C:/Users/rea1m/Documents/MuMu共享文件夹/Screenshots/MuMu-20260917-173824-447.png")
EVENT_ID = "e7wc_battle_gate_2026_09_10"
NOW = datetime.fromisoformat("2026-09-18T12:00:00+08:00")


class Config:
    SpecialActivity_BuyHucheMysticMedals = False
    Emulator_PackageName = "com.stove.epic7.google"
    Emulator_GameLanguage = "auto"
    Scheduler_ServerUpdate = "02:00"
    LimitedActivity_GetE7wcBattleGateReward = True

    def __init__(self):
        self.values = {}
        self.delays = []

    def cross_get(self, path, default=None):
        return self.values.get(path, default)

    def cross_set(self, path, value):
        self.values[path] = value

    def task_delay(self, **kwargs):
        self.delays.append(kwargs)


class Claim(E7wcBattleGate):
    def __init__(self, frames):
        self.config = Config()
        self.activity_id = EVENT_ID
        self.frames = frames
        self.frame = -1
        self.clicks = []
        self.popups = 0
        self.routes = []
        self.device = SimpleNamespace(screenshot=self.screenshot, click=self.click,
                                      app_is_running=lambda: True)
        self.device.screenshot()

    def screenshot(self):
        self.frame += 1
        if self.frame >= len(self.frames):
            raise AssertionError("Flow failed to terminate on supplied frames")

    def click(self, button):
        self.clicks.append(button.name)

    def select_activity(self, keyword, button):
        return True

    def ui_goto(self, page, **kwargs):
        self.routes.append(page)

    def match_template_color(self, button, **kwargs):
        frame = self.frames[self.frame]
        if button is E7WC_BATTLE_GATE_CHECK:
            return frame in ("available", "received", "left", "right")
        if button is E7WC_LEFT_REWARD_AVAILABLE:
            return frame in ("available", "right")
        raise AssertionError("Unexpected template")

    def reward_received(self, reward):
        frame = self.frames[self.frame]
        return frame == "received" or (
            frame == "left" and reward is E7WC_LEFT_REWARD_AVAILABLE
        ) or (frame == "right" and reward is E7WC_RIGHT_REWARD_AVAILABLE)

    def handle_touch_to_close(self, **kwargs):
        if self.frames[self.frame] == "popup":
            self.popups += 1
            return True
        return False

    def handle_network_error(self):
        return False


class ClaimTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")

    def test_already_received_skips_click_and_popup(self):
        claim = Claim(["received"])
        self.assertTrue(claim.run_claim())
        self.assertEqual(claim.clicks, [])
        self.assertEqual(claim.popups, 0)
        self.assertEqual(len(claim.config.values), 1)

    def test_one_click_claims_both_and_closes_popup(self):
        claim = Claim(["available", "popup", "received"])
        self.assertTrue(claim.run_claim())
        self.assertEqual(claim.clicks, ["E7WC_LEFT_REWARD_AVAILABLE"])
        self.assertEqual(claim.popups, 1)
        self.assertEqual(len(claim.config.values), 1)

    def test_receipt_before_popup_does_not_exit_early(self):
        claim = Claim(["available", "received", "popup", "left", "received"])
        self.assertTrue(claim.run_claim())
        self.assertEqual(claim.frame, 4)
        self.assertEqual(len(claim.clicks), 1)

    def test_missed_click_retries_same_icon(self):
        claim = Claim(["available", "available", "popup", "received"])
        self.assertTrue(claim.run_claim())
        self.assertEqual(claim.clicks, ["E7WC_LEFT_REWARD_AVAILABLE"] * 2)
        self.assertEqual(claim.popups, 1)

    def test_incomplete_receipt_and_popup_only_timeout_without_record(self):
        for frames in (["left", "left"], ["available", "popup", "other"],
                       ["other"], ["available", "received"],
                       ["available", "available", "available"]):
            with self.subTest(frames=frames), patch("tasks.activity.legacy.e7wc_battle_gate_2026_09_12.e7wc_battle_gate.Timer") as timer:
                timer.return_value.start.return_value.reached.side_effect = [False] * (len(frames) - 1) + [True]
                claim = Claim(frames)
                self.assertFalse(claim.run_claim())
                self.assertEqual(claim.config.values, {})
                timer.return_value.start.return_value.reset.assert_not_called()

    def test_failed_sidebar_navigation_never_claims(self):
        claim = Claim(["available"])
        claim.select_activity = lambda *args: False
        self.assertFalse(claim.run_claim())
        self.assertEqual(claim.clicks, [])
        self.assertEqual(claim.config.values, {})

    def test_disabled_and_unsupported_skip_without_assets_or_device(self):
        for lang, package, enabled in (
            ("global_cn", "com.stove.epic7.google", False),
            ("cn", "com.zlongame.cn.epicseven", True),
            ("global_en", "com.stove.epic7.google", True),
        ):
            with self.subTest(lang=lang, enabled=enabled):
                server.set_lang(lang)
                claim = Claim(["available"])
                claim.config.Emulator_PackageName = package
                claim.config.LimitedActivity_GetE7wcBattleGateReward = enabled
                claim.device = None
                self.assertTrue(claim.run())
                self.assertEqual(claim.config.delays, [{"server_update": True}])

    def test_success_stays_on_activity_failure_requests_retry(self):
        from tasks.base.page import page_common_activity
        claim = Claim(["received"])
        self.assertTrue(claim.run())
        self.assertEqual(claim.routes, [page_common_activity])
        self.assertEqual(claim.config.delays, [{"server_update": True}])
        claim = Claim(["other"])
        with patch.object(claim, "run_claim", return_value=False):
            self.assertFalse(claim.run())
        self.assertEqual(claim.config.delays, [{"success": False}])




class CalendarTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")
        self.config = Config()

    def test_explicit_dates_and_overseas_only(self):
        info = load_info()
        gate = info.current("e7wc_battle_gate", "OVERSEA", NOW)
        self.assertEqual(gate.start.isoformat(), "2026-09-10T11:00:00+08:00")
        self.assertEqual(gate.end.isoformat(), "2026-09-27T11:00:00+08:00")
        koharu = info.current("koharu_raffle", "OVERSEA", NOW)
        self.assertEqual(koharu.start.isoformat(), "2026-09-17T11:00:00+08:00")
        self.assertEqual(koharu.end.isoformat(), "2026-10-08T11:00:00+08:00")
        self.assertFalse(any(p.server_family == "CN" and p.kind in ("e7wc_battle_gate", "koharu_raffle") for p in info.periods))

    def test_countdowns_bound_end_time(self):
        for timestamp, days, hours, minutes, kind in (
            ("2026-09-12T11:04:05+08:00", 14, 23, 55, "e7wc_battle_gate"),
            ("2026-09-14T13:10:05+08:00", 12, 21, 49, "e7wc_battle_gate"),
            ("2026-09-17T17:38:24+08:00", 20, 17, 21, "koharu_raffle"),
        ):
            earliest = datetime.fromisoformat(timestamp) + timedelta(days=days, hours=hours, minutes=minutes)
            end = load_info().current(kind, "OVERSEA", NOW).end
            self.assertLessEqual(earliest, end)
            self.assertLess(end, earliest + timedelta(minutes=1))

    def test_start_inclusive_end_exclusive_with_koharu(self):
        gate = load_info().current("e7wc_battle_gate", "OVERSEA", NOW)
        ids = lambda at: {e.event_id for e in calendar.active_activities(self.config, at)}  # noqa: E731
        # Historical facts retain their original boundaries; automation no
        # longer includes this category even during the archived event window.
        self.assertFalse(gate.contains(gate.start - timedelta(seconds=1)))
        self.assertTrue(gate.contains(gate.start))
        self.assertFalse(gate.contains(gate.end))
        self.assertNotIn(EVENT_ID, ids(gate.start - timedelta(seconds=1)))
        self.assertNotIn(EVENT_ID, ids(gate.start))
        self.assertNotIn(EVENT_ID, ids(gate.end))
        self.assertEqual(ids(NOW), {calendar.DEFAULT_FREE_GACHA_20_ID, "koharu_raffle_2026_09_17", "huche_shop_2026_09_17"})
        server.set_lang("cn")
        self.config.Emulator_PackageName = "com.zlongame.cn.epicseven"
        self.assertEqual(ids(NOW), {calendar.DEFAULT_FREE_GACHA_20_ID})
        self.assertEqual(ids(NOW + timedelta(days=21)), {calendar.DEFAULT_FREE_GACHA_20_ID})

    def test_future_cn_dates_do_not_bypass_missing_assets(self):
        cn = calendar.ActivityWindow(EVENT_ID, "Gate", "e7wc_battle_gate", "CN", NOW, NOW + timedelta(days=1))
        self.config.Emulator_PackageName = "com.zlongame.cn.epicseven"
        server.set_lang("cn")
        with patch.object(calendar, "load_calendar", return_value=(cn,)):
            self.assertEqual(calendar.active_activities(self.config, NOW), ())
            self.assertIsNone(calendar.next_activity_start(self.config, NOW - timedelta(days=1)))

    def test_records_are_isolated_from_free_summons_and_other_servers(self):
        self.config.values[scheduling.FREE_GACHA_20_CHECKED_AT] = datetime.now()
        self.assertFalse(scheduling.is_activity_checked_today(self.config, EVENT_ID))
        scheduling.mark_activity_checked(self.config, EVENT_ID)
        self.assertTrue(scheduling.is_activity_checked_today(self.config, EVENT_ID))
        self.config.Emulator_PackageName = "com.zlongame.cn.epicseven"
        self.assertFalse(scheduling.is_activity_checked_today(self.config, EVENT_ID))

    def test_dispatch_and_daily_skip(self):
        gate = calendar.ActivityWindow(EVENT_ID, "Gate", "e7wc_battle_gate", "OVERSEA", NOW,
                                       NOW + timedelta(days=1))
        activities = (*calendar.active_activities(self.config, NOW), gate)
        scheduling.mark_activity_checked(self.config, "koharu_raffle_2026_09_17")
        scheduling.mark_free_gacha_20_checked(self.config)
        with patch("tasks.activity.entry.active_activities", return_value=activities), patch(
            "tasks.activity.entry.CommonActivityBatch"
        ) as task, patch("tasks.activity.free_gacha_20.FreeGacha20") as free:
            task.ACTIVITIES = {'e7wc_battle_gate': None}
            task.return_value.run.return_value = True
            self.assertTrue(LimitedActivityEntry(self.config).run())
            task.assert_not_called()
            free.assert_not_called()
            scheduling.mark_activity_checked(self.config, EVENT_ID)
            task.reset_mock()
            self.assertTrue(LimitedActivityEntry(self.config).run())
            task.assert_not_called()

    def test_config_upgrade_preserves_disabled_switch_and_translations_exist(self):
        from module.config.config_updater import ConfigUpdater
        records = {EVENT_ID: {"OVERSEA": "2026-09-26 12:00:00"}}
        for task, enabled in (("SpecialActivity", False), ("SpecialActivity", True),
                              ("LimitedActivity", False), ("LimitedActivity", True)):
            old = {"SpecialActivity": {"ActivityRuntime": {"CheckedEvents": records}}}
            old.setdefault(task, {})[task] = {"GetE7wcBattleGateReward": enabled}
            config = ConfigUpdater().config_update(old)
            self.assertNotIn("GetE7wcBattleGateReward", config["LimitedActivity"]["LimitedActivity"])
            self.assertNotIn("GetE7wcBattleGateReward", config["SpecialActivity"]["SpecialActivity"])
            self.assertEqual(config["SpecialActivity"]["ActivityRuntime"]["CheckedEvents"], records)
            self.assertEqual(ConfigUpdater().config_update(config), config)
        archive = WORKTREE / "tasks/activity/legacy/e7wc_battle_gate_2026_09_12/i18n.json"
        original = json.loads(archive.read_text(encoding="utf-8"))
        for path in Path("module/config/i18n").glob("*.json"):
            self.assertNotIn("GetE7wcBattleGateReward",
                             json.loads(path.read_text(encoding="utf-8"))["LimitedActivity"])
            texts = original[path.stem]
            self.assertTrue(texts["name"])
            self.assertTrue(texts["help"])
            self.assertNotIn("SpecialActivity.", texts["name"])

    def test_future_dates_do_not_reactivate_archived_flow(self):
        from dataclasses import replace

        info = load_info()
        start = NOW + timedelta(days=30)
        period = replace(info.current("e7wc_battle_gate", "OVERSEA", NOW),
                         start=start, end=start + timedelta(days=7))
        with patch.object(calendar, "load_info", return_value=SimpleNamespace(periods=(period,))):
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
assert 'e7wc_battle_gate' not in SUPPORTED_MODES
assert 'e7wc_battle_gate' not in LimitedActivityEntry.ACTIVITY_MODES
assert 'e7wc_battle_gate' not in LimitedActivityEntry.COMMON_ACTIVITY_OPTIONS
assert 'e7wc_battle_gate' not in CommonActivityBatch.ACTIVITIES
assert not any('e7wc_battle_gate' in name for name in sys.modules)
assert 'tasks.activity.assets.assets_activity_special_26_9_12' not in sys.modules
"""
        for lang in ("cn", "global_cn", "global_en"):
            with self.subTest(lang=lang):
                result = subprocess.run([sys.executable, "-X", "utf8", "-B", "-c", code, lang], cwd=WORKTREE,
                                        capture_output=True, text=True, encoding="utf-8", timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
