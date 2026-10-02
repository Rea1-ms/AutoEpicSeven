# ruff: noqa: E402
from module.config import server as _test_server

_test_server.set_lang("global_cn")

"""Offline regression checks for the September CN event and repeat-combat update."""
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
from tasks.activity import calendar, scheduling
from tasks.activity.limited_activity import LimitedActivityEntry
from tasks.activity.entry import SpecialActivityEntry
from tasks.activity.free_gacha_20 import FreeGacha20


class Config:
    SpecialActivity_BuyHucheMysticMedals = False
    Emulator_PackageName = "com.zlongame.cn.epicseven"
    Emulator_GameLanguage = "auto"
    Scheduler_ServerUpdate = "03:00"
    LimitedActivity_GetFreeGacha = True
    SpecialActivity_GetTaskReward = True
    SpecialActivity_GetDailyReward = True

    def __init__(self):
        self.values = {}
        self.delays = []
        self.calls = []

    def cross_get(self, path, default=None):
        return self.values.get(path, default)

    def cross_set(self, path, value):
        self.values[path] = value

    def task_delay(self, **kwargs):
        self.delays.append(kwargs)

    def task_call(self, task, **kwargs):
        self.calls.append((task, kwargs))

    def is_task_enabled(self, task):
        return True


class Claim(FreeGacha20):
    def __init__(self, frames):
        self.config = Config()
        self.activity_id = "test_campaign"
        self.frames = frames
        self.frame = -1
        self.clicks = 0
        self.popups = 0
        self.device = SimpleNamespace(screenshot=self.screenshot)
        self.device.screenshot()

    def screenshot(self):
        self.frame += 1
        if self.frame >= len(self.frames):
            raise AssertionError("Claim did not terminate on the supplied frames")

    def select_activity(self, *args, **kwargs):
        return True

    def ui_goto(self, *args, **kwargs):
        pass

    def appear(self, button, **kwargs):
        return button.name == "FREE_20_GACHA_OBTAINED" and self.frames[self.frame] == "obtained"

    def appear_then_click(self, button, **kwargs):
        if button.name == "FREE_20_GACHA" and self.frames[self.frame] == "claim":
            self.clicks += 1
            return True
        return False

    def handle_touch_to_close(self, **kwargs):
        if self.frames[self.frame] == "popup":
            self.popups += 1
            return True
        return False

    def handle_network_error(self):
        return False


class CalendarTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("cn")
        self.config = Config()
        clock = patch.object(calendar, "datetime", wraps=datetime)
        self.addCleanup(clock.stop)
        clock.start().now.return_value = datetime(2026, 9, 17, 12, tzinfo=calendar.EVENT_TIMEZONE)

    def make_calendar(self, **changes):
        event = dict(
            id="first", name="Reward", kind="free_gacha_20", source="Offline fixture",
            oversea_start="2026-08-27T02:00:00+08:00",
            oversea_end="2026-10-29T02:00:00+08:00",
        )
        event.update(changes)
        return dict(schema_version=1, defaults={}, cn_delay_days={"free_gacha_20": 21}, events=[event])

    def load(self, data):
        with patch.object(Path, "read_text", return_value=json.dumps(data)):
            return calendar.load_calendar()

    def test_default_offset_and_override(self):
        first, cn = self.load(self.make_calendar())
        self.assertEqual(cn.start - first.start, timedelta(days=21))
        self.assertEqual(cn.end - first.end, timedelta(days=21))
        _, cn = self.load(self.make_calendar(cn_end="2026-11-19T11:00:00+08:00"))
        self.assertEqual(cn.end.hour, 11)

    def test_current_cn_deadline(self):
        cn = next(w for w in calendar.load_calendar() if w.server_family == "CN")
        self.assertEqual(cn.end.isoformat(), "2026-11-19T11:00:00+08:00")
        self.assertTrue(cn.contains(cn.start))
        self.assertFalse(cn.contains(cn.start - timedelta(microseconds=1)))
        self.assertFalse(cn.contains(cn.end))

    def test_adjacent_campaign_switch(self):
        data = self.make_calendar()
        data["events"].append(dict(data["events"][0], id="second",
            oversea_start=data["events"][0]["oversea_end"],
            oversea_end="2026-12-01T02:00:00+08:00"))
        windows = self.load(data)
        boundary = windows[1].end
        with patch.object(calendar, "load_calendar", return_value=windows):
            self.assertEqual([w.event_id for w in calendar.active_activities(self.config, boundary)], ["second"])
            self.assertEqual([w.event_id for w in calendar.active_activities(
                self.config, boundary - timedelta(microseconds=1))], ["first"])

    def test_overlapping_same_flow_rejected(self):
        data = self.make_calendar()
        data["events"].append(dict(data["events"][0], id="second"))
        with self.assertRaisesRegex(ValueError, "Overlapping"):
            self.load(data)

    def test_invalid_windows_rejected(self):
        for update in (
            dict(oversea_end="2026-08-26T02:00:00+08:00"),
            dict(oversea_start="2026-08-27T02:00:00"),
            dict(mode="unimplemented"),
        ):
            with self.subTest(update=update), self.assertRaises(ValueError):
                self.load(self.make_calendar(**update))

    def test_unsupported_language_skips(self):
        server.set_lang("global_en")
        self.config.Emulator_PackageName = "com.stove.epic7.google"
        self.assertEqual(calendar.active_activities(self.config), ())

    def test_no_battle_or_login_legacy_claim(self):
        self.assertFalse(scheduling.should_schedule_after_battle(self.config))
        self.assertTrue(SpecialActivityEntry(self.config).run_login_daily_reward())
        self.assertEqual(self.config.calls, [])

    def test_dispatch_cn_free_reward(self):
        with patch("tasks.activity.entry.CommonActivityBatch") as task:
            task.ACTIVITIES = {'free_gacha_20': None}
            task.return_value.run.return_value = True
            self.assertTrue(LimitedActivityEntry(self.config).run())
            self.assertEqual(task.return_value.run.call_args.args[0][0].event_id, calendar.DEFAULT_FREE_GACHA_20_ID)

    def test_daily_record_campaign_and_server_isolation(self):
        scheduling.mark_free_gacha_20_checked(self.config, "first")
        self.assertTrue(scheduling.is_free_gacha_20_checked_today(self.config, "first"))
        self.assertFalse(scheduling.is_free_gacha_20_checked_today(self.config, "second"))
        self.config.Emulator_PackageName = "com.stove.epic7.google"
        self.assertFalse(scheduling.is_free_gacha_20_checked_today(self.config, "first"))

    def test_expired_daily_record(self):
        self.config.values[scheduling._checked_path(self.config, "first")] = datetime.now() - timedelta(days=2)
        self.assertFalse(scheduling.is_free_gacha_20_checked_today(self.config, "first"))

    def test_legacy_timestamp_migrates_only_original_campaign(self):
        self.config.values[scheduling.FREE_GACHA_20_CHECKED_AT] = datetime.now()
        self.assertTrue(scheduling.is_free_gacha_20_checked_today(self.config))
        self.assertFalse(scheduling.is_free_gacha_20_checked_today(self.config, "second"))

    def test_checked_campaign_is_not_dispatched_again(self):
        scheduling.mark_free_gacha_20_checked(self.config)
        with patch("tasks.activity.entry.CommonActivityBatch") as task:
            self.assertTrue(LimitedActivityEntry(self.config).run())
            task.assert_not_called()

    def test_claim_retries_and_confirms_obtained(self):
        claim = Claim(["claim", "claim", "popup", "obtained"])
        self.assertTrue(claim.run_claim())
        self.assertEqual(claim.clicks, 2)
        self.assertEqual(claim.popups, 1)
        self.assertEqual(len(claim.config.calls), 1)
        self.assertTrue(scheduling.is_free_gacha_20_checked_today(claim.config, "test_campaign"))

    def test_already_obtained_does_not_call_gacha(self):
        claim = Claim(["obtained"])
        self.assertTrue(claim.run_claim())
        self.assertEqual(claim.config.calls, [])
        self.assertEqual(claim.clicks, 0)

    def test_missing_target_page_does_not_mark_success(self):
        claim = Claim(["other"])
        with patch("tasks.activity.free_gacha_20.Timer") as timer:
            timer.return_value.start.return_value.reached.return_value = True
            self.assertFalse(claim.run_claim())
        self.assertEqual(claim.config.values, {})
        self.assertEqual(claim.clicks, 0)

    def test_future_campaign_wakes_at_launch_time(self):
        launch = datetime(2026, 9, 18, 11, tzinfo=calendar.EVENT_TIMEZONE)
        with patch.object(scheduling, "next_activity_start", return_value=launch):
            scheduling.delay_next_activity_check(self.config, task="LimitedActivity")
        self.assertEqual(self.config.delays, [dict(server_update=True, task="LimitedActivity",
            target=launch.astimezone().replace(tzinfo=None))])

    def test_future_start_uses_cn_delay(self):
        windows = self.load(self.make_calendar())
        before = windows[1].start - timedelta(seconds=1)
        with patch.object(calendar, "load_calendar", return_value=windows):
            self.assertEqual(calendar.next_activity_start(self.config, before), windows[1].start)
            self.assertIsNone(calendar.next_activity_start(self.config, windows[1].start))

    def test_no_active_campaign_still_schedules_future_start(self):
        with patch("tasks.activity.entry.active_activities", return_value=()), patch(
            "tasks.activity.entry.delay_next_activity_check"
        ) as delay:
            self.assertTrue(LimitedActivityEntry(self.config).run())
        delay.assert_called_once_with(self.config, task="LimitedActivity")

    def test_render_uses_actual_schedule(self):
        timeline = calendar.render_timeline()
        self.assertIn("2026-11-19 11:00", timeline)
        self.assertIn("2026-10-29 02:00", timeline)


