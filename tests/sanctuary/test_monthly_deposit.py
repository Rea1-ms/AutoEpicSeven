"""Offline screenshot and state replay coverage for monthly deposit capacity."""

import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from module.config import server as server_

server_.set_lang('global_cn')

from module.base.button import ClickButton  # noqa: E402
from tasks.sanctuary import monthly_deposit as deposit  # noqa: E402
from tasks.sanctuary.monthly import DEPOSIT_BOX_NOT_FULL  # noqa: E402
from tasks.sanctuary.sanctuary import Sanctuary  # noqa: E402
from tests.support.sanctuary import MonthlyReplay, PROJECT_ROOT, screenshot  # noqa: E402


class DepositScreenshotTests(unittest.TestCase):
    def setUp(self):
        server_.set_lang("global_cn")
        self.image = screenshot("full_s_20260921.png")

    def task(self, image):
        task = Sanctuary.__new__(Sanctuary)
        task.device = SimpleNamespace(image=image)
        task.appear = lambda asset, **kwargs: asset.match_template(image)
        return task

    def test_five_s(self):
        self.assertEqual(deposit.match_deposit_tiers(self.image), ("S",) * 5)
        self.assertIs(self.task(self.image)._is_monthly_deposit_box_full(), True)

    def test_four_s_one_b(self):
        image = screenshot("full_b_20260921.png")
        self.assertEqual(deposit.match_deposit_tiers(image), ("S", "S", "S", "S", "B"))
        self.assertIs(self.task(image)._is_monthly_deposit_box_full(), True)

    def test_four_s_one_a(self):
        image = screenshot("full_a_20260921.png")
        self.assertEqual(deposit.match_deposit_tiers(image), ("S", "S", "S", "S", "A"))
        self.assertIs(self.task(image)._is_monthly_deposit_box_full(), True)

    def test_a_again_after_animation_settled(self):
        image = screenshot("full_a_stable_20260921.png")
        self.assertEqual(deposit.match_deposit_tiers(image), ("S", "S", "S", "S", "A"))
        self.assertIs(self.task(image)._is_monthly_deposit_box_full(), True)

    def test_shared_assets_dispatch_for_cn(self):
        server_.set_lang("cn")
        try:
            self.assertIs(self.task(self.image)._is_monthly_deposit_box_full(), True)
        finally:
            server_.set_lang("global_cn")

    def test_each_missing_slot_keeps_capacity_unknown(self):
        for slot in range(5):
            with self.subTest(slot=slot):
                image = self.image.copy()
                left = 808 + slot * 87
                image[380:419, left:left+87] = 0
                self.assertIsNone(self.task(image)._is_monthly_deposit_box_full())

    def test_blank_page_is_unknown(self):
        image = np.zeros_like(self.image)
        self.assertIsNone(self.task(image)._is_monthly_deposit_box_full())

    def test_visible_free_slot_allows_progress(self):
        image = np.array(Image.open(PROJECT_ROOT / DEPOSIT_BOX_NOT_FULL.buttons[0].file).convert("RGB"))
        self.assertIs(self.task(image)._is_monthly_deposit_box_full(), False)

    def test_left_reward_tier_is_not_part_of_the_deposit_count(self):
        image = self.image.copy()
        image[380:419, 1156:1248] = 0
        image[382:416, 347:411] = self.image[382:416, 821:885]
        self.assertEqual(sum(t is not None for t in deposit.match_deposit_tiers(image)), 4)

    def test_mixed_known_tiers_in_all_positions(self):
        sources = {
            "S": self.image[382:416, 821:885],
            "A": screenshot("full_a_20260921.png")[382:416, 1169:1233],
            "B": screenshot("full_b_20260921.png")[382:416, 1169:1233],
        }
        image = self.image.copy()
        expected = ("A", "B", "S", "B", "A")
        for index, tier in enumerate(expected):
            left = 821 + index * 87
            image[382:416, left:left+64] = sources[tier]
        self.assertEqual(deposit.match_deposit_tiers(image), expected)

    def test_synthetic_compound_s_labels_are_not_single_s(self):
        glyph = self.image[384:411, 842:863].copy()
        for starts in ((1180, 1202), (1169, 1190, 1211)):
            with self.subTest(starts=starts):
                image = self.image.copy()
                image[380:419, 1162:1240] = self.image[416, 1200]
                for left in starts:
                    image[384:411, left:left+21] = glyph
                self.assertIsNone(self.task(image)._is_monthly_deposit_box_full())

    def test_repeated_hits_in_one_slot_do_not_count_as_five(self):
        hits = [ClickButton((821 + shift, 382, 885 + shift, 416)) for shift in range(5)]
        with patch.object(deposit.DEPOSIT_REWARD_TIER_S, "match_multi_template", return_value=hits), \
             patch.object(deposit.DEPOSIT_REWARD_TIER_A, "match_multi_template", return_value=[]), \
             patch.object(deposit.DEPOSIT_REWARD_TIER_B, "match_multi_template", return_value=[]):
            self.assertEqual(deposit.match_deposit_tiers(self.image), ("S", None, None, None, None))

    def test_conflicting_labels_in_one_slot_are_unknown(self):
        hit = ClickButton((821, 382, 885, 416))
        with patch.object(deposit.DEPOSIT_REWARD_TIER_S, "match_multi_template", return_value=[hit]), \
             patch.object(deposit.DEPOSIT_REWARD_TIER_A, "match_multi_template", return_value=[hit]), \
             patch.object(deposit.DEPOSIT_REWARD_TIER_B, "match_multi_template", return_value=[]):
            self.assertEqual(deposit.match_deposit_tiers(self.image), (None,) * 5)


