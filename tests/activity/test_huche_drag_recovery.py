# ruff: noqa: E402
from module.config import server as _test_server

_test_server.set_lang("global_cn")

"""Regression for the September 22 Huche inertial overshoot and stuck scan."""
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

from tests.support.history_huche_shop import Flow, EVENT_ID, WINDOW, NOW
from tests.support.history_huche_discount_batch import stable
from tasks.activity import scheduling
from tasks.activity.huche_shop import DiscountBatchScan, DiscountItem, DiscountView, HucheShop


def rows(names, first=127):
    return tuple(DiscountItem(first + 145 * index, name, False) for index, name in enumerate(names))


TOP = DiscountView(rows(("生命之叶", "史诗书签", "圣约书签", "银河书签"), 85), None)
TRANSIENT = DiscountView(rows(("圣约书签", "银河书签", "命运之眼", "5★神器召唤券"), 98), None)
LOST = DiscountView(rows(("5★神器召唤券", "觉醒的灵药", "随机Lv.85特化装备")), (634, 640, 713, 663))
RECOVERED = DiscountView(rows(("银河书签", "命运之眼", "5★神器召唤券", "觉醒的灵药")), None)
NEXT = DiscountView(rows(("命运之眼", "5★神器召唤券", "觉醒的灵药", "随机Lv.85特化装备")), None)


class RecoveryTests(unittest.TestCase):
    def run_views(self, views, expected, scan=None):
        flow = Flow(["empty"] * len(views))
        fixed_key = scheduling._checked_path(flow.config, EVENT_ID + "_regular_mystic")
        flow.config.values[fixed_key] = "2026-09-18 12:00:00"
        flow.device.swipe = Mock(side_effect=AssertionError("Batch scanning must brake inertia with drag"))
        flow.device.drag = Mock(side_effect=lambda *a, **kw: flow.scans.append(a))
        scan = scan or DiscountBatchScan()
        with patch("tasks.activity.huche_shop.aware_time", return_value=NOW), patch.object(
            flow, "read_discount_view", side_effect=lambda: views[flow.frame]
        ), patch("tasks.activity.huche_shop.DiscountBatchScan", return_value=scan):
            self.assertEqual(flow.run_purchase(WINDOW), expected)
        self.assertEqual(flow.clicks, [])
        self.assertEqual(flow.config.values[fixed_key], "2026-09-18 12:00:00")
        self.assertEqual(scheduling._checked_path(flow.config, EVENT_ID) in flow.config.values, expected)
        flow.device.swipe.assert_not_called()
        return flow, scan


    def test_transient_overlap_during_inertia_does_not_complete_disconnected_boundary(self):
        views = [TOP] * 2 + [TRANSIENT] + [LOST] * 2 + [RECOVERED] * 2 + [NEXT] * 2 + [LOST] * 2
        flow, scan = self.run_views(views, True)
        self.assertEqual(flow.scans, [((850, 620), (850, 475)), ((850, 180), (850, 325)),
                                      ((850, 620), (850, 475)), ((850, 620), (850, 475))])
        self.assertEqual(scan.recoveries, 0)
        self.assertIsNone(scan.tail)

    def test_recovery_can_take_multiple_short_backward_drags(self):
        views = [TOP] * 2 + [LOST] * 4 + [RECOVERED] * 2 + [NEXT] * 2 + [LOST] * 2
        flow, scan = self.run_views(views, True)
        self.assertEqual(flow.scans.count(((850, 180), (850, 325))), 2)
        self.assertEqual(scan.recoveries, 0)

    def test_three_failed_recoveries_exit_without_completion_or_purchase(self):
        views = [TOP] * 2 + [LOST] * (2 * (HucheShop.BATCH_RECOVERY_LIMIT + 1))
        flow, scan = self.run_views(views, False)
        self.assertEqual(scan.tail.name, "银河书签")
        self.assertEqual(scan.recoveries, 3)
        self.assertEqual(flow.device.drag.call_count, 4)
        self.assertEqual(flow.frame + 1, len(views))

    def test_recovery_keeps_original_tail_and_does_not_count_observation_waits(self):
        scan = DiscountBatchScan()
        stable(scan, TOP)
        scan.scrolled(TOP, "down")
        for _ in range(5):
            self.assertEqual(stable(scan, LOST), "recover")
        self.assertEqual(scan.recoveries, 0)
        scan.scrolled(LOST, "recover")
        self.assertEqual(scan.tail, TOP.items[-1])
        self.assertEqual(scan.recoveries, 1)
        self.assertEqual(scan.observe(RECOVERED), "wait")
        self.assertEqual(scan.recoveries, 1)
        self.assertEqual(scan.observe(RECOVERED), "down")
        self.assertEqual(scan.recoveries, 0)

    def test_recovery_to_old_position_must_still_make_forward_progress(self):
        views = [TOP] * 2 + [LOST] * 2 + [TOP] * 2 + [RECOVERED] * 2 + [NEXT] * 2 + [LOST] * 2
        flow, scan = self.run_views(views, True)
        self.assertEqual(flow.device.drag.call_count, 5)
        self.assertEqual(scan.recoveries, 0)

    def test_failed_forward_drags_remain_bounded(self):
        limit = HucheShop.SCROLL_UP_LIMIT + HucheShop.SCROLL_DOWN_LIMIT
        flow, scan = self.run_views([TOP] * (2 * (limit + 1)), False)
        self.assertEqual(flow.device.drag.call_count, limit)
        self.assertEqual(scan.tail, TOP.items[-1])

    def test_unreadable_frames_after_overshoot_remain_bounded(self):
        views = [TOP] * 2 + [LOST] * 2 + [None] * HucheShop.BATCH_UNVERIFIED_LIMIT
        flow, scan = self.run_views(views, False)
        self.assertEqual(flow.device.drag.call_count, 2)
        self.assertEqual(scan.tail.name, "银河书签")

    def test_possible_mystic_after_recovery_is_not_treated_as_empty(self):
        target = replace(RECOVERED, items=RECOVERED.items + (DiscountItem(600, "神秘奖牌", True),))
        views = [TOP] * 2 + [LOST] * 2 + [target] * (HucheShop.BATCH_UNVERIFIED_LIMIT + 1)
        flow, _ = self.run_views(views, False)
        self.assertEqual(flow.device.drag.call_count, 2)
