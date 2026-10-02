# ruff: noqa: E402
from module.config import server as _test_server

_test_server.set_lang("global_cn")

"""September 25 log replay: edge-row OCR changes must not trigger backtracking."""
import unittest
from dataclasses import replace

import tests.support.history_huche_drag_recovery as recovery
from tests.support.history_huche_discount_batch import stable
from tasks.activity.huche_shop import DiscountBatchScan, DiscountItem, DiscountView, HucheShop


def view(names, first=78, boundary=None):
    return DiscountView(tuple(DiscountItem(first + 145 * i, name, False) for i, name in enumerate(names)), boundary)


TOP = view(("生命之叶", "金光传承石", "圣约书签", "装备根源选择箱"), 85)
PAGE1 = view(("金光传承石", "圣约书签", "装备根源选择箱", "智慧之眼"))
BEFORE = view(("圣约书签", "装备根源选择箱", "智慧之眼", "5★神器召唤"))
AFTER = view(("装备根源选择箱", "智慧之眼", "5★神器召唤券", "飞翔的灵药"))
NEXT = view(("智慧之眼", "5★神器召唤券", "飞翔的灵药", "随机Lv.85特化装备"))
FINAL = view(("5★神器召唤券", "飞翔的灵药", "随机Lv.85特化装备"), 127, (634, 640, 713, 663))


def before_drag():
    scan = DiscountBatchScan()
    for current in (TOP, PAGE1, BEFORE):
        assert stable(scan, current) == "down"
        scan.scrolled(current, "down")
    return scan


class OverlapTests(unittest.TestCase):
    def test_reported_name_change_uses_two_exact_neighbors(self):
        scan = before_drag()
        self.assertEqual(stable(scan, AFTER), "down")
        self.assertIsNone(scan.tail)
        self.assertEqual(scan.anchors, ())
        self.assertEqual(scan.recoveries, 0)

    def test_complete_reported_scan_advances_without_any_backward_drag(self):
        frames = [frame for current in (TOP, PAGE1, BEFORE, AFTER, NEXT, FINAL) for frame in (current, current)]
        flow, scan = recovery.RecoveryTests().run_views(frames, True)
        self.assertEqual(flow.scans, [((850, 620), (850, 475))] * 5)
        self.assertEqual(scan.recoveries, 0)

    def test_one_shared_neighbor_is_not_enough_for_changed_tail(self):
        current = replace(AFTER, items=AFTER.items[1:])
        self.assertEqual(stable(before_drag(), current), "recover")

    def test_similar_names_without_exact_neighbors_cannot_establish_overlap(self):
        current = view(("装备根源选择", "智慧", "5★神器召唤券", "飞翔的灵药"))
        self.assertEqual(stable(before_drag(), current), "recover")

    def test_reordered_neighbor_pair_cannot_establish_overlap(self):
        current = view(("智慧之眼", "装备根源选择箱", "5★神器召唤券", "飞翔的灵药"))
        self.assertEqual(stable(before_drag(), current), "recover")

    def test_nonadjacent_matches_cannot_establish_overlap(self):
        current = view(("装备根源选择箱", "另一件商品", "智慧之眼", "5★神器召唤券"))
        self.assertEqual(stable(before_drag(), current), "recover")

    def test_inconsistent_neighbor_displacements_cannot_establish_overlap(self):
        current = replace(AFTER, items=(AFTER.items[0], replace(AFTER.items[1], y=AFTER.items[1].y + 15)) + AFTER.items[2:])
        self.assertEqual(stable(before_drag(), current), "recover")

    def test_mystic_classification_difference_breaks_neighbor_identity(self):
        current = replace(AFTER, items=(replace(AFTER.items[0], mystic=True),) + AFTER.items[1:])
        self.assertEqual(stable(before_drag(), current), "recover")

    def test_same_position_with_changed_tail_and_boundary_does_not_complete(self):
        current = replace(BEFORE, items=BEFORE.items[:-1] + (replace(BEFORE.items[-1], name="5★神器召唤券"),), boundary=(634, 740, 713, 763))
        scan = before_drag()
        self.assertEqual(stable(scan, current), "down")
        self.assertIsNotNone(scan.tail)
        self.assertEqual(scan.anchors, BEFORE.items)

    def test_conflicting_repeated_pairs_are_not_accepted(self):
        current = view(("装备根源选择箱", "智慧之眼", "装备根源选择箱", "智慧之眼"))
        self.assertEqual(stable(before_drag(), current), "recover")

    def test_neighbor_proof_requires_two_stable_frames(self):
        scan = before_drag()
        self.assertEqual(scan.observe(AFTER), "wait")
        self.assertIsNotNone(scan.tail)
        self.assertEqual(scan.observe(AFTER), "down")
        self.assertIsNone(scan.tail)

    def test_recovery_keeps_original_neighbors_until_coverage_is_proved(self):
        scan = before_drag()
        missing = view(("其它商品一", "其它商品二", "其它商品三"))
        self.assertEqual(stable(scan, missing), "recover")
        scan.scrolled(missing, "recover")
        self.assertEqual(scan.anchors, BEFORE.items)
        self.assertEqual(scan.recoveries, 1)
        self.assertEqual(stable(scan, AFTER), "down")
        self.assertEqual(scan.recoveries, 0)

    def test_possible_medals_after_neighbor_proof_never_complete_or_buy_unverified(self):
        target = replace(AFTER, items=AFTER.items[:-1] + (replace(AFTER.items[-1], name="神秘奖牌", mystic=True),))
        frames = [frame for current in (TOP, PAGE1, BEFORE) for frame in (current, current)]
        frames += [target] * (HucheShop.BATCH_UNVERIFIED_LIMIT + 1)
        flow, _ = recovery.RecoveryTests().run_views(frames, False)
        self.assertEqual(flow.scans, [((850, 620), (850, 475))] * 3)
