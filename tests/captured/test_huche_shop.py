# ruff: noqa: E402
from module.config import server as _test_server
_test_server.set_lang("global_cn")
from tests.support.history_fixtures import input_root, read_input as load_image

"""Huche shop screenshot, purchase, refresh and page-routing regressions."""
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

WORKTREE = Path(__file__).resolve().parents[2]
import module.config.server as server
from tasks.activity import calendar
from tasks.activity.huche_shop import HucheShop, MysticOffer
from tasks.activity.assets.assets_activity_huche_shop_26_9_17 import (
    HUCHE_SHOP_CHECK, HUCHE_BUY_POPUP_CHECK, OCR_HUCHE_BUY_AMOUNT, HUCHE_MYSTIC_ITEM, HUCHE_PRICE_CURRENCY,
)
from tasks.store.assets.assets_store_actions import BUY_CONFIRM_MULTI, BUY_CONFIRM_SINGLE
from tasks.store.purchase import plan_purchase_selection

NOW = datetime.fromisoformat("2026-09-20T12:30:00+08:00")
EVENT_ID = "huche_shop_2026_09_17"
WINDOW = next(e for e in calendar.load_calendar() if e.event_id == EVENT_ID)
FIXTURES = input_root('huche_shop')
CHEAP = MysticOffer((634, 285, 733, 314), 160, 2, 2, False)
REGULAR = MysticOffer((634, 632, 733, 661), 200, 4, 4, False)
CHEAP_DONE = MysticOffer(CHEAP.row, 160, 0, 2, True)
REGULAR_DONE = MysticOffer(REGULAR.row, 200, 0, 4, True)


class Config:
    Emulator_PackageName = "com.stove.epic7.google"
    Emulator_GameLanguage = "auto"
    Scheduler_ServerUpdate = "02:00"
    SpecialActivity_BuyHucheMysticMedals = True
    SpecialActivity_GetFreeGacha = False
    SpecialActivity_GetE7wcBattleGateReward = False
    SpecialActivity_GetKoharuRaffleReward = False

    def __init__(self):
        self.values = {}
        self.delays = []

    def cross_get(self, key, default=None):
        return self.values.get(key, default)

    def cross_set(self, key, value):
        self.values[key] = value

    def task_delay(self, **kwargs):
        self.delays.append(kwargs)


