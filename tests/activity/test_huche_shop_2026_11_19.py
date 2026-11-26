"""Purchase, refresh and routing regressions for the November 19 Huche shop."""

import json
import subprocess
import sys
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from module.config import server
from tasks.activity import calendar, scheduling
from tests.support.offline import ControlledClock, record_action

WORKTREE = Path(__file__).resolve().parents[2]


class Config:
    Emulator_PackageName = "OVERSEA-Play"
    SpecialActivity_BuyHucheMysticMedals = True

    def __init__(self):
        self.values = {}

    def cross_get(self, key, default=None):
        return self.values.get(key, default)

    def cross_set(self, key, value):
        self.values[key] = value


def current_shop_window():
    from tasks.activity.legacy.huche_shop_2026_11_19.huche_shop import archived_window

    return archived_window()


def stock_viewports():
    strip = np.random.default_rng(19).integers(0, 256, (900, 310), dtype=np.uint8)
    return strip[:624], strip[145:769], strip[276:900]


class FlowTests(unittest.TestCase):
    def setUp(self):
        language = patch.object(server, "lang", "global_cn")
        language.start()
        self.addCleanup(language.stop)

    def replay_current_shop(self, offers_by_frame, *, views=None, stock_views=None,
                            regular_done_at=None, at=None, success=True, unverified_regular=False, clipped_regular=False):
        from tasks.activity.legacy.huche_shop_2026_11_19.huche_shop import HucheShop
        from tasks.store.assets.assets_store_actions import BUY_CONFIRM_MULTI

        window = current_shop_window()
        shop = HucheShop(Config(), device=SimpleNamespace(), activity_id=window.event_id)
        shop._configure_offers(window)
        if regular_done_at is not None:
            key = scheduling._checked_path(shop.config, f"{window.event_id}_regular_mystic")
            shop.config.cross_set(key, regular_done_at)
        frames = iter(enumerate(offers_by_frame))
        frame, offers = next(frames)
        actions = []
        clock = ControlledClock()
        default_view, _, _ = stock_viewports()

        def screenshot():
            nonlocal frame, offers
            frame, offers = next(frames)
            clock.advance(0.75)

        def click(button, interval=0):
            if interval and not shop.interval_is_reached(button, interval=interval):
                return False
            if interval:
                shop.interval_reset(button, interval=interval)
            actions.append(record_action((frame, button.name)))
            return True

        def drag(start, end):
            actions.append(record_action((frame, "drag", start, end)))

        def read_view():
            if views is None:
                self.fail("Offer search should not rescan the rotating batch")
            return views[frame]

        shop.device.screenshot = screenshot
        shop.device.click = click
        shop.device.drag = drag
        shop.device.swipe = lambda *args, **kwargs: self.fail("Shop scrolling must use drag")
        with clock, patch.multiple(
            shop, find_offers=lambda: offers, match_template_color=lambda asset: offers is not None,
            _locate=lambda asset, area: (0, 0) if offers is None else None,
            _match_at=lambda *args: True, read_balance=lambda: 2000, read_discount_view=read_view,
            read_stock_view=lambda: stock_views[frame] if stock_views is not None else default_view,
            has_regular_candidate=lambda include_clipped=False: (
                unverified_regular or include_clipped and clipped_regular
            ),
            popup_selection=lambda offer, balance: (SimpleNamespace(action="none"), BUY_CONFIRM_MULTI),
            appear_then_click=click, handle_network_error=lambda: False,
            handle_touch_to_close=lambda **kwargs: False,
        ), patch("tasks.activity.legacy.huche_shop_2026_11_19.huche_shop.aware_time", return_value=at or window.start):
            self.assertEqual(shop.run_purchase(window), success)
        self.assertEqual(frame, len(offers_by_frame) - 1)
        return actions, shop.config

    def test_same_price_stock_is_tracked_per_offer(self):
        from tasks.activity.legacy.huche_shop_2026_11_19.huche_shop import (
            MysticOffer as CurrentOffer,
        )

        window = current_shop_window()
        rotating = CurrentOffer((634, 285, 733, 314), 160, 2, 2, False)
        regular = CurrentOffer((634, 632, 733, 661), 160, 4, 4, False)
        rotating_done = replace(rotating, remaining=0, sold_out=True)
        regular_done = replace(regular, remaining=0, sold_out=True)
        actions, config = self.replay_current_shop(
            [(rotating,)] * 2 + [None] + [(rotating, replace(regular, remaining=1))] * 2
            + [(rotating_done,)] * 2 + [()] + [(regular,)] * 2 + [None] + [(regular_done,)] * 2
        )
        self.assertEqual(actions, [
            (1, "HucheMysticBuy"), (2, "BUY_CONFIRM_MULTI"), (7, "drag", (850, 620), (850, 475)),
            (9, "HucheMysticBuy"), (10, "BUY_CONFIRM_MULTI"),
        ])
        self.assertEqual(set(config.values), {
            scheduling._checked_path(config, window.event_id),
            scheduling._checked_path(config, f"{window.event_id}_regular_mystic"),
        })

    def test_current_scan_reverses_only_to_recover_missing_overlap(self):
        from tasks.activity.legacy.huche_shop_2026_11_19.huche_shop import (
            DiscountItem,
            DiscountView,
        )

        def view(*names, boundary=None):
            return DiscountView(tuple(DiscountItem(85 + 145 * index, name, False)
                                      for index, name in enumerate(names)), boundary)

        top = view("史诗书签", "圣约书签", "银河书签", "属性召唤箱")
        lost = view(boundary=(634, 238, 713, 261))
        recovered = view("圣约书签", "银河书签", "属性召唤箱", "生命之叶")
        end = view("银河书签", "属性召唤箱", "生命之叶", boundary=(634, 603, 713, 626))
        views = [top] * 2 + [lost] * 3 + [recovered] * 3 + [end] * 2
        window = current_shop_window()
        actions, config = self.replay_current_shop([()] * len(views), views=views, regular_done_at=window.start)
        self.assertEqual(actions, [
            (1, "drag", (850, 620), (850, 475)), (4, "drag", (850, 180), (850, 325)),
            (7, "drag", (850, 620), (850, 475)),
        ])
        self.assertIn(scheduling._checked_path(config, window.event_id), config.values)
        self.assertEqual(config.cross_get(scheduling._checked_path(config, f"{window.event_id}_regular_mystic")),
                         window.start)

    def test_empty_rotating_batch_still_buys_and_records_disappearing_regular_offer(self):
        from tasks.activity.legacy.huche_shop_2026_11_19.huche_shop import (
            DiscountView,
            MysticOffer,
        )

        window = current_shop_window()
        boundary = DiscountView((), (634, 156, 713, 179))
        regular = MysticOffer((634, 261, 733, 290), 160, 4, 4, False)
        top, bottom, _ = stock_viewports()
        offers = [(), (), (), (regular,), (regular,)] + [None] * 4 + [(regular,)] * 2 + [()] * 19
        stock = [top] * 3 + [bottom] * 10 + [top] * 9 + [bottom] * 8
        actions, config = self.replay_current_shop(
            offers, views=[boundary] * 2, stock_views=stock,
        )
        self.assertEqual(actions, [
            (2, "drag", (850, 620), (850, 475)), (4, "HucheMysticBuy"), (5, "BUY_CONFIRM_MULTI"),
            (12, "drag", (850, 180), (850, 325)), (15, "drag", (850, 180), (850, 325)),
            (18, "drag", (850, 180), (850, 325)), (21, "drag", (850, 620), (850, 475)),
            (24, "drag", (850, 620), (850, 475)), (27, "drag", (850, 620), (850, 475)),
        ])
        self.assertEqual(set(config.values), {
            scheduling._checked_path(config, window.event_id),
            scheduling._checked_path(config, f"{window.event_id}_regular_mystic"),
        })

    def test_full_scan_records_a_regular_offer_already_absent_on_entry(self):
        from tasks.activity.legacy.huche_shop_2026_11_19.huche_shop import DiscountView

        window = current_shop_window()
        boundary = DiscountView((), (634, 156, 713, 179))
        top, middle, bottom = stock_viewports()
        stock = [top] * 3 + [middle] * 3 + [bottom] * 8
        actions, config = self.replay_current_shop([()] * len(stock), views=[boundary] * 2, stock_views=stock)
        self.assertEqual(actions, [(frame, "drag", (850, 620), (850, 475)) for frame in (2, 5, 8, 11)])
        self.assertEqual(set(config.values), {
            scheduling._checked_path(config, window.event_id),
            scheduling._checked_path(config, f"{window.event_id}_regular_mystic"),
        })

    def test_incomplete_or_unreadable_scan_never_records_absence(self):
        from tasks.activity.legacy.huche_shop_2026_11_19.huche_shop import DiscountView

        boundary = DiscountView((), (634, 156, 713, 179))
        top, bottom, _ = stock_viewports()
        unrelated = np.random.default_rng(29).integers(0, 256, top.shape, dtype=np.uint8)
        for stock, candidate, clipped, drags in (
            ([top] * 8, False, False, (2, 5)),
            ([top] * 3 + [unrelated] * 2, False, False, (2,)),
            ([top] * 7, True, False, ()),
            ([top] * 3 + [bottom] * 8, False, True, (2, 5, 8)),
        ):
            with self.subTest(candidate=candidate, clipped=clipped, drags=drags):
                actions, config = self.replay_current_shop(
                    [()] * len(stock), views=[boundary] * 2, stock_views=stock,
                    success=False, unverified_regular=candidate, clipped_regular=clipped,
                )
                self.assertEqual(actions, [(frame, "drag", (850, 620), (850, 475)) for frame in drags])
                self.assertEqual(config.values, {})

    def test_saved_regular_quota_stops_at_boundary_or_rotating_sellout(self):
        from tasks.activity.legacy.huche_shop_2026_11_19.huche_shop import (
            DiscountView,
            MysticOffer,
        )

        window = current_shop_window()
        at = window.next_refresh(window.start)
        rotating_done = MysticOffer((634, 632, 733, 661), 160, 0, 2, True)
        boundary = DiscountView((), (634, 156, 713, 179))
        for offers, views in (((), [boundary] * 2), ((rotating_done,), None)):
            with self.subTest(offers=offers):
                actions, config = self.replay_current_shop(
                    [offers] * 2, views=views, regular_done_at=window.start, at=at,
                )
                self.assertEqual(actions, [])
                self.assertIn(scheduling._checked_path(config, window.event_id), config.values)
                self.assertEqual(config.cross_get(scheduling._checked_path(
                    config, f"{window.event_id}_regular_mystic",
                )), window.start)


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        language = patch.object(server, "lang", "global_cn")
        language.start()
        self.addCleanup(language.stop)
        from tasks.activity.entry import SpecialActivityEntry

        self.entry = SpecialActivityEntry
        self.config = Config()

    def test_dates_and_exact_refresh_boundaries(self):
        current = current_shop_window()
        self.assertEqual(current.start.isoformat(), "2026-11-19T03:00:00+08:00")
        self.assertEqual(current.end.isoformat(), "2026-11-26T03:00:00+08:00")
        morning = current.start.replace(hour=11)
        evening = current.start.replace(hour=23)
        self.assertEqual(current.refresh_start(current.start), current.start)
        self.assertEqual(current.next_refresh(current.start), morning)
        self.assertEqual(current.refresh_start(morning), morning)
        self.assertEqual(current.next_refresh(morning), evening)
        self.assertEqual(current.refresh_start(evening), evening)
        self.assertIsNone(current.next_refresh(current.end - timedelta(seconds=1)))

    def test_archived_shop_never_dispatches_and_retains_historical_purchase_guards(self):
        from tasks.activity.legacy.huche_shop_2026_11_19 import huche_shop

        window = current_shop_window()
        for package, lang in (
            ("OVERSEA-Play", "global_cn"),
            ("OVERSEA-Play", "global_en"),
            ("CN-Official", "cn"),
        ):
            self.config.Emulator_PackageName = package
            events = calendar.active_activities(self.config, window.start, assets_lang=lang)
            self.assertFalse(any(event.mode == "huche_shop" for event in events))
        self.config.Emulator_PackageName = "OVERSEA-Play"
        with patch("tasks.activity.entry.active_activities", return_value=(window,)), patch.object(
            huche_shop, "HucheShop"
        ) as task, patch("tasks.activity.entry.delay_next_activity_check"):
            self.assertTrue(self.entry(self.config).run())
            task.assert_not_called()
        self.config.task_delay = Mock()
        shop = huche_shop.HucheShop(self.config, device=SimpleNamespace(app_is_running=lambda: True))
        with patch.object(huche_shop, "aware_time", return_value=window.start) as now, patch.object(
            shop, "run_purchase", return_value=True
        ) as purchase, patch.object(shop, "ui_goto") as navigate:
            self.assertTrue(shop.run())
            purchase.assert_called_once_with(window)
            navigate.assert_called_once()
            self.config.SpecialActivity_BuyHucheMysticMedals = False
            self.assertTrue(shop.run())
            self.config.SpecialActivity_BuyHucheMysticMedals = True
            now.return_value = window.end
            self.assertTrue(shop.run())
            self.assertEqual(purchase.call_count, 1)
            self.assertEqual(navigate.call_count, 1)

    def test_config_defaults_off_and_keeps_explicit_preference(self):
        from module.config.config_updater import ConfigUpdater
        updater=ConfigUpdater()
        self.assertFalse(updater.config_update({})['SpecialActivity']['SpecialActivity']['BuyHucheMysticMedals'])
        old={'SpecialActivity':{'SpecialActivity':{'BuyHucheMysticMedals':True}}}
        self.assertTrue(updater.config_update(old)['SpecialActivity']['SpecialActivity']['BuyHucheMysticMedals'])
        for path in (WORKTREE / 'module/config/i18n').glob('*.json'):
            entry=json.loads(path.read_text(encoding='utf-8'))['SpecialActivity']['BuyHucheMysticMedals']
            self.assertTrue(entry['name'])
            self.assertTrue(entry['help'])
        texts = json.loads((WORKTREE / 'module/config/i18n/zh-CN.json').read_text(encoding='utf-8'))
        shop_text = texts['SpecialActivity']['BuyHucheMysticMedals']
        self.assertEqual(shop_text['name'], '购买流浪商店神秘奖牌')
        self.assertIn('两档均为160天空石换50枚', shop_text['help'])
        args = json.loads((WORKTREE / 'module/config/argument/args.json').read_text(encoding='utf-8'))
        self.assertNotEqual(args['SpecialActivity']['SpecialActivity']['BuyHucheMysticMedals'].get('display'), 'hide')
        key = 'SpecialActivity.SpecialActivity.BuyHucheMysticMedals'
        profile = {'Alas': {'Emulator': {'PackageName': 'OVERSEA-Play', 'GameLanguage': 'cn'}}}
        window = current_shop_window()
        with patch.object(calendar, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = window.start
            self.assertIn(key, updater.get_hidden_args(profile))
            clock.now.return_value = window.end
            self.assertIn(key, updater.get_hidden_args(profile))

    def test_page_registration_and_routes_per_server(self):
        code="""
import sys
import module.config.server as server
server.set_lang(sys.argv[1])
server.server='CN-Official' if sys.argv[1]=='cn' else 'OVERSEA-Play'
from tasks.base import page
from tasks.activity.legacy.huche_shop_2026_11_19.huche_shop import HucheShop
from tasks.activity.assets.assets_activity_huche_shop import HUCHE_SHOP_CHECK, MAIN_GOTO_HUCHE_SHOP
assert 'tasks.activity.legacy.huche_shop_2026_09_17.huche_shop' not in sys.modules
assert hasattr(page,'page_huche_shop') == (sys.argv[1]=='global_cn')
list(page.Page.iter_check_buttons())
if sys.argv[1]=='global_cn':
    assert page.MAIN_GOTO_HUCHE_SHOP is MAIN_GOTO_HUCHE_SHOP
    assert HucheShop.SHOP_CHECK is HUCHE_SHOP_CHECK
    page.Page.init_connection(page.page_huche_shop)
    assert page.page_main.parent is page.page_huche_shop
    page.Page.init_connection(page.page_main)
    assert page.page_huche_shop.parent is page.page_main
    assert page.MAIN_GOTO_HUCHE_SHOP in page.page_main.links_need_match or page.page_huche_shop in page.page_main.links_need_match
print('verified',sys.argv[1])
"""
        for lang in ("cn", "global_cn", "global_en"):
            result = subprocess.run(
                [sys.executable, "-B", "-c", code, lang], cwd=WORKTREE,
                check=False, capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
