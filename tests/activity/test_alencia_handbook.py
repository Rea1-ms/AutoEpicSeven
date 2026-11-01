"""Handbook daily claims, combined receipt, home routes and task-owned rechecks."""

import subprocess
import sys
import unittest
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime
from unittest.mock import Mock, patch

from module.config import server
from tests.support.offline import ROOT, ControlledClock, fixture_image

server.set_lang("global_cn")

from module.config.config_updater import ConfigUpdater
from module.ocr.ocr import Ocr
from tasks.activity import calendar, scheduling
from tasks.activity.alencia_handbook import AlenciaHandbook
from tasks.activity.assets.assets_activity_alencia_handbook import (
    ALENCIA_HANDBOOK_CHECK,
    ALENCIA_HUB_CHECK,
    ALENCIA_REWARD_POPUP_CHECK,
    ALENCIA_REWARDS_CHECK,
    ALENCIA_TASK_LIST,
)
from tasks.activity.entry import SpecialActivityEntry
from tests.support.alencia_handbook import (
    AFTER,
    BOOK,
    BOOK_AFTER,
    BOTTOM,
    CAPTURES,
    HUB,
    HUB_AFTER,
    MANIFEST,
    POPUP,
    READY,
    HandbookReplay,
    capture,
    chapter_results,
    daily_results,
)

NOW = datetime.fromisoformat("2026-11-01T00:10:00+08:00")
EVENT_ID = "alencia_handbook_2026_11_01"


class HandbookRecognitionTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")

    def test_real_pages_are_exclusive_and_popup_rejects_underlying_list(self):
        for name in CAPTURES:
            image = fixture_image(MANIFEST, f"{name}")
            for marker, expected in (
                (ALENCIA_HUB_CHECK, name in (HUB, HUB_AFTER)),
                (ALENCIA_HANDBOOK_CHECK, name in (BOOK, BOOK_AFTER)),
                (ALENCIA_REWARDS_CHECK, name in (READY, AFTER, BOTTOM)),
                (ALENCIA_REWARD_POPUP_CHECK, name == POPUP),
            ):
                with self.subTest(name=name, marker=marker.name):
                    self.assertEqual(marker.match_template_color(image), expected)

    def test_real_ocr_pairs_reordered_daily_with_its_own_counter_and_check(self):
        worker = object.__new__(AlenciaHandbook)
        worker.match_template_color = lambda button: button.match_template_color(worker.device.image)
        for name, expected in ((READY, {200: "pending", 400: "incomplete"}),
                               (AFTER, {400: "incomplete"}), (BOTTOM, {})):
            image = fixture_image(MANIFEST, f"{name}")
            worker.device = Mock(image=image)
            rows = worker._daily_rows(Ocr(ALENCIA_TASK_LIST, lang="cn").detect_and_ocr(image))
            self.assertEqual({tier: row[0] for tier, row in rows.items()}, expected)

    def test_incomplete_fields_and_wrong_counter_never_authorize_claim(self):
        worker = object.__new__(AlenciaHandbook)
        worker._received_in_row = lambda area: False
        for title in ("【每日成就】使用200点行动力", "【每日成就】使用200点行动力（1光谱核心=160"):
            rows = daily_results({200: "pending"})
            rows[0].ocr_text = title
            self.assertEqual(worker._daily_rows(rows)[200][0], "pending")
        for mutate in (
            lambda rows: rows.pop(1),
            lambda rows: setattr(rows[1], "ocr_text", "200/400"),
            lambda rows: setattr(rows[0], "ocr_text", "使用200点行动力"),
            lambda rows: setattr(rows[0], "score", 0.7),
            lambda rows: setattr(rows[0], "box", (476, 87, 860, 107)),
        ):
            rows = daily_results({200: "pending"})
            mutate(rows)
            self.assertEqual(worker._daily_rows(rows), {})


class HandbookReplayTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.clock = self.stack.enter_context(ControlledClock())

    def replay(self, frames):
        replay = HandbookReplay(frames, self.clock)
        self.device = replay
        reader = self.stack.enter_context(patch("tasks.activity.alencia_handbook.Ocr"))
        reader.return_value.detect_and_ocr.side_effect = replay.ocr_results
        return replay

    def test_both_available_require_one_click_and_one_popup_for_both_receipts(self):
        r = self.replay([capture(daily={200: "pending", 400: "pending"}), capture(POPUP),
                         capture(ocr=chapter_results()), capture(BOOK_AFTER), capture(HUB_AFTER)])
        self.assertTrue(r.task.run_claim(navigate=False))
        self.assertEqual(r.actions, [(0, "ALENCIA_CLAIM_200"), (1, "ALENCIA_REWARD_CLOSE"),
                                     (2, "ALENCIA_REWARDS_BACK"), (3, "ALENCIA_HANDBOOK_BACK")])
        self.assertTrue(r.values)

    def test_real_partial_claim_scans_bottom_and_keeps_battle_recheck(self):
        # The former bottom scan must end as soon as the remaining 400 daily
        # is positively unfinished at the top after the actual receipt.
        r = self.replay([capture(), capture(POPUP), capture(AFTER),
                         capture(BOOK_AFTER), capture(HUB_AFTER)])
        self.assertTrue(r.task.run_claim(navigate=False))
        self.assertEqual(r.clicks("ALENCIA_CLAIM_200"), [0])
        self.assertFalse(r.clicks("ALENCIA_CLAIM_400"))
        self.assertFalse(r.values)
        self.assertEqual(r.actions[-2:], [(2, "ALENCIA_REWARDS_BACK"), (3, "ALENCIA_HANDBOOK_BACK")])
        self.assertFalse(any(action == "swipe" for _, action in r.actions))

    def test_either_unfinished_top_daily_returns_without_scanning_other_tier(self):
        for tier in (200, 400):
            with self.subTest(tier=tier):
                r = self.replay([capture(daily={tier: "incomplete"}),
                                 capture(BOOK_AFTER), capture(HUB_AFTER)])
                results = daily_results({tier: "incomplete"})[:2]
                with patch("tasks.activity.alencia_handbook.Ocr") as reader:
                    reader.return_value.detect_and_ocr.return_value = results
                    self.assertTrue(r.task.run_claim(navigate=False))
                    reader.return_value.detect_and_ocr.assert_called_once()
                self.assertEqual(r.actions, [(0, "ALENCIA_REWARDS_BACK"), (1, "ALENCIA_HANDBOOK_BACK")])
                self.assertFalse(r.values)
                r.task.config.task_delay.assert_not_called()

    def test_logged_80_progress_with_missing_status_returns_without_swiping(self):
        r = self.replay([capture(daily={200: "incomplete", 400: "incomplete"}),
                         capture(BOOK_AFTER), capture(HUB_AFTER)])
        results = daily_results({200: "incomplete", 400: "incomplete"})
        results[1].ocr_text = "80/200"
        results[4].ocr_text = "80/400"
        results.pop(5)  # The user's log has no readable status on the 400 row.
        with patch("tasks.activity.alencia_handbook.Ocr") as reader:
            reader.return_value.detect_and_ocr.return_value = results
            self.assertTrue(r.task.run_claim(navigate=False))
            reader.return_value.detect_and_ocr.assert_called_once()
        self.assertEqual(r.actions, [(0, "ALENCIA_REWARDS_BACK"), (1, "ALENCIA_HANDBOOK_BACK")])
        self.assertFalse(r.values)
        r.task.config.task_delay.assert_not_called()

    def test_logged_missing_parentheses_and_status_return_on_first_frame(self):
        # Exact strings from the 12:43 log; geometry is explicitly synthetic.
        results = daily_results({200: "incomplete", 400: "incomplete"})
        results[0].ocr_text = "【每日成就】使用200点行动力（1光谱核心=160"
        results[1].ocr_text = "80/200"
        results[3].ocr_text = "【每日成就】使用400点行动力（1光谱核心=160"
        results[4].ocr_text = "80/400"
        results = [results[index] for index in (0, 1, 3, 4)]
        r = self.replay([capture(ocr=results), capture(BOOK_AFTER), capture(HUB_AFTER)])
        self.assertTrue(r.task.run_claim(navigate=False))
        self.assertEqual(r.actions, [(0, "ALENCIA_REWARDS_BACK"), (1, "ALENCIA_HANDBOOK_BACK")])
        self.assertFalse(r.values)
        r.task.config.task_delay.assert_not_called()

    def test_chapters_at_top_finish_immediately_without_claim_or_swipe(self):
        r = self.replay([capture(ocr=chapter_results()), capture(BOOK_AFTER), capture(HUB_AFTER)])
        self.assertTrue(r.task.run_claim(navigate=False))
        self.assertEqual(r.actions, [(0, "ALENCIA_REWARDS_BACK"), (1, "ALENCIA_HANDBOOK_BACK")])
        self.assertTrue(r.values)
        r.task.config.task_delay.assert_not_called()

    def test_unreadable_top_never_completes_from_lower_chapter_tasks(self):
        for kind in ("empty", "missing", "unknown", "low_score"):
            with self.subTest(kind=kind):
                results = chapter_results()
                if kind == "empty":
                    results = []
                elif kind == "missing":
                    results = results[3:]
                elif kind == "unknown":
                    results[0].ocr_text = "未知任务"
                else:
                    results[0].score = 0.7
                r = self.replay([capture(ocr=results, seconds=2)] * 47)
                self.assertFalse(r.task.run_claim(navigate=False))
                self.assertFalse(r.values)
                self.assertEqual(r.actions, [])
                r.task.config.task_delay.assert_called_once_with(success=False)

    def test_incomplete_dailies_exit_without_claim_or_completion_record(self):
        r = self.replay([capture(daily={200: "incomplete", 400: "incomplete"}),
                         capture(BOOK_AFTER), capture(HUB_AFTER)])
        self.assertTrue(r.task.run_claim(navigate=False))
        self.assertEqual(r.actions, [(0, "ALENCIA_REWARDS_BACK"), (1, "ALENCIA_HANDBOOK_BACK")])
        self.assertFalse(r.values)

    def test_already_received_dailies_finish_without_claiming_achievements(self):
        r = self.replay([capture(daily={200: "received", 400: "received"}),
                         capture(BOOK_AFTER), capture(HUB_AFTER)])
        self.assertTrue(r.task.run_claim(navigate=False))
        self.assertTrue(r.values)
        self.assertFalse(any("CLAIM" in action for _, action in r.actions))

    def test_receipts_before_popup_cannot_finish_or_claim_another_tier(self):
        r = self.replay([capture(daily={200: "pending", 400: "pending"})]
                        + [capture(ocr=chapter_results())] * 7
                        + [capture(POPUP), capture(ocr=chapter_results()),
                           capture(BOOK_AFTER), capture(HUB_AFTER)])
        self.assertTrue(r.task.run_claim(navigate=False))
        self.assertEqual(r.clicks("ALENCIA_CLAIM_200"), [0])
        self.assertFalse(r.clicks("ALENCIA_CLAIM_400"))
        self.assertEqual(r.clicks("ALENCIA_REWARDS_BACK"), [9])

    def test_dropped_same_tap_and_popup_close_retry_at_independent_intervals(self):
        r = self.replay([capture(daily={200: "pending", 400: "pending"})] * 6
                        + [capture(POPUP)] * 6 + [capture(daily={200: "received", 400: "received"}),
                                                capture(BOOK_AFTER), capture(HUB_AFTER)])
        self.assertTrue(r.task.run_claim(navigate=False))
        r.assert_retried(self, "ALENCIA_CLAIM_200", 2)
        r.assert_retried(self, "ALENCIA_REWARD_CLOSE", 2)
        self.assertFalse(r.clicks("ALENCIA_CLAIM_400"))
        self.assertEqual(r.clicks("ALENCIA_REWARDS_BACK"), [12])

    def test_missing_popup_times_out_without_record_or_new_claim(self):
        r = self.replay([capture(daily={200: "pending", 400: "pending"})]
                        + [capture(ocr=chapter_results(), seconds=2)] * 47)
        self.assertFalse(r.task.run_claim(navigate=False))
        self.assertFalse(r.values)
        self.assertEqual(r.actions, [(0, "ALENCIA_CLAIM_200")])
        r.task.config.task_delay.assert_called_once_with(success=False)

    def test_new_400_claim_after_separate_200_receipt_requires_fresh_confirmation(self):
        r = self.replay([capture(daily={200: "pending", 400: "incomplete"}), capture(POPUP),
                         capture(daily={200: "received", 400: "pending"}), capture(POPUP),
                         capture(daily={200: "received", 400: "received"}),
                         capture(BOOK_AFTER), capture(HUB_AFTER)])
        self.assertTrue(r.task.run_claim(navigate=False))
        self.assertEqual(r.clicks("ALENCIA_CLAIM_200"), [0])
        self.assertEqual(r.clicks("ALENCIA_CLAIM_400"), [2])
        self.assertTrue(r.values)

    def test_one_visible_daily_and_chapter_checks_never_prove_daily_completion(self):
        # A malformed daily in the first card cannot be hidden by a valid
        # chapter in the second card. Missing fields remain unknown.
        results = daily_results({200: "pending"}) + chapter_results()[3:]
        results[0].ocr_text = "【每日成就】使用20点行动力"
        r = self.replay([capture(ocr=results, seconds=2)] * 47)
        self.assertFalse(r.task.run_claim(navigate=False))
        self.assertFalse(r.values)
        self.assertEqual(r.actions, [])

    def test_real_home_entry_routes_to_handbook_then_returns_to_home(self):
        # Page registration is conditional at import time. A clean process
        # keeps earlier CN page imports in the full suite out of this route.
        code = '''
from module.config import server
server.server = "OVERSEA-Play"
server.set_lang("global_cn")
from contextlib import ExitStack
from unittest.mock import patch
from module.ocr.ocr import Ocr
from tasks.base.page import page_main
from tests.support.offline import ControlledClock
from tests.support.alencia_handbook import (
    HOME, HUB, BOOK, BOOK_AFTER, HUB_AFTER, HOME_AFTER, HandbookReplay, capture,
)
with ExitStack() as stack:
    clock = ControlledClock()
    stack.enter_context(clock)
    r = HandbookReplay([capture(HOME), capture(HUB), capture(BOOK),
                        capture(daily={400: "incomplete"}),
                        capture(BOOK_AFTER), capture(HUB_AFTER), capture(HOME_AFTER)], clock)
    stack.enter_context(patch.object(Ocr, "detect_and_ocr", side_effect=r.ocr_results))
    assert r.task.run_claim()
    r.task.ui_goto(page_main)
    assert [name for _, name in r.actions] == ["MAIN_GOTO_ALENCIA_HUB", "ALENCIA_HANDBOOK_ENTRY",
        "ALENCIA_REWARDS_ENTRY", "ALENCIA_REWARDS_BACK", "ALENCIA_HANDBOOK_BACK", "BACK"], r.actions
    first = r.click_areas[0][2]
    assert 0 <= first[0] < first[2] <= 1280 and 460 < first[1] < 550, first
'''
        result = subprocess.run([sys.executable, "-X", "utf8", "-B", "-c", code], cwd=ROOT,
                                capture_output=True, text=True, encoding="utf-8", timeout=45, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class HandbookSchedulingTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")
        self.config = HandbookReplay([capture()], ControlledClock()).task.config

    def events(self, at=NOW, task="SpecialActivity"):
        return calendar.active_activities(self.config, at, task=task)

    def test_calendar_boundaries_and_server_language_scope(self):
        event = next(e for e in self.events() if e.mode == "alencia_handbook")
        self.assertTrue(event.contains(event.start))
        self.assertFalse(event.contains(event.end))
        self.assertFalse(any(e.mode == event.mode for e in self.events(task="LimitedActivity")))
        self.config.Emulator_PackageName = "CN-Official"
        self.assertFalse(any(e.mode == event.mode for e in self.events()))
        self.config.Emulator_PackageName = "OVERSEA-Play"
        self.assertFalse(calendar.active_activities(self.config, NOW, task="SpecialActivity", assets_lang="global_en"))

    def test_saved_visibility_uses_event_window_and_preserves_old_choices(self):
        data = {"Alas": {"Emulator": {"PackageName": "OVERSEA-Play", "GameLanguage": "cn"}},
                "SpecialActivity": {"Scheduler": {"Enable": False},
                                    "SpecialActivity": {"GetAlenciaDailyReward": False}}}
        before = deepcopy(data)
        key = "SpecialActivity.SpecialActivity.GetAlenciaDailyReward"
        for stamp, hidden in (("2026-11-01T00:04:59+08:00", True),
                              ("2026-11-01T00:05:00+08:00", False),
                              ("2026-12-10T11:00:00+08:00", True)):
            with patch.object(calendar, "datetime", wraps=datetime) as clock:
                clock.now.return_value = datetime.fromisoformat(stamp)
                self.assertEqual(key in ConfigUpdater().get_hidden_args(data), hidden)
        self.assertEqual(data, before)
        self.assertFalse(ConfigUpdater().config_update(data)["SpecialActivity"]["SpecialActivity"][
            "GetAlenciaDailyReward"])

    def test_battle_rechecks_special_task_until_both_receipts_then_next_reset(self):
        event = next(e for e in self.events() if e.mode == "alencia_handbook")
        values = {}
        self.config.cross_get = lambda key, default=None: values.get(key, default)
        with patch.object(scheduling, "active_activities", side_effect=lambda config, *, task: (event,) if task == "SpecialActivity" else ()):
            scheduling.schedule_activity_after_battle(self.config)
            self.config.task_call.assert_called_once_with("SpecialActivity", force_call=False)
            values["SpecialActivity.SpecialActivity.GetAlenciaDailyReward"] = False
            self.assertFalse(scheduling.should_schedule_after_battle(self.config, task="SpecialActivity"))
            values["SpecialActivity.SpecialActivity.GetAlenciaDailyReward"] = True
            values[scheduling._checked_path(self.config, EVENT_ID)] = datetime(2026, 11, 1, 3)
            with patch.object(scheduling, "get_server_last_update", return_value=datetime(2026, 11, 1, 2)):
                self.assertFalse(scheduling.should_schedule_after_battle(self.config, task="SpecialActivity"))
            with patch.object(scheduling, "get_server_last_update", return_value=datetime(2026, 11, 2, 2)):
                self.assertTrue(scheduling.should_schedule_after_battle(self.config, task="SpecialActivity"))

    def test_disabled_task_and_expired_event_never_wake(self):
        self.config.is_task_enabled = lambda task: False
        self.assertFalse(scheduling.should_schedule_after_battle(self.config, task="SpecialActivity"))
        self.config.is_task_enabled = lambda task: True
        with patch.object(calendar, "datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime.fromisoformat("2026-12-10T11:00:00+08:00")
            self.assertFalse(scheduling.should_schedule_after_battle(self.config, task="SpecialActivity"))

    def test_entry_dispatches_dedicated_home_flow_and_returns_once(self):
        event = next(e for e in self.events() if e.mode == "alencia_handbook")
        with patch("tasks.activity.entry.active_activities", return_value=(event,)), \
                patch("tasks.activity.entry.is_activity_checked_in_window", return_value=False), \
                patch("tasks.activity.entry.delay_next_activity_check"), \
                patch("tasks.activity.alencia_handbook.AlenciaHandbook") as worker:
            worker.return_value.run_claim.return_value = True
            self.assertTrue(SpecialActivityEntry(self.config, device=object()).run())
            worker.return_value.run_claim.assert_called_once_with()
            worker.return_value.ui_goto.assert_called_once()

    def test_page_import_scan_and_paths_are_isolated_for_three_clients(self):
        code = '''
from module.config import server
server.server = {package!r}
server.set_lang({lang!r})
from tasks.base import page
from tasks.activity.entry import SpecialActivityEntry
from tasks.activity.limited_activity import LimitedActivityEntry
from tasks.base.ui import UI
from types import SimpleNamespace
from unittest.mock import Mock
names = set(page.Page.all_pages)
expected = {{'page_alencia_hub', 'page_alencia_handbook', 'page_alencia_rewards'}}
assert (names & expected) == (expected if {lang!r} == 'global_cn' else set())
if {lang!r} == 'global_cn':
    for target, chain in ((page.page_alencia_rewards, [page.page_main, page.page_alencia_hub, page.page_alencia_handbook]),
                          (page.page_main, [page.page_alencia_rewards, page.page_alencia_handbook, page.page_alencia_hub])):
        page.Page.init_connection(target)
        for source in chain:
            assert source.parent is not None
    for name in expected:
        target = page.Page.all_pages[name]
        ui = object.__new__(UI)
        ui.device = SimpleNamespace(image=object(), screenshot=Mock(side_effect=AssertionError('device')))
        ui.is_in_main = lambda interval=0: False
        ui.appear = lambda button, **kwargs: button is target.check_button
        assert ui.ui_get_current_page() is target
        ui.device.screenshot.assert_not_called()
page.Page.clear_connection()
'''
        for package, lang in (("CN-Official", "cn"), ("OVERSEA-Play", "global_cn"), ("OVERSEA-Play", "global_en")):
            result = subprocess.run([sys.executable, "-X", "utf8", "-B", "-c", code.format(package=package, lang=lang)],
                                    cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=45, check=False)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