class Flow(HucheShop):
    def __init__(self, frames, balance=2000):
        self.config = Config()
        self.activity_id = EVENT_ID
        self.price_limits = {160: 2, 200: 4}
        self.medals_per_item = 50
        self.regular_price = 200
        self.frames = frames
        self.frame = -1
        self.balance = balance
        self.clicks = []
        self.scans = []
        self.cancelled = 0
        self.device = SimpleNamespace(screenshot=self.screenshot, click=self.click,
                                      swipe=lambda *a, **kw:self.scans.append(a),
                                      drag=lambda *a, **kw:self.scans.append(a))
        self.device.screenshot()

    def screenshot(self):
        self.frame += 1
        if self.frame >= len(self.frames):
            raise AssertionError("No positive completion before fixture ended")

    @property
    def state(self):
        return self.frames[self.frame]

    def click(self, button):
        self.clicks.append((self.frame, button.name, button.button))

    def match_template_color(self, button, **kw):
        return button is HUCHE_SHOP_CHECK and self.state in (
            "available", "cheap_done", "done", "partial", "empty", "short_funds", "regular_short", "cheap_only_done")

    def _locate(self, button, area):
        return (0, 0) if button is HUCHE_BUY_POPUP_CHECK and self.state.startswith("popup") else None

    def _match_at(self, *a, **kw):
        return True

    def interval_is_reached(self, *a, **kw):
        return True

    def interval_reset(self, *a, **kw):
        pass

    def find_offers(self):
        return {
            "available": [CHEAP], "cheap_done": [CHEAP_DONE, REGULAR],
            "done": [CHEAP_DONE, REGULAR_DONE],
            "partial": [MysticOffer(CHEAP.row,160,0,2,False), REGULAR_DONE],
            "short_funds": [CHEAP, REGULAR_DONE], "empty": [],
            "regular_short": [CHEAP_DONE, REGULAR], "cheap_only_done": [CHEAP_DONE],
        }.get(self.state, [])

    def read_balance(self):
        return self.balance

    def popup_selection(self, offer, balance):
        if self.state == "popup_wrong":
            return None
        selected = 1 if self.state == "popup_min" else offer.remaining
        return plan_purchase_selection("target", (selected,offer.remaining-selected,offer.remaining),
                                       min(offer.remaining,balance//offer.price)), BUY_CONFIRM_MULTI

    def appear_then_click(self, button, **kw):
        self.clicks.append((self.frame, button.name, None))
        return True

    def handle_purchase_cancel(self):
        self.cancelled += 1
        return True

    def handle_touch_to_close(self, **kw):
        return self.state == "reward"

    def handle_network_error(self):
        return self.state == "network"


class ScreenshotTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")
        self.shop = object.__new__(HucheShop)
        self.shop.config = Config()
        self.shop.price_limits = {160:2,200:4}
        self.shop.medals_per_item = 50
        self.shop.device = SimpleNamespace(image=None, stuck_record_add=lambda b:None)

    def image(self, case):
        self.shop.device.image = load_image(FIXTURES / (case + ".png"))

    def test_available_and_reordered_sold_out_rows(self):
        for case, expected in (
            ("available", [(160,2,2,False)]),
            ("sold_out", [(160,0,2,True)]),
            ("sold_out_bottom", [(160,0,2,True),(200,0,4,True)]),
            ("main", []), ("confirm", []),
        ):
            with self.subTest(case=case):
                self.image(case)
                offers = self.shop.find_offers()
                self.assertEqual([(x.price,x.remaining,x.limit,x.sold_out) for x in offers], expected)

    def test_resource_bar_before_and_after_purchase(self):
        self.image("available")
        self.assertEqual(self.shop.read_balance(),491)
        self.image("sold_out")
        self.assertEqual(self.shop.read_balance(),171)

    def test_actual_popup_selects_max_but_does_not_confirm_yet(self):
        self.image("confirm")
        selected, confirm = self.shop.popup_selection(CHEAP,491)
        self.assertEqual((selected.quantity,selected.action),(2,"max"))
        self.assertIs(confirm,BUY_CONFIRM_MULTI)
        selected, _ = self.shop.popup_selection(CHEAP,171)
        self.assertEqual((selected.quantity,selected.action),(1,"none"))
        self.assertIsNone(self.shop.popup_selection(CHEAP,159))

    def test_wrong_price_amount_currency_and_unreadable_counter_never_confirm(self):
        self.image("confirm")
        self.assertIsNone(self.shop.popup_selection(REGULAR,2000))
        self.shop.medals_per_item = 100
        self.assertIsNone(self.shop.popup_selection(CHEAP,2000))
        self.shop.medals_per_item = 50
        with patch.object(self.shop,"_match_at",return_value=False):
            self.assertIsNone(self.shop.popup_selection(CHEAP,2000))
        with patch("tasks.activity.huche_shop.ocr_purchase_counter",return_value=(0,0,0)):
            self.assertIsNone(self.shop.popup_selection(CHEAP,2000))
        with patch.object(self.shop,"_locate",return_value=None):
            self.assertIsNone(self.shop.popup_selection(CHEAP,2000))

    def test_single_layout_needs_one_remaining_and_verified_total(self):
        offer = MysticOffer(CHEAP.row,160,1,2,False)
        with patch.object(self.shop,"_locate",return_value=(0,0)), patch.object(
            self.shop,"_match_at",return_value=True
        ), patch.object(self.shop,"match_template_color",side_effect=lambda b:b is BUY_CONFIRM_SINGLE), patch.object(
            self.shop,"_number",side_effect=lambda b, offset=(0,0):50 if b is OCR_HUCHE_BUY_AMOUNT else 160
        ):
            selection, confirm = self.shop.popup_selection(offer,171)
            self.assertEqual(selection.quantity,1)
            self.assertIs(confirm,BUY_CONFIRM_SINGLE)
            self.assertIsNone(self.shop.popup_selection(CHEAP,491))

    def test_asset_search_mutations_are_restored(self):
        self.image("sold_out_bottom")
        before = [b.search for b in HUCHE_MYSTIC_ITEM.iter_buttons()]
        self.shop.find_offers()
        self.assertEqual([b.search for b in HUCHE_MYSTIC_ITEM.iter_buttons()],before)
        before = [b.search for b in HUCHE_PRICE_CURRENCY.iter_buttons()]
        with patch.object(self.shop,"match_template_luma",side_effect=RuntimeError("test")):
            with self.assertRaises(RuntimeError):
                self.shop._match_at(HUCHE_PRICE_CURRENCY,(0,50),color=False)
        self.assertEqual([b.search for b in HUCHE_PRICE_CURRENCY.iter_buttons()],before)
