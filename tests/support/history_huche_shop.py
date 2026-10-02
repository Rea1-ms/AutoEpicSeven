# ruff: noqa: E402
from module.config import server as _test_server

_test_server.set_lang("global_cn")


"""Huche shop screenshot, purchase, refresh and page-routing regressions."""
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

WORKTREE = Path(__file__).resolve().parents[2]
PROJECT_ROOT = WORKTREE
from tasks.activity import calendar
from tasks.activity.huche_shop import HucheShop, MysticOffer
from tasks.activity.assets.assets_activity_huche_shop_26_9_17 import (
    HUCHE_SHOP_CHECK, HUCHE_BUY_POPUP_CHECK,
)
from tasks.store.assets.assets_store_actions import BUY_CONFIRM_MULTI
from tasks.store.purchase import plan_purchase_selection

NOW = datetime.fromisoformat("2026-09-20T12:30:00+08:00")
EVENT_ID = "huche_shop_2026_09_17"
WINDOW = next(e for e in calendar.load_calendar() if e.event_id == EVENT_ID)
FIXTURES = Path(__file__).parent / "screenshots/huche_shop"
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

__all__ = ['Config', 'EVENT_ID', 'FIXTURES', 'Flow', 'NOW', 'WINDOW']