class NavigationTests(unittest.TestCase):
    def navigate(self, frames, up_limit=4, down_limit=8):
        from tasks.activity.navigation import ActivityNavigationMixin
        from tasks.activity.assets.assets_activity_special_26_8_27 import FREE_20_GACHA_SELECTED

        class Navigation(ActivityNavigationMixin):
            def __init__(self):
                self.config = Config()
                self.frame = -1
                self.clicks = []
                self.swipes = []
                self.device = SimpleNamespace(screenshot=self.screenshot, click=self.click,
                    swipe=self.swipe, image=None)
                self.ACTIVITY_SCROLL_UP_LIMIT = up_limit
                self.ACTIVITY_SCROLL_DOWN_LIMIT = down_limit
                self.screenshot()

            def screenshot(self):
                self.frame += 1
                if self.frame >= len(frames):
                    raise AssertionError("Navigation failed to terminate")
                self.device.image = self.frame

            def click(self, button):
                self.clicks.append((self.frame, button.text, button.button))

            def swipe(self, start, end, **kwargs):
                self.swipes.append((start, end))

            def match_template_color(self, button):
                return frames[self.frame].get("selected", False)

            def ui_page_appear(self, page):
                return True

            def interval_is_reached(self, *args, **kwargs):
                return True

            def interval_reset(self, *args, **kwargs):
                pass

            def handle_network_error(self):
                return False

        def ocr_results(index):
            frame = frames[index]
            text = frame.get("text", "INFINITY00")
            y = frame.get("y", 200)
            return [SimpleNamespace(ocr_text=text, box=(20, y, 140, y + 20), score=1)]

        with patch("tasks.activity.navigation.Ocr") as ocr:
            ocr.return_value.detect_and_ocr.side_effect = ocr_results
            nav = Navigation()
            success = nav.select_activity("INFINITY", FREE_20_GACHA_SELECTED)
            self.assertEqual(ocr.call_args.kwargs["lang"], "cn")
        return nav, success

    def test_sidebar_click_waits_for_selected_marker(self):
        nav, success = self.navigate([{}, {}, {"selected": True}])
        self.assertTrue(success)
        self.assertEqual([frame for frame, _, _ in nav.clicks], [1])

    def test_moving_text_is_not_clicked(self):
        nav, success = self.navigate([{"y": 200}, {"y": 220}, {"y": 220}, {"selected": True}])
        self.assertTrue(success)
        self.assertEqual([frame for frame, _, _ in nav.clicks], [2])

    def test_failed_click_is_retried(self):
        nav, success = self.navigate([{}, {}, {}, {}, {"selected": True}])
        self.assertTrue(success)
        self.assertEqual([frame for frame, _, _ in nav.clicks], [1, 3])

    def test_already_selected_does_not_click(self):
        nav, success = self.navigate([{"selected": True}])
        self.assertTrue(success)
        self.assertEqual(nav.clicks, [])

    def test_missing_row_scrolls_before_click(self):
        nav, success = self.navigate([{"text": "Other"}, {"text": "Other"}, {}, {}, {"selected": True}])
        self.assertTrue(success)
        self.assertEqual(len(nav.swipes), 1)
        self.assertEqual([frame for frame, _, _ in nav.clicks], [3])

    def test_missing_event_has_bounded_bidirectional_search(self):
        nav, success = self.navigate([{"text": "Other"}] * 6, up_limit=1, down_limit=1)
        self.assertFalse(success)
        self.assertEqual(nav.clicks, [])
        self.assertEqual(len(nav.swipes), 2)
        self.assertGreater(nav.swipes[0][0][1], nav.swipes[0][1][1])
        self.assertLess(nav.swipes[1][0][1], nav.swipes[1][1][1])

    def test_navigation_failure_does_not_claim(self):
        claim = Claim(["claim"])
        claim.select_activity = lambda *args: False
        self.assertFalse(claim.run_claim())
        self.assertEqual(claim.clicks, 0)
        self.assertEqual(claim.config.values, {})

    def test_real_sidebar_ocr_finds_event(self):
        from module.base.utils import load_image
        from module.ocr.ocr import Ocr
        from tasks.activity.assets.assets_activity_common import COMMON_ACTIVITY_LIST
        from tasks.activity.navigation import ActivityNavigationMixin
        image = load_image(COMMON_ACTIVITY_LIST.buttons[0].file)
        results = Ocr(COMMON_ACTIVITY_LIST, lang="cn").detect_and_ocr(image)
        matches = [row for row in results
                   if "INFINITY" in ActivityNavigationMixin._activity_text(row.ocr_text)]
        self.assertEqual(len(matches), 1)
        self.assertLess(matches[0].box[2], COMMON_ACTIVITY_LIST.area[2])


