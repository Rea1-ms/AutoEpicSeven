"""October events: real captures and bounded, isolated interaction replays."""

import subprocess
import sys
import unittest
from contextlib import ExitStack
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from module.config import server
from tests.support.offline import ROOT, ControlledClock, fixture_image

server.set_lang("global_cn")

from module.ocr.ocr import Ocr
from tasks.activity import calendar, scheduling
from tasks.activity.assets.assets_activity_common import COMMON_ACTIVITY_CHECK
from tasks.activity.assets.assets_activity_completion_record import (
    COMPLETION_RECORD_CHECK,
)
from tasks.activity.assets.assets_activity_detective_search import (
    DETECTIVE_SEARCH_CHECK,
    OCR_DETECTIVE_POINTS,
)
from tasks.activity.assets.assets_activity_epic_path import (
    EPIC_PATH_CHECK,
    EPIC_PATH_DAILY_TASK_CHECK,
    EPIC_PATH_REWARD_POPUP_CHECK,
    EPIC_PATH_TASK_LIST,
    EPIC_PATH_TASK_RECEIVED,
)
from tasks.activity.common_activity import CommonActivityBatch
from tasks.activity.completion_record import CompletionRecord
from tasks.activity.detective_search import DetectiveSearch
from tasks.activity.epic_path import EpicPath
from tests.support.october_events import (
    ATTENDANCE,
    AVAILABLE,
    DAILY,
    EPIC_DAILY_AFTER,
    EPIC_DAILY_READY,
    EPIC_HOME_CLAIMED,
    EPIC_HOME_READY,
    EPIC_REWARD,
    HOME,
    HOME_AFTER,
    POPUP,
    RECEIVED,
    OctoberReplay,
    capture,
)

MANIFEST = ROOT / "tests/fixtures/historical/manifest.json"


class OctoberPageTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")

    def test_activity_title_identifies_epic_path_without_clock(self):
        image = fixture_image(MANIFEST, "20261029-203759-503")
        self.assertTrue(COMMON_ACTIVITY_CHECK.match_template_color(image))

    def test_title_rejects_both_dialog_overlays(self):
        for name in (HOME, AVAILABLE, RECEIVED, HOME_AFTER, ATTENDANCE, POPUP, DAILY):
            with self.subTest(name=name):
                image = fixture_image(MANIFEST, f"20261029-{name}")
                self.assertEqual(COMMON_ACTIVITY_CHECK.match_template_color(image), name not in (POPUP, DAILY))

    def test_page_markers_are_mutually_exclusive_and_reject_overlays(self):
        for name in (HOME, AVAILABLE, RECEIVED, HOME_AFTER, ATTENDANCE, POPUP, DAILY):
            image = fixture_image(MANIFEST, f"20261029-{name}")
            for button, expected in ((EPIC_PATH_CHECK, name in (HOME, HOME_AFTER)),
                                     (DETECTIVE_SEARCH_CHECK, name in (AVAILABLE, RECEIVED)),
                                     (COMPLETION_RECORD_CHECK, name == ATTENDANCE)):
                with self.subTest(name=name, button=button.name):
                    self.assertEqual(button.match_template_color(image), expected)

    def test_real_ocr_reads_detective_points_and_four_daily_task_rows(self):
        image = fixture_image(MANIFEST, f"20261029-{AVAILABLE}")
        text = Ocr(OCR_DETECTIVE_POINTS, lang="cn").ocr_single_line(image)
        self.assertEqual(text.replace(" ", ""), "720/720")
        image = fixture_image(MANIFEST, f"20261029-{DAILY}")
        results = Ocr(EPIC_PATH_TASK_LIST, lang="cn").detect_and_ocr(image)
        worker = object.__new__(EpicPath)
        rows = worker._daily_rows(results)
        self.assertIsNotNone(rows, [(r.ocr_text, r.box) for r in results])
        self.assertEqual(set(rows), EpicPath.DAILY_TASKS)
        self.assertTrue(all(state == "incomplete" for state, _ in rows.values()))


class OctoberReplayTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.clock = self.stack.enter_context(ControlledClock())

    def replay(self, task, frames):
        return OctoberReplay(task, frames, self.clock)

    def test_attendance_records_only_after_verified_page_without_any_click(self):
        r = self.replay(CompletionRecord, [capture(ATTENDANCE)])
        self.assertTrue(r.task.run_claim(navigate=False))
        self.assertEqual(len(r.task.config.values), 1)
        self.assertFalse(r.actions)

    def test_wrong_attendance_page_never_records_or_claims(self):
        r = self.replay(CompletionRecord, [capture(HOME)])
        self.assertFalse(r.task.run_claim(navigate=False))
        self.assertFalse(r.task.config.values)
        self.assertFalse(r.actions)

    def test_fixed_page_markers_preserve_search_bounds_and_skip_sidebar_ocr(self):
        for task, name, marker in ((CompletionRecord, ATTENDANCE, COMPLETION_RECORD_CHECK),
                                   (DetectiveSearch, AVAILABLE, DETECTIVE_SEARCH_CHECK),
                                   (EpicPath, HOME, EPIC_PATH_CHECK)):
            r = self.replay(task, [capture(name)])
            before = [b.search for b in marker.iter_buttons()]
            with patch("tasks.activity.navigation.Ocr") as ocr:
                self.assertTrue(r.task.select_activity("unused", marker))
                ocr.return_value.detect_and_ocr.assert_not_called()
            self.assertEqual([b.search for b in marker.iter_buttons()], before)
            self.assertFalse(r.actions)

    def test_detective_real_four_tiers_and_partial_receipt(self):
        for name, partial, expected in ((AVAILABLE, False, ["pending"] * 4),
                                        (RECEIVED, False, ["received"] * 4),
                                        (AVAILABLE, True, ["received"] + ["pending"] * 3)):
            r = self.replay(DetectiveSearch, [capture(name, partial=partial)])
            self.assertEqual([r.task._reward_state(a) for a in DetectiveSearch.REWARD_AREAS], expected)

    def test_detective_claim_popup_then_all_receipts_records_completion(self):
        r = self.replay(DetectiveSearch, [capture(AVAILABLE), capture(POPUP), capture(RECEIVED)])
        self.assertTrue(r.task.run_claim(navigate=False))
        self.assertEqual(r.actions, [(0, "DETECTIVE_CLAIM_180"), (1, "TOUCH_TO_CLOSE")])
        self.assertTrue(r.task.config.values)

    def test_detective_receipts_before_popup_do_not_finish_pending_reward(self):
        r = self.replay(DetectiveSearch, [capture(AVAILABLE)] + [capture(RECEIVED)] * 4
                        + [capture(POPUP), capture(RECEIVED)])
        self.assertTrue(r.task.run_claim(navigate=False))
        self.assertEqual(r.clicks("TOUCH_TO_CLOSE"), [5])
        self.assertEqual(r.index, 6)

    def test_detective_dropped_tap_retries_only_original_tier(self):
        r = self.replay(DetectiveSearch, [capture(AVAILABLE)] * 7 + [capture(POPUP)] * 7 + [capture(RECEIVED)])
        self.assertTrue(r.task.run_claim(navigate=False))
        r.assert_retried(self, "DETECTIVE_CLAIM_180", 2)
        r.assert_retried(self, "TOUCH_TO_CLOSE", 2)
        self.assertFalse(r.clicks("DETECTIVE_CLAIM_360"))

    def test_detective_partial_claim_advances_to_next_tier_only_after_popup(self):
        r = self.replay(DetectiveSearch, [capture(AVAILABLE, partial=True), capture(POPUP), capture(RECEIVED)])
        self.assertTrue(r.task.run_claim(navigate=False))
        self.assertEqual(r.actions[0], (0, "DETECTIVE_CLAIM_360"))
        self.assertTrue(552 <= r.click_areas[0][2][0] < 730)

    def test_detective_zero_points_keeps_battle_recheck_without_clicking(self):
        r = self.replay(DetectiveSearch, [capture(AVAILABLE, locked=True)])
        with patch("tasks.activity.detective_search.Ocr") as ocr:
            ocr.return_value.ocr_single_line.return_value = "0/720"
            self.assertTrue(r.task.run_claim(navigate=False))
        self.assertFalse(r.actions)
        self.assertFalse(r.task.config.values)

    def test_detective_unknown_points_time_out_without_completion(self):
        r = self.replay(DetectiveSearch, [capture(AVAILABLE, locked=True, seconds=1)] * 48)
        with patch("tasks.activity.detective_search.Ocr") as ocr:
            ocr.return_value.ocr_single_line.return_value = "unreadable"
            self.assertFalse(r.task.run_claim(navigate=False))
        self.assertFalse(r.actions)
        self.assertFalse(r.task.config.values)

    def test_detective_missing_popup_cannot_extend_deadline(self):
        r = self.replay(DetectiveSearch, [capture(AVAILABLE)] + [capture(RECEIVED, seconds=1)] * 48)
        self.assertFalse(r.task.run_claim(navigate=False))
        self.assertFalse(r.task.config.values)

    def test_detective_wrong_tab_is_rejected(self):
        r = self.replay(DetectiveSearch, [capture(HOME)])
        self.assertFalse(r.task.run_claim(navigate=False))
        self.assertFalse(r.actions)

    def test_epic_real_unfinished_daily_closes_without_clicking_go(self):
        r = self.replay(EpicPath, [capture(HOME), capture(DAILY), capture(HOME_AFTER)])
        self.assertTrue(r.task.run_claim(navigate=False))
        self.assertEqual(r.actions, [(0, "EPIC_PATH_TASK_ENTRY"), (1, "EPIC_PATH_TASK_CLOSE")])
        self.assertFalse(r.task.config.values)

    def test_epic_real_rewards_reorder_tasks_and_preserve_arena_recheck(self):
        r = self.replay(EpicPath, [capture(EPIC_HOME_READY), capture(EPIC_DAILY_READY),
                        capture(EPIC_REWARD), capture(EPIC_DAILY_AFTER), capture(EPIC_HOME_CLAIMED)])
        self.assertTrue(r.task.run_claim(navigate=False))
        self.assertEqual(r.actions, [(0, "EPIC_PATH_TASK_ENTRY"), (1, "EPIC_CLAIM_stamina_100"),
                                    (2, "TOUCH_TO_CLOSE"), (3, "EPIC_PATH_TASK_CLOSE")])
        self.assertFalse(r.task.config.values)
        self.assertEqual(r.index, 4)

    def test_epic_real_receipts_before_reward_keep_pending_task(self):
        r = self.replay(EpicPath, [capture(EPIC_HOME_READY), capture(EPIC_DAILY_READY)]
                        + [capture(EPIC_DAILY_AFTER)] * 3 + [capture(EPIC_REWARD),
                        capture(EPIC_DAILY_AFTER), capture(EPIC_HOME_CLAIMED)])
        self.assertTrue(r.task.run_claim(navigate=False))
        self.assertEqual(r.clicks("TOUCH_TO_CLOSE"), [5])
        self.assertEqual(r.clicks("EPIC_PATH_TASK_CLOSE"), [6])
        self.assertFalse(r.task.config.values)

    def test_epic_real_claim_and_popup_retry_with_production_intervals(self):
        r = self.replay(EpicPath, [capture(EPIC_HOME_READY)] + [capture(EPIC_DAILY_READY)] * 7
                        + [capture(EPIC_REWARD)] * 7 + [capture(EPIC_DAILY_AFTER), capture(EPIC_HOME_CLAIMED)])
        self.assertTrue(r.task.run_claim(navigate=False))
        r.assert_retried(self, "EPIC_CLAIM_stamina_100", 2)
        r.assert_retried(self, "TOUCH_TO_CLOSE", 2)
        self.assertFalse(r.clicks("EPIC_CLAIM_stamina_200"))
        self.assertFalse(r.task.config.values)

    def test_epic_real_received_marks_follow_task_identity_after_reorder(self):
        for name, expected in ((EPIC_DAILY_READY, {("stamina", 100): "pending", ("stamina", 200): "pending",
                                                 ("arena", 3): "incomplete", ("arena", 5): "incomplete"}),
                               (EPIC_DAILY_AFTER, {("stamina", 100): "received", ("stamina", 200): "received",
                                                  ("arena", 3): "incomplete", ("arena", 5): "incomplete"})):
            with self.subTest(name=name):
                r = self.replay(EpicPath, [capture(name)])
                results = Ocr(EPIC_PATH_TASK_LIST, lang="cn").detect_and_ocr(r.image)
                rows = r.task._daily_rows(results)
                self.assertIsNotNone(rows)
                self.assertEqual({key: state for key, (state, _) in rows.items()}, expected)
                if name == EPIC_DAILY_READY:
                    self.assertLess(rows[("stamina", 100)][1][1], rows[("arena", 3)][1][1])
                else:
                    self.assertGreater(rows[("stamina", 100)][1][1], rows[("arena", 3)][1][1])

    def test_epic_real_control_markers_reject_other_rows_and_overlays(self):
        before = [button.search for button in EPIC_PATH_TASK_RECEIVED.iter_buttons()]
        for name in (EPIC_HOME_READY, EPIC_DAILY_READY, EPIC_REWARD, EPIC_DAILY_AFTER, EPIC_HOME_CLAIMED):
            with self.subTest(name=name):
                r = self.replay(EpicPath, [capture(name)])
                self.assertEqual(r.task.match_template_color(EPIC_PATH_CHECK),
                                 name in (EPIC_HOME_READY, EPIC_HOME_CLAIMED))
                self.assertEqual(r.task.match_template_color(COMMON_ACTIVITY_CHECK),
                                 name in (EPIC_HOME_READY, EPIC_HOME_CLAIMED))
                self.assertEqual(r.task.match_template_color(EPIC_PATH_DAILY_TASK_CHECK),
                                 name in (EPIC_DAILY_READY, EPIC_DAILY_AFTER))
                self.assertEqual(r.task.match_template_color(EPIC_PATH_REWARD_POPUP_CHECK), name == EPIC_REWARD)
                states = [r.task._received_in_row((904, top + 16, 1080, bottom - 15))
                          for _, top, _, bottom in EpicPath.ROW_AREAS]
                self.assertEqual(states, [False, False, True, True] if name == EPIC_DAILY_AFTER else [False] * 4)
        self.assertEqual([button.search for button in EPIC_PATH_TASK_RECEIVED.iter_buttons()], before)

    def test_epic_synthetic_claim_waits_for_popup_and_returns_to_event(self):
        r = self.replay(EpicPath, [capture(HOME), capture(DAILY, epic_receipts=(1, 2, 3))]
                        + [capture(DAILY, epic_receipts=(0, 1, 2, 3))] * 4
                        + [capture(EPIC_REWARD), capture(DAILY, epic_receipts=(0, 1, 2, 3)), capture(HOME_AFTER)])
        with patch("tasks.activity.epic_path.Ocr") as ocr:
            ocr.return_value.detect_and_ocr.side_effect = lambda image: daily_ocr(
                "pending" if r.index == 1 else "received")
            self.assertTrue(r.task.run_claim(navigate=False))
        self.assertEqual(r.clicks("EPIC_CLAIM_arena_3"), [1])
        self.assertEqual(r.clicks("TOUCH_TO_CLOSE"), [6])
        self.assertEqual(r.clicks("EPIC_PATH_TASK_CLOSE"), [7])
        self.assertEqual(r.index, 8)
        self.assertTrue(r.task.config.values)

    def test_epic_unknown_status_never_closes_or_marks_done(self):
        r = self.replay(EpicPath, [capture(HOME)] + [capture(DAILY, seconds=1)] * 48)
        with patch("tasks.activity.epic_path.Ocr") as ocr:
            ocr.return_value.detect_and_ocr.return_value = daily_ocr("unknown")
            self.assertFalse(r.task.run_claim(navigate=False))
        self.assertEqual(r.actions, [(0, "EPIC_PATH_TASK_ENTRY")])
        self.assertFalse(r.task.config.values)

    def test_epic_parser_rejects_partial_duplicate_and_wrong_task_set(self):
        r = self.replay(EpicPath, [capture(DAILY, epic_receipts=(0, 1, 2, 3))])
        worker = r.task
        results = daily_ocr("received")
        self.assertIsNotNone(worker._daily_rows(results))
        self.assertIsNone(worker._daily_rows(results[:-2]))
        results[2].ocr_text = results[0].ocr_text
        self.assertIsNone(worker._daily_rows(results))

    def test_shared_batch_processes_three_real_selected_pages_and_keeps_dialogs_closed(self):
        r = self.replay(CommonActivityBatch, [capture(AVAILABLE), capture(POPUP), capture(RECEIVED),
                        capture(HOME), capture(DAILY), capture(HOME_AFTER), capture(ATTENDANCE)])
        pending = {}
        for mode in ("completion_record", "detective_search", "epic_path"):
            keyword, marker, worker_type = CommonActivityBatch.ACTIVITIES[mode]
            worker = object.__new__(worker_type)
            worker.device, worker.config, worker.interval_timer = r, r.task.config, {}
            worker.activity_id = mode
            pending[mode] = (keyword, marker, worker)
        self.assertTrue(r.task._run_pending(pending))
        self.assertFalse(pending)
        self.assertEqual(r.actions, [(0, "DETECTIVE_CLAIM_180"), (1, "TOUCH_TO_CLOSE"),
                                    (3, "EPIC_PATH_TASK_ENTRY"), (4, "EPIC_PATH_TASK_CLOSE")])
        self.assertEqual(len(r.task.config.values), 2)

    def test_detective_network_dialog_does_not_confirm_pending_reward(self):
        from tests.support.interaction import scene
        network = scene("NETWORK_ERROR_ABNORMAL", "TOUCH_TO_CLOSE")
        r = self.replay(DetectiveSearch, [capture(AVAILABLE), network] + [capture(RECEIVED)] * 4
                        + [capture(POPUP), capture(RECEIVED)])
        self.assertTrue(r.task.run_claim(navigate=False))
        self.assertEqual(r.clicks("TOUCH_TO_CLOSE"), [1, 6])
        self.assertEqual(r.index, 7)

    def test_epic_open_and_close_retries_use_separate_production_intervals(self):
        r = self.replay(EpicPath, [capture(HOME)] * 7
                        + [capture(DAILY, epic_receipts=(0, 1, 2, 3))] * 7 + [capture(HOME_AFTER)])
        with patch("tasks.activity.epic_path.Ocr") as ocr:
            ocr.return_value.detect_and_ocr.return_value = daily_ocr("received")
            self.assertTrue(r.task.run_claim(navigate=False))
        r.assert_retried(self, "EPIC_PATH_TASK_ENTRY", 2)
        r.assert_retried(self, "EPIC_PATH_TASK_CLOSE", 2)
        self.assertEqual(r.clicks("EPIC_PATH_TASK_CLOSE")[0], 7)
        self.assertTrue(r.task.config.values)


