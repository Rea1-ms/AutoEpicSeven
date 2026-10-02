# ruff: noqa: E402
from module.config import server as _test_server

_test_server.set_lang("global_cn")

"""Rotating Huche batches: screenshot boundaries and continuous scan coverage."""
import unittest
from dataclasses import replace
from unittest.mock import patch

from tests.support.history_huche_shop import Flow, EVENT_ID, WINDOW, NOW
import module.config.server as server
from tasks.activity import scheduling
from tasks.activity.huche_shop import HucheShop, DiscountItem, DiscountView, DiscountBatchScan

TOP = DiscountView(tuple(DiscountItem(y, name, False) for y, name in zip(
    (85, 230, 375, 520), ("圣约书签", "装备重铸精髓", "上级转换石选择箱", "5★英雄召唤券"))), None)
EDGE = DiscountView((DiscountItem(69, "5★英雄召唤券", False),
                     DiscountItem(214, "飞翔的灵药", False),
                     DiscountItem(360, "随机Lv.85特化饰品", False)), (634, 583, 713, 606))


def stable(scan, view):
    scan.observe(view)
    return scan.observe(view)


def at_top():
    scan = DiscountBatchScan()
    assert stable(scan, TOP) == "down"
    return scan




class CoverageTests(unittest.TestCase):
    def test_complete_only_after_top_and_overlapping_boundary(self):
        scan = at_top()
        scan.scrolled(TOP, "down")
        self.assertEqual(stable(scan, EDGE), "complete")

    def test_boundary_in_middle_cannot_complete_after_failed_upward_swipes(self):
        scan = DiscountBatchScan()
        for _ in range(4):
            self.assertEqual(stable(scan, EDGE), "up")
            scan.scrolled(EDGE, "up")
        self.assertFalse(scan.top)

    def test_repeated_frames_do_not_count_as_extra_swipes(self):
        scan = DiscountBatchScan()
        for _ in range(5):
            self.assertEqual(stable(scan, TOP), "down")
        self.assertTrue(scan.top)
        self.assertEqual(scan.scrolls, 0)

    def test_missing_overlap_or_failed_downward_swipe_cannot_complete(self):
        scan = at_top()
        scan.scrolled(TOP, "down")
        self.assertEqual(stable(scan, replace(EDGE, items=EDGE.items[1:])), "recover")
        self.assertEqual(stable(scan, TOP), "down")
        self.assertIsNotNone(scan.tail)

    def test_unmatched_mystic_name_blocks_absence_completion(self):
        scan = at_top()
        scan.scrolled(TOP, "down")
        view = replace(EDGE, items=EDGE.items + (DiscountItem(505, "神秘奖牌", True),))
        self.assertEqual(stable(scan, view), "target")
        self.assertEqual(scan.observe(None), "wait")
        self.assertEqual(scan.observe(view), "wait")


class FlowTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")

    def test_unreadable_batch_stops_after_bounded_retries_without_completion(self):
        flow = Flow(["empty"] * HucheShop.BATCH_UNVERIFIED_LIMIT)
        fixed_key = scheduling._checked_path(flow.config, EVENT_ID + "_regular_mystic")
        flow.config.values[fixed_key] = "2026-09-18 12:00:00"
        with patch("tasks.activity.huche_shop.aware_time", return_value=NOW), patch.object(
            flow, "read_discount_view", return_value=None
        ):
            self.assertFalse(flow.run_purchase(WINDOW))
        self.assertEqual(flow.frame + 1, HucheShop.BATCH_UNVERIFIED_LIMIT)
        self.assertEqual(flow.clicks, [])
        self.assertEqual(flow.scans, [])
        self.assertEqual(list(flow.config.values), [fixed_key])

    def test_possible_mystic_without_verified_offer_never_buys_or_completes(self):
        flow = Flow(["empty"] * (HucheShop.BATCH_UNVERIFIED_LIMIT + 1))
        fixed_key = scheduling._checked_path(flow.config, EVENT_ID + "_regular_mystic")
        flow.config.values[fixed_key] = "2026-09-18 12:00:00"
        view = replace(EDGE, items=(DiscountItem(69, "神秘奖牌", True),) + EDGE.items[1:])
        scan = at_top()
        with patch("tasks.activity.huche_shop.aware_time", return_value=NOW), patch.object(
            flow, "read_discount_view", return_value=view
        ), patch("tasks.activity.huche_shop.DiscountBatchScan", return_value=scan):
            self.assertFalse(flow.run_purchase(WINDOW))
        self.assertEqual(flow.clicks, [])
        self.assertEqual(list(flow.config.values), [fixed_key])

    def test_transient_unreadable_frames_reset_after_valid_progress(self):
        views = [None] * 3 + [TOP] * 2 + [EDGE] * 2
        flow = Flow(["empty"] * len(views))
        fixed_key = scheduling._checked_path(flow.config, EVENT_ID + "_regular_mystic")
        flow.config.values[fixed_key] = "2026-09-18 12:00:00"
        with patch("tasks.activity.huche_shop.aware_time", return_value=NOW), patch.object(
            flow, "read_discount_view", side_effect=lambda: views[flow.frame]
        ):
            self.assertTrue(flow.run_purchase(WINDOW))
        self.assertEqual(flow.clicks, [])
        self.assertEqual(len(flow.scans), 1)

    def test_middle_entry_returns_top_then_scans_down_without_extra_probe(self):
        views = [EDGE] * 2 + [TOP] * 2 + [EDGE] * 2
        flow = Flow(["empty"] * len(views))
        fixed_key = scheduling._checked_path(flow.config, EVENT_ID + "_regular_mystic")
        flow.config.values[fixed_key] = "2026-09-18 12:00:00"
        with patch("tasks.activity.huche_shop.aware_time", return_value=NOW), patch.object(
            flow, "read_discount_view", side_effect=lambda: views[flow.frame]
        ):
            self.assertTrue(flow.run_purchase(WINDOW))
        self.assertEqual(flow.clicks, [])
        self.assertEqual(flow.scans, [((850, 180), (850, 620)), ((850, 620), (850, 475))])

    def test_fixed_quota_done_and_no_medals_waits_for_next_batch(self):
        views = [TOP] * 2 + [EDGE] * 2
        flow = Flow(["empty"] * len(views))
        fixed_key = scheduling._checked_path(flow.config, EVENT_ID + "_regular_mystic")
        flow.config.values[fixed_key] = "2026-09-18 12:00:00"
        with patch("tasks.activity.huche_shop.aware_time", return_value=NOW), patch.object(
            flow, "read_discount_view", side_effect=lambda: views[flow.frame]
        ):
            self.assertTrue(flow.run_purchase(WINDOW))
        self.assertEqual(flow.clicks, [])
        self.assertEqual(len(flow.scans), 1)
        self.assertEqual(flow.scans, [((850, 620), (850, 475))])
        self.assertEqual(flow.scans[-1][0][1] - flow.scans[-1][1][1], 145)
        self.assertEqual(flow.config.values[fixed_key], "2026-09-18 12:00:00")
        self.assertIn(scheduling._checked_path(flow.config, EVENT_ID), flow.config.values)
        self.assertFalse(scheduling.is_activity_checked_in_window(flow.config, WINDOW, NOW.replace(hour=23)))

    def test_no_fixed_quota_record_still_searches_for_regular_stock(self):
        flow = Flow(["empty"] * 14)
        with patch("tasks.activity.huche_shop.aware_time", return_value=NOW), patch.object(
            flow, "read_discount_view", return_value=EDGE
        ) as read_batch:
            self.assertFalse(flow.run_purchase(WINDOW))
        read_batch.assert_not_called()
        self.assertEqual(flow.config.values, {})

    def test_refresh_boundary_does_not_mark_previous_empty_batch(self):
        flow = Flow(["empty"] * 4)
        key = scheduling._checked_path(flow.config, EVENT_ID + "_regular_mystic")
        flow.config.values[key] = "2026-09-18 12:00:00"
        views = [TOP] * 2 + [EDGE] * 2
        times = [NOW] * 5 + [NOW.replace(hour=23)]
        with patch("tasks.activity.huche_shop.aware_time", side_effect=times), patch.object(
            flow, "read_discount_view", side_effect=lambda: views[flow.frame]
        ):
            self.assertFalse(flow.run_purchase(WINDOW))
        self.assertNotIn(scheduling._checked_path(flow.config, EVENT_ID), flow.config.values)


    def test_refresh_during_final_ocr_does_not_mark_new_batch(self):
        flow = Flow(["empty"] * 4)
        key = scheduling._checked_path(flow.config, EVENT_ID + "_regular_mystic")
        flow.config.values[key] = "2026-09-18 12:00:00"
        views = [TOP] * 2 + [EDGE] * 2
        with patch("tasks.activity.huche_shop.aware_time", side_effect=[NOW] * 6 + [NOW.replace(hour=23)]), patch.object(
            flow, "read_discount_view", side_effect=lambda: views[flow.frame]
        ):
            self.assertFalse(flow.run_purchase(WINDOW))
        self.assertNotIn(scheduling._checked_path(flow.config, EVENT_ID), flow.config.values)
