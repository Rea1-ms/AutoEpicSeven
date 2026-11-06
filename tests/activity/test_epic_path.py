"""Epic-path daily task recognition, receipts and bounded claim replays."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from module.config import server
from tests.support.offline import ROOT, fixture_image

server.set_lang("global_cn")

from module.ocr.ocr import Ocr
from tasks.activity.assets.assets_activity_common import COMMON_ACTIVITY_CHECK
from tasks.activity.assets.assets_activity_epic_path import (
    EPIC_PATH_CHECK,
    EPIC_PATH_DAILY_TASK_CHECK,
    EPIC_PATH_REWARD_POPUP_CHECK,
    EPIC_PATH_TASK_LIST,
    EPIC_PATH_TASK_RECEIVED,
)
from tasks.activity.epic_path import EpicPath
from tests.support.activity_replay import (
    DAILY,
    EPIC_DAILY_AFTER,
    EPIC_DAILY_READY,
    EPIC_HOME_CLAIMED,
    EPIC_HOME_READY,
    EPIC_REWARD,
    HOME,
    HOME_AFTER,
    ActivityReplayCase,
    capture,
)

MANIFEST = ROOT / "tests/fixtures/historical/manifest.json"
NEUTRAL_EPIC_REWARD = "20261102-225549-949"


class EpicPathRecognitionTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")

    def test_epic_reward_background_variant_is_a_popup_not_daily_navigation(self):
        image = fixture_image(MANIFEST, NEUTRAL_EPIC_REWARD)
        self.assertTrue(EPIC_PATH_REWARD_POPUP_CHECK.match_template_color(image))
        self.assertFalse(EPIC_PATH_DAILY_TASK_CHECK.match_template_color(image))
        self.assertFalse(EPIC_PATH_CHECK.match_template_color(image))
        self.assertFalse(EPIC_PATH_REWARD_POPUP_CHECK.match_template_color(image // 2))
        daily = fixture_image(MANIFEST, EPIC_DAILY_READY)
        self.assertFalse(EPIC_PATH_REWARD_POPUP_CHECK.match_template_color(daily))

    def test_real_ocr_reads_four_daily_task_rows(self):
        image = fixture_image(MANIFEST, DAILY)
        results = Ocr(EPIC_PATH_TASK_LIST, lang='cn').detect_and_ocr(image)
        worker = object.__new__(EpicPath)
        rows = worker._daily_rows(results)
        self.assertIsNotNone(rows, [(r.ocr_text, r.box) for r in results])
        self.assertEqual(set(rows), EpicPath.DAILY_TASKS)
        self.assertTrue(all(state == 'incomplete' for (state, _) in rows.values()))


class EpicPathReplayTests(ActivityReplayCase):
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

    def test_epic_delayed_popup_and_receipt_never_replay_tab_or_claim(self):
        # Dimmed daily frames and delayed receipt frames are explicit simulated
        # timing, not a continuous recording of the user's device. Clicks never
        # advance the frames; the popup must be dismissed and the same task's
        # receipt must actually arrive before this claim is released.
        popup = dict(capture(EPIC_REWARD), fixture=NEUTRAL_EPIC_REWARD)
        frames = ([capture(EPIC_HOME_READY), capture(EPIC_DAILY_READY)]
                  + [capture(EPIC_DAILY_READY, dim=0.6)] * 6 + [popup] * 7
                  + [capture(EPIC_DAILY_READY)] * 7
                  + [capture(EPIC_DAILY_AFTER), capture(EPIC_HOME_CLAIMED)])
        r = self.replay(EpicPath, frames)
        click = r.click

        def checked_click(button):
            self.assertNotEqual(button.name, "EPIC_PATH_DAILY_TASK_CHECK",
                                "A dimmed daily or reward overlay cannot authorize a tab switch")
            if button.name == "EPIC_CLAIM_stamina_100":
                self.assertFalse(r.clicks(button.name), "A reward already shown must not be claimed again")
            click(button)

        r.click = checked_click
        self.assertTrue(r.task.run_claim(navigate=False))
        self.assertEqual(r.clicks("EPIC_CLAIM_stamina_100"), [1])
        self.assertFalse(r.clicks("EPIC_CLAIM_stamina_200"))
        self.assertEqual(r.clicks("TOUCH_TO_CLOSE")[0], 8)
        r.assert_retried(self, "TOUCH_TO_CLOSE", 2)
        self.assertEqual(r.clicks("EPIC_PATH_TASK_CLOSE"), [22])
        self.assertEqual(r.index, 23)
        self.assertFalse(r.task.config.values)

    def test_epic_missing_reward_on_dimmed_daily_times_out_without_tab_clicks(self):
        for claim_started in (False, True):
            with self.subTest(claim_started=claim_started):
                initial = [capture(EPIC_HOME_READY)] + ([capture(EPIC_DAILY_READY)] if claim_started else [])
                r = self.replay(EpicPath, initial + [capture(EPIC_DAILY_READY, dim=0.6, seconds=1)] * 48)
                self.assertFalse(r.task.run_claim(navigate=False))
                expected = [(0, "EPIC_PATH_TASK_ENTRY")]
                if claim_started:
                    expected.append((1, "EPIC_CLAIM_stamina_100"))
                self.assertEqual(r.actions, expected)
                self.assertFalse(r.task.config.values)

    def test_epic_unselected_daily_tab_requires_a_bright_usable_dialog(self):
        # There is no season-tab capture. Simulate only an unselected daily
        # label, keeping the real dialog close control bright. Season rows
        # must not be OCRed; subsequent daily/reward/receipt frames are real.
        inactive = capture(EPIC_DAILY_READY, dim=0.6, dim_area=(254, 102, 366, 163))
        r = self.replay(EpicPath, [capture(EPIC_HOME_READY)] + [inactive] * 7
                        + [capture(EPIC_DAILY_READY), capture(EPIC_REWARD),
                           capture(EPIC_DAILY_AFTER), capture(EPIC_HOME_CLAIMED)])
        read_rows = Ocr(EPIC_PATH_TASK_LIST, lang="cn").detect_and_ocr
        with patch("tasks.activity.epic_path.Ocr.detect_and_ocr", wraps=read_rows) as ocr:
            self.assertTrue(r.task.run_claim(navigate=False))
        r.assert_retried(self, "EPIC_PATH_DAILY_TASK_CHECK", 2)
        self.assertEqual(ocr.call_count, 2)
        self.assertEqual(r.clicks("EPIC_CLAIM_stamina_100"), [8])
        self.assertEqual(r.clicks("TOUCH_TO_CLOSE"), [9])
        self.assertEqual(r.clicks("EPIC_PATH_TASK_CLOSE"), [10])
        self.assertFalse(r.task.config.values)

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
