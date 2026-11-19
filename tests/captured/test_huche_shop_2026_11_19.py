"""Registered captures for the November 19 Huche shop."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from module.config import server
from tasks.activity import calendar
from tests.support.history_fixtures import MANIFEST
from tests.support.offline import fixture_image


class CurrentScreenshotTests(unittest.TestCase):
    def setUp(self):
        language = patch.object(server, "lang", "global_cn")
        language.start()
        self.addCleanup(language.stop)

    def test_entry_and_same_price_offers_in_current_captures(self):
        from tasks.activity.assets.assets_activity_huche_shop import (
            MAIN_GOTO_HUCHE_SHOP,
        )
        from tasks.activity.assets.assets_activity_huche_shop_26_9_17 import (
            HUCHE_MYSTIC_BUY,
            OCR_HUCHE_STOCK,
        )
        from tasks.activity.huche_shop import HucheShop as CurrentShop

        shop = object.__new__(CurrentShop)
        shop.config = SimpleNamespace()
        window = next(window for window in calendar.load_calendar()
                      if window.event_id == calendar.CURRENT_HUCHE_SHOP_ID)
        shop._configure_offers(window)
        shop.device = SimpleNamespace(image=None, stuck_record_add=lambda button: None)
        for scene, expected in (
            ("lobby", []), ("rotating", [(160, 2, 2)]),
            ("regular", [(160, 4, 4)]), ("partial", []),
        ):
            with self.subTest(scene=scene):
                shop.device.image = fixture_image(MANIFEST, f"huche-20261119-{scene}")
                self.assertEqual(shop.match_template_color(MAIN_GOTO_HUCHE_SHOP), scene == "lobby")
                self.assertEqual(shop.match_template_color(shop.SHOP_CHECK), scene != "lobby")
                offers = shop.find_offers()
                self.assertEqual([(offer.price, offer.remaining, offer.limit) for offer in offers], expected)
                for offer in offers:
                    self.assertTrue(shop._match_at(HUCHE_MYSTIC_BUY, offer.buy_offset))
                    read_text = shop._ocr
                    wrong_limit = "4/4" if offer.limit == 2 else "2/2"
                    with patch.object(shop, "_ocr", side_effect=lambda asset, offset, wrong_limit=wrong_limit,
                                      read_text=read_text: (
                        wrong_limit if asset is OCR_HUCHE_STOCK else read_text(asset, offset)
                    )):
                        self.assertEqual(shop.find_offers(), [])

    def test_reordered_rotating_sellout_requires_the_label(self):
        from tasks.activity.assets.assets_activity_huche_shop_26_9_17 import (
            HUCHE_ITEM_SOLD_OUT,
        )
        from tasks.activity.huche_shop import HucheShop

        shop = object.__new__(HucheShop)
        shop.config = SimpleNamespace()
        window = next(window for window in calendar.load_calendar()
                      if window.event_id == calendar.CURRENT_HUCHE_SHOP_ID)
        shop._configure_offers(window)
        shop.device = SimpleNamespace(
            image=fixture_image(MANIFEST, "huche-20261119-rotating-sold-out"),
            stuck_record_add=lambda button: None,
        )
        self.assertTrue(shop.match_template_color(shop.SHOP_CHECK))
        self.assertEqual([(offer.key, offer.remaining, offer.sold_out) for offer in shop.find_offers()],
                         [((160, 2), 0, True)])
        match = shop._match_at
        with patch.object(shop, "_match_at", side_effect=lambda asset, offset, **kwargs: (
            asset is not HUCHE_ITEM_SOLD_OUT and match(asset, offset, **kwargs)
        )):
            self.assertEqual([(offer.key, offer.remaining, offer.sold_out) for offer in shop.find_offers()],
                             [((160, 2), 0, False)])

    def test_regular_list_overlap_is_verified_in_both_directions(self):
        from tasks.activity.huche_shop import HucheShop, RegularQuotaScan

        shop = object.__new__(HucheShop)
        shop.device = SimpleNamespace(
            image=fixture_image(MANIFEST, "huche-20261119-regular"),
            stuck_record_add=lambda button: None,
        )
        first = shop.read_stock_view()
        shop.device.image = fixture_image(MANIFEST, "huche-20261119-partial")
        later = shop.read_stock_view()
        self.assertAlmostEqual(RegularQuotaScan.displacement(first, later, True), -209, delta=2)
        self.assertAlmostEqual(RegularQuotaScan.displacement(later, first, False), 209, delta=2)
        self.assertEqual(RegularQuotaScan.displacement(later, later, True), 0)
        self.assertFalse(shop.has_regular_candidate())
        shop.device.image = fixture_image(MANIFEST, "huche-20261119-regular")
        self.assertTrue(shop.has_regular_candidate())
