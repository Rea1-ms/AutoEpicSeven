# ruff: noqa: E402
from module.config import server as _test_server
_test_server.set_lang("global_cn")
from tests.support.history_fixtures import input_root, read_input as load_image

"""Rotating Huche batches: screenshot boundaries and continuous scan coverage."""
import unittest
from unittest.mock import patch

from tests.support.history_huche_shop import Config
FIXTURES = input_root("huche_shop")
import module.config.server as server
from tasks.activity.huche_shop import HucheShop, DiscountItem, DiscountView, DiscountBatchScan
from tasks.activity.assets.assets_activity_huche_shop_26_9_17 import HUCHE_REFRESH_ITEM
from types import SimpleNamespace

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


class ScreenshotTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")
        self.shop = object.__new__(HucheShop)
        self.shop.config = Config()
        self.shop.device = SimpleNamespace(image=None, stuck_record_add=lambda button: None)

    def read(self, name):
        self.shop.device.image = load_image(FIXTURES / (name + ".png"))
        return self.shop.read_discount_view()

    def test_reported_leif_screenshot_advances_past_low_confidence_name(self):
        view = self.read("leif_batch")
        self.assertEqual([item.name for item in view.items[:2]], ["生命之叶", "银河书签"])
        self.assertEqual(view.items[-1].name, "智慧之眼")
        self.assertFalse(any(item.mystic for item in view.items))
        self.shop.price_limits = {160: 2, 200: 4}
        self.assertEqual(self.shop.find_offers(), [])
        scan = DiscountBatchScan()
        self.assertEqual(stable(scan, view), "down")

    def test_unreadable_non_target_name_still_blocks_absence(self):
        results = [SimpleNamespace(ocr_text="生命之叶", score=0.4)]
        with patch("tasks.activity.huche_shop.Ocr.detect_and_ocr", return_value=results), patch.object(
            self.shop, "_match_at", return_value=False
        ):
            self.assertIsNone(self.read("leif_batch"))

    def test_possible_mystic_name_is_retained_even_with_low_confidence(self):
        results = [SimpleNamespace(ocr_text="神秘奖牌", score=0.4)]
        with patch("tasks.activity.huche_shop.Ocr.detect_and_ocr", return_value=results), patch.object(
            self.shop, "_match_at", return_value=False
        ):
            view = self.read("leif_batch")
        self.assertTrue(all(item.mystic for item in view.items))
        scan = DiscountBatchScan()
        self.assertEqual(stable(scan, view), "target")

    def test_clock_rows_include_wrapped_names_and_mystic_medals(self):
        view = self.read("available")
        self.assertIsNone(view.boundary)
        self.assertEqual([item.y for item in view.items], [85, 230, 375, 520])
        self.assertEqual([item.mystic for item in view.items], [False, True, False, False])

    def test_boundary_includes_catalogue_row_without_buy_button(self):
        view = self.read("discount_boundary")
        self.assertIsNotNone(view.boundary)
        self.assertEqual(len(view.items), 3)
        self.assertIn("特化饰品", view.items[-1].name)
        self.assertFalse(any(item.mystic for item in view.items))

    def test_regular_boundary_excludes_reordered_sold_out_medals(self):
        view = self.read("sold_out_bottom")
        self.assertEqual(view.items, ())
        self.assertIsNotNone(view.boundary)

    def test_unreadable_names_and_unrelated_pages_are_not_empty_batches(self):
        for name in ("main", "confirm"):
            self.assertIsNone(self.read(name))
        with patch("tasks.activity.huche_shop.Ocr.detect_and_ocr", return_value=[]):
            self.assertIsNone(self.read("available"))

    def test_missing_clock_before_boundary_is_not_a_complete_batch(self):
        self.shop.device.image = load_image(FIXTURES / "discount_boundary.png")
        original = HUCHE_REFRESH_ITEM.match_multi_template
        with patch.object(HUCHE_REFRESH_ITEM, "match_multi_template", side_effect=lambda image: original(image)[:-1]):
            self.assertIsNone(self.shop.read_discount_view())

    def test_clock_searches_restored_after_scan(self):
        original = [button.search for button in HUCHE_REFRESH_ITEM.iter_buttons()]
        self.read("discount_boundary")
        self.assertEqual([button.search for button in HUCHE_REFRESH_ITEM.iter_buttons()], original)
