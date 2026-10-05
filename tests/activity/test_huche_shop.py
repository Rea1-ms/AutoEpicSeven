# ruff: noqa: E402
from module.config import server as _test_server

_test_server.set_lang("global_cn")

"""Huche shop screenshot, purchase, refresh and page-routing regressions."""
import json
import subprocess
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

WORKTREE = Path(__file__).resolve().parents[2]
PROJECT_ROOT = WORKTREE
import module.config.server as server
from module.game_info.catalog import parse_info
from tasks.activity import calendar, scheduling
from tasks.activity.entry import SpecialActivityEntry
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




class FlowTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")
        clock = patch("tasks.activity.huche_shop.aware_time", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)

    def test_both_offers_are_bought_and_checked_only_after_stock_changes(self):
        flow = Flow(["available"]*2 + ["popup_min","popup_max","available","available","reward"]
                    + ["cheap_done"]*2 + ["popup_min","popup_max"] + ["done"]*2)
        self.assertTrue(flow.run_purchase(WINDOW))
        self.assertEqual([name for _,name,_ in flow.clicks],
                         ["HucheMysticBuy","BUY_MAX","BUY_CONFIRM_MULTI"]*2)
        self.assertEqual(len(flow.config.values),2)
        self.assertEqual(flow.clicks[0][2],(1170,300,1216,326))
        self.assertGreater(flow.clicks[3][2][1],600)

    def test_already_sold_out_never_clicks(self):
        flow = Flow(["done"]*2)
        self.assertTrue(flow.run_purchase(WINDOW))
        self.assertEqual(flow.clicks,[])

    def test_insufficient_balance_checks_period_without_spending(self):
        flow = Flow(["short_funds"]*3,balance=159)
        self.assertTrue(flow.run_purchase(WINDOW))
        self.assertEqual(flow.clicks,[])
        self.assertEqual(len(flow.config.values),2)

    def test_wrong_popup_is_cancelled_without_completion(self):
        flow = Flow(["available"]*2 + ["popup_wrong"]*6 + ["available"])
        self.assertFalse(flow.run_purchase(WINDOW))
        self.assertEqual([name for _,name,_ in flow.clicks],["HucheMysticBuy"])
        self.assertEqual(flow.cancelled,1)
        self.assertEqual(flow.config.values,{})

    def test_counter_zero_without_sold_out_does_not_complete(self):
        flow = Flow(["partial"]*3)
        with patch("tasks.activity.huche_shop.Timer") as timer:
            timer.return_value.start.return_value.reached.side_effect=[False,False,True]
            self.assertFalse(flow.run_purchase(WINDOW))
        self.assertNotIn(scheduling._checked_path(flow.config, EVENT_ID), flow.config.values)
        self.assertIn(scheduling._checked_path(flow.config, EVENT_ID + "_regular_mystic"), flow.config.values)

    def test_missing_items_have_bounded_search_without_record(self):
        flow = Flow(["empty"]*4)
        flow.SCROLL_UP_LIMIT=1
        flow.SCROLL_DOWN_LIMIT=2
        self.assertFalse(flow.run_purchase(WINDOW))
        self.assertEqual(len(flow.scans),3)
        self.assertLess(flow.scans[0][0][1],flow.scans[0][1][1])
        self.assertGreater(flow.scans[1][0][1],flow.scans[1][1][1])
        self.assertEqual(flow.config.values,{})

    def test_refresh_boundary_discards_old_sold_out_observation(self):
        flow = Flow(["done"]*2)
        with patch("tasks.activity.huche_shop.aware_time",side_effect=[NOW,NOW,NOW,NOW+timedelta(hours=12)]):
            self.assertFalse(flow.run_purchase(WINDOW))
        self.assertEqual(flow.config.values,{})


    def test_regular_quota_record_survives_later_refresh_without_rescanning_bottom(self):
        flow = Flow(["cheap_only_done"]*2)
        key = scheduling._checked_path(flow.config, EVENT_ID + "_regular_mystic")
        flow.config.values[key] = "2026-09-18 12:00:00"
        self.assertTrue(flow.run_purchase(WINDOW))
        self.assertEqual(flow.scans, [])
        self.assertEqual(flow.clicks, [])
        self.assertEqual(flow.config.values[key], "2026-09-18 12:00:00")

    def test_insufficient_regular_balance_does_not_mark_fixed_quota_sold_out(self):
        flow = Flow(["regular_short"]*3, balance=199)
        self.assertTrue(flow.run_purchase(WINDOW))
        key = scheduling._checked_path(flow.config, EVENT_ID + "_regular_mystic")
        self.assertNotIn(key, flow.config.values)
        self.assertEqual(flow.clicks, [])
        self.assertEqual(len(flow.config.values), 1)

    def test_old_fixed_quota_record_cannot_skip_a_new_campaign(self):
        config = Config()
        event_id = EVENT_ID + "_regular_mystic"
        config.values[scheduling._checked_path(config, event_id)] = "2026-09-18 12:00:00"
        self.assertTrue(scheduling.is_activity_checked_since(config, event_id, WINDOW.start, NOW))
        self.assertFalse(scheduling.is_activity_checked_since(config, event_id, NOW, NOW))
        self.assertFalse(scheduling.is_activity_checked_since(config, "next_regular_mystic", WINDOW.start, NOW))


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")
        self.config=Config()

    def test_dates_and_exact_refresh_boundaries(self):
        self.assertEqual(WINDOW.start.isoformat(),"2026-09-17T11:00:00+08:00")
        self.assertEqual(WINDOW.end.isoformat(),"2026-10-29T11:00:00+08:00")
        for clock, start, end in (("10:59:59","2026-09-19T23:00","2026-09-20T11:00"),
                                  ("11:00:00","2026-09-20T11:00","2026-09-20T23:00"),
                                  ("23:00:00","2026-09-20T23:00","2026-09-21T11:00")):
            at=datetime.fromisoformat("2026-09-20T"+clock+"+08:00")
            self.assertEqual(WINDOW.refresh_start(at).strftime("%Y-%m-%dT%H:%M"),start)
            self.assertEqual(WINDOW.next_refresh(at).strftime("%Y-%m-%dT%H:%M"),end)
        self.assertIsNone(WINDOW.next_refresh(WINDOW.end-timedelta(seconds=1)))
        self.assertFalse(WINDOW.contains(WINDOW.end))

    def test_record_never_skips_next_half_day_or_another_server(self):
        key=scheduling._checked_path(self.config,EVENT_ID)
        # Receipts are persisted in scheduler-local time. Keep this observation
        # at 12:00 Beijing time even when the host's local timezone is UTC.
        self.config.values[key]=NOW.replace(hour=12,minute=0).astimezone().replace(tzinfo=None).isoformat(sep=" ")
        self.assertTrue(scheduling.is_activity_checked_in_window(self.config,WINDOW,NOW))
        self.assertFalse(scheduling.is_activity_checked_in_window(self.config,WINDOW,NOW.replace(hour=23)))
        self.config.Emulator_PackageName="com.zlongame.cn.epicseven"
        self.assertFalse(scheduling.is_activity_checked_in_window(self.config,WINDOW,NOW))

    def test_next_run_includes_refresh_only_when_enabled(self):
        for enabled in (True,False):
            self.config.SpecialActivity_BuyHucheMysticMedals=enabled
            self.config.delays=[]
            with patch.object(scheduling,"aware_time",return_value=NOW), patch.object(
                scheduling,"active_activities",return_value=(WINDOW,)
            ), patch.object(scheduling,"next_activity_start",return_value=None):
                scheduling.delay_next_activity_check(self.config)
            expected={"server_update":True,"task":"SpecialActivity"}
            if enabled:
                expected["target"]=NOW.replace(hour=23,minute=0,second=0).astimezone().replace(tzinfo=None)
            self.assertEqual(self.config.delays,[expected])

    def test_bad_refresh_hours_rejected(self):
        for value in (0,-1,True,"12"):
            data={"schema_version":1,"events":[{"id":"test","kind":"huche_shop","name":"Shop","source":"fixture",
                  "oversea_start":WINDOW.start.isoformat(),"oversea_end":WINDOW.end.isoformat(),"values":{"refresh_hours":value}}]}
            with self.assertRaises(ValueError):
                parse_info(json.dumps(data))

    def test_entry_dispatches_shop_and_skips_disabled_option(self):
        with patch("tasks.activity.entry.active_activities",return_value=(WINDOW,)), patch(
            "tasks.activity.huche_shop.HucheShop"
        ) as task, patch("tasks.activity.entry.delay_next_activity_check"):
            task.return_value.run.return_value=True
            self.assertTrue(SpecialActivityEntry(self.config).run())
            task.assert_called_once()
            task.return_value.ui_goto.assert_called_once()
            task.reset_mock()
            self.config.SpecialActivity_BuyHucheMysticMedals=False
            self.assertTrue(SpecialActivityEntry(self.config).run())
            task.assert_not_called()

    def test_config_defaults_off_and_keeps_explicit_preference(self):
        from module.config.config_updater import ConfigUpdater
        updater=ConfigUpdater()
        self.assertFalse(updater.config_update({})['SpecialActivity']['SpecialActivity']['BuyHucheMysticMedals'])
        old={'SpecialActivity':{'SpecialActivity':{'BuyHucheMysticMedals':True}}}
        self.assertTrue(updater.config_update(old)['SpecialActivity']['SpecialActivity']['BuyHucheMysticMedals'])
        for path in Path('module/config/i18n').glob('*.json'):
            entry=json.loads(path.read_text(encoding='utf-8'))['SpecialActivity']['BuyHucheMysticMedals']
            self.assertTrue(entry['name'])
            self.assertTrue(entry['help'])

    def test_page_registration_and_routes_per_server(self):
        code="""
import sys
import module.config.server as server
server.set_lang(sys.argv[1])
server.server='CN-Official' if sys.argv[1]=='cn' else 'OVERSEA-Play'
from tasks.base import page
from tasks.activity.huche_shop import HucheShop
assert hasattr(page,'page_huche_shop') == (sys.argv[1]=='global_cn')
list(page.Page.iter_check_buttons())
if sys.argv[1]=='global_cn':
    page.Page.init_connection(page.page_huche_shop)
    assert page.page_main.parent is page.page_huche_shop
    page.Page.init_connection(page.page_main)
    assert page.page_huche_shop.parent is page.page_main
    assert page.MAIN_GOTO_HUCHE_SHOP in page.page_main.links_need_match or page.page_huche_shop in page.page_main.links_need_match
print('verified',sys.argv[1])
"""
        for lang in ("cn","global_cn","global_en"):
            result=subprocess.run([sys.executable,"-B","-c",code,lang],cwd=WORKTREE,capture_output=True,text=True,encoding='utf-8',errors='replace')
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