class DepositWorkflowTests(unittest.TestCase):
    def flow(self, frames, mode="Smart"):
        flow = MonthlyReplay(frames, mode)
        base_screenshot = flow.task.device.screenshot
        full_image = screenshot("full_s_20260921.png")

        def next_frame():
            base_screenshot()
            if flow.frame.get("full"):
                flow.task.device.image = full_image.copy()
        flow.task.device.screenshot = next_frame
        return flow

    def test_full_box_stops_both_modes_without_clicks(self):
        for mode in ("Smart", "SS"):
            with self.subTest(mode=mode):
                flow = self.flow([{"full": True}], mode)
                self.assertEqual(flow.run(), Sanctuary.MONTHLY_STATUS_FULL)
                self.assertEqual(flow.actions, [])

    def test_claimed_precedes_full_box(self):
        for mode in ("Smart", "SS"):
            with self.subTest(mode=mode):
                flow = self.flow([{"full": True, "claimed": True}], mode)
                self.assertEqual(flow.run(), Sanctuary.MONTHLY_STATUS_CLAIMED)
                self.assertEqual(flow.actions, [])

    def test_full_box_does_not_require_counter_ocr(self):
        for mode in ("Smart", "SS"):
            with self.subTest(mode=mode):
                flow = self.flow([{"full": True, "counter": (0, 0, 0, None)}], mode)
                flow.task._ocr_purify_times = Mock(side_effect=AssertionError("Counter must not be read"))
                self.assertEqual(flow.run(), Sanctuary.MONTHLY_STATUS_FULL)

    def test_box_becomes_full_after_confirmed_custody(self):
        # Only the stable added reward confirms the transaction. A transient
        # duplicate-click notice alone cannot release the protected reward.
        stored = {"deposit_tiers": ("S", None, None, None, None), "custody": False}
        flow = self.flow([{"cancel": True}, {}, {}] + [stored] * 2 + [{"full": True}])
        self.assertEqual(flow.run(), Sanctuary.MONTHLY_STATUS_FULL)
        self.assertEqual([action[1] for action in flow.actions], ["POPUP_CANCEL", "CUSTODY"])

    def test_full_status_returns_to_sanctuary_and_schedules_next_week(self):
        flow = self.flow([{"full": True}])
        flow.task.config.Scheduler_ServerUpdate = "03:00"
        flow.task.config.task_delay = Mock()
        flow.task._back_to_sanctuary = Mock(return_value=True)
        target = datetime(2026, 9, 28, 3)
        with patch("tasks.sanctuary.sanctuary.get_server_next_monday_update", return_value=target):
            self.assertTrue(flow.run(public=True))
        self.assertEqual(flow.task._monthly_status, Sanctuary.MONTHLY_STATUS_FULL)
        flow.task.config.task_delay.assert_called_once_with(target=target)
        self.assertEqual(flow.task._back_to_sanctuary.call_count, 2)

    def test_full_status_still_uses_month_end_reminder_schedule(self):
        flow = self.flow([{"full": True}])
        flow.task.config.Scheduler_ServerUpdate = "03:00"
        flow.task.config.task_delay = Mock()
        flow.task._send_monthly_reward_reminder = Mock()
        target = datetime(2026, 9, 28, 3)
        flow.task._monthly_reminder_delay = Mock(return_value=target)
        with patch("tasks.sanctuary.sanctuary.get_server_next_monday_update",
                   return_value=datetime(2026, 10, 5, 3)):
            self.assertTrue(flow.run(public=True))
        flow.task._send_monthly_reward_reminder.assert_called_once()
        flow.task.config.task_delay.assert_called_once_with(target=target)