def daily_ocr(state):
    """Synthetic full-completion counters; real captures cover stamina only."""
    rows = []
    for index, (family, total) in enumerate((("arena", 3), ("arena", 5), ("stamina", 100), ("stamina", 200))):
        top = EpicPath.ROW_AREAS[index][1]
        label = "畅玩竞技场" if family == "arena" else "消耗行动力(购买光谱核心除外)"
        rows.append(SimpleNamespace(ocr_text=f"{label}({total}/{total})", box=(206, top + 30, 780, top + 60), score=1))
        if state == "unknown" or state == "pending" and index == 0:
            text = "???" if state == "unknown" else "领取"
            rows.append(SimpleNamespace(ocr_text=text, box=(964, top + 30, 1020, top + 60), score=1))
    return rows


class OctoberSchedulingTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")
        from tests.support.meirin_reward import Config
        self.config = Config()
        self.config.LimitedActivity_GetDetectiveSearch = True
        self.config.LimitedActivity_GetEpicPathDailyReward = True
        self.now = datetime.fromisoformat("2026-10-29T21:00:00+08:00")

    def test_new_events_are_international_only_and_have_end_boundaries(self):
        events = calendar.active_activities(self.config, self.now, task="LimitedActivity")
        self.assertEqual({e.mode for e in events}, {"support_reward", "completion_record", "detective_search", "epic_path"})
        for event in events:
            self.assertFalse(event.contains(event.end))
        self.config.Emulator_PackageName = "com.zlongame.cn.epicseven"
        server.set_lang("cn")
        self.assertFalse(any(e.mode in ("completion_record", "detective_search", "epic_path")
                             for e in calendar.active_activities(self.config, self.now)))

    def test_battle_and_arena_wake_each_unfinished_event_and_stop_after_receipt(self):
        events = calendar.active_activities(self.config, self.now, task="LimitedActivity")
        for mode, option in (("detective_search", "GetDetectiveSearch"), ("epic_path", "GetEpicPathDailyReward")):
            event = next(e for e in events if e.mode == mode)
            with patch.object(scheduling, "active_activities", side_effect=lambda config, *, task, event=event: (event,) if task == "LimitedActivity" else ()):
                for arena in (False, True):
                    scheduling.schedule_activity_after_battle(self.config, arena=arena)
                self.assertEqual(self.config.calls, [("LimitedActivity", {"force_call": False})] * 2)
                self.config.calls.clear()
                self.config.values[f"LimitedActivity.LimitedActivity.{option}"] = False
                self.assertFalse(scheduling.should_schedule_after_battle(self.config))
                self.config.values[f"LimitedActivity.LimitedActivity.{option}"] = True
                self.config.values[scheduling._checked_path(self.config, event.event_id)] = datetime(2026, 10, 29, 21)
                with patch.object(scheduling, "get_server_last_update", return_value=datetime(2026, 10, 29, 2)):
                    self.assertFalse(scheduling.should_schedule_after_battle(self.config))
                with patch.object(scheduling, "get_server_last_update", return_value=datetime(2026, 10, 30, 2)):
                    self.assertTrue(scheduling.should_schedule_after_battle(self.config))

    def test_attendance_does_not_wake_after_battle_and_flags_are_registered(self):
        event = next(e for e in calendar.active_activities(self.config, self.now) if e.mode == "completion_record")
        with patch.object(scheduling, "active_activities", return_value=(event,)):
            self.assertFalse(scheduling.should_schedule_after_battle(self.config))
        self.assertIs(CommonActivityBatch.ACTIVITIES["completion_record"][2], CompletionRecord)

    def test_disabled_task_does_not_wake_battle_rewards(self):
        self.config.enabled = False
        with patch.object(scheduling, "active_activities") as active:
            self.assertFalse(scheduling.should_schedule_after_battle(self.config))
            active.assert_not_called()

    def test_activity_route_and_modal_registration_are_isolated_by_server(self):
        code = '''
from types import SimpleNamespace
from unittest.mock import Mock
from module.config import server
server.server = {package!r}
server.set_lang({lang!r})
from tasks.base import page
from tasks.base.ui import UI
from tasks.activity.calendar import active_activities
assert ('page_epic_path_daily' in page.Page.all_pages) == ({lang!r} == 'global_cn')
assert page.page_main.links[page.page_common_activity].name == 'MAIN_GOTO_COMMON_ACTIVITY'
page.Page.init_connection(page.page_common_activity)
assert page.page_main.parent is page.page_common_activity
if {lang!r} == 'global_cn':
    modal = page.page_epic_path_daily
    assert modal.parent is page.page_common_activity
    assert modal.links[page.page_common_activity].name == 'EPIC_PATH_TASK_CLOSE'
    ui = object.__new__(UI)
    ui.device = SimpleNamespace(image=object(), screenshot=Mock(side_effect=AssertionError('real device call')))
    ui.is_in_main = lambda interval=0: False
    ui.appear = lambda button, **kwargs: button is modal.check_button
    assert ui.ui_get_current_page() is modal
    ui.device.screenshot.assert_not_called()
page.Page.clear_connection()
'''
        for package, lang in (("CN-Official", "cn"), ("OVERSEA-Play", "global_cn"), ("OVERSEA-Play", "global_en")):
            with self.subTest(lang=lang):
                result = subprocess.run([sys.executable, "-B", "-X", "utf8", "-c", code.format(package=package, lang=lang)],
                                        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=45, check=False)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
