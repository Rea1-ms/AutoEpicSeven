# ruff: noqa: E402
from module.config import server as _test_server

_test_server.set_lang("global_cn")

from tests.support.offline import OfflineAssertions

"""Regression for the September 22 Huche inertial overshoot and stuck scan."""
from unittest.mock import Mock, patch

from tests.support.history_huche_shop import Flow, EVENT_ID, WINDOW, NOW
from tasks.activity import scheduling
from tasks.activity.huche_shop import DiscountBatchScan, DiscountItem, DiscountView


def rows(names, first=127):
    return tuple(DiscountItem(first + 145 * index, name, False) for index, name in enumerate(names))


TOP = DiscountView(rows(("生命之叶", "史诗书签", "圣约书签", "银河书签"), 85), None)
TRANSIENT = DiscountView(rows(("圣约书签", "银河书签", "命运之眼", "5★神器召唤券"), 98), None)
LOST = DiscountView(rows(("5★神器召唤券", "觉醒的灵药", "随机Lv.85特化装备")), (634, 640, 713, 663))
RECOVERED = DiscountView(rows(("银河书签", "命运之眼", "5★神器召唤券", "觉醒的灵药")), None)
NEXT = DiscountView(rows(("命运之眼", "5★神器召唤券", "觉醒的灵药", "随机Lv.85特化装备")), None)


class RecoveryTests(OfflineAssertions):
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