class RepeatTests(unittest.TestCase):
    def test_import_routes_assets_and_mode_per_language(self):
        code = """
import sys
from pathlib import Path
from types import SimpleNamespace
import module.config.server as server
server.set_lang(sys.argv[1])
server.server = "CN-Official" if sys.argv[1] == "cn" else "OVERSEA-Play"
from tasks.dungeon.dungeon import Combat
from tasks.dungeon import repeat
from module.base.button import ButtonWrapper
from tasks.base.page import Page, page_main, page_combat_prepare, page_common_activity
from tasks.activity.limited_activity import LimitedActivityEntry
combat = object.__new__(Combat)
combat.config = SimpleNamespace(Emulator_PackageName=server.to_package(server.server))
assert combat._uses_server_repeat_combat() == (server.lang != "global_en")
assert page_combat_prepare.check_button is repeat.REPEAT_COMBAT_MENU
Page.init_connection(page_main)
assert page_combat_prepare.parent is not None
assert page_common_activity.parent is page_main
Page.init_connection(page_common_activity)
assert page_main.parent is page_common_activity
list(Page.iter_check_buttons())
if server.lang in ("cn", "global_cn"):
    assert combat._combat_runtime_build()["mode"] == "repeat_server"
    assert combat._combat_runtime_build_detected_existing()["mode"] == "repeat_server"
    for asset in vars(repeat).values():
        if isinstance(asset, ButtonWrapper):
            for button in asset.buttons:
                assert Path(button.file).is_file(), button.file
    from module.base.utils import load_image
    for asset in (repeat.REPEAT_COMBAT_MENU, repeat.REPEAT_COMBAT_MENU_CHECK,
                  repeat.REPEAT_START, repeat.SETTLEMENT_CLOSE):
        image = load_image(asset.buttons[0].file)
        assert asset.match_template_luma(image, similarity=0.8), asset.name
else:
    config = SimpleNamespace(Emulator_PackageName=server.to_package(server.server),
                            task_delay=lambda **kwargs: None)
    assert LimitedActivityEntry(config).run()
    combat.config = config
    assert combat.run()
print("verified", server.lang)
"""
        for lang in ("cn", "global_cn", "global_en"):
            with self.subTest(lang=lang):
                result = subprocess.run([sys.executable, "-B", "-c", code, lang],
                    cwd=WORKTREE, text=True, encoding="utf-8", errors="replace",
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                self.assertEqual(result.returncode, 0, result.stdout)
