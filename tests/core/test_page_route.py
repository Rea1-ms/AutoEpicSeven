# ruff: noqa: E402
"""Page graph and dynamic-return rules, independent of captured game screens."""

import subprocess
import sys
import unittest

from module.config import server

server.set_lang('global_cn')

from tasks.base.page import (
    Page,
    page_episode,
    page_episode_selector,
    page_inventory,
    page_inventory_equipment,
    page_knights,
    page_knights_team_battle,
    page_mail,
    page_menu,
    page_mission_reward_daily,
    page_pets,
    page_sanctuary,
    page_sanctuary_forest,
    page_sanctuary_tower,
)
from tasks.base.ui import UI
from tasks.dungeon.assets.assets_dungeon_episode_navigation import (
    EPISODE_SELECTOR_ENTRY,
)
from tests.support.history_main_navigation import Route, frame, image_from_asset
from tests.support.offline import ROOT


class LegacyRuleTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(Page.clear_connection)

    def test_mission_reward_popup_returns_to_menu_chain(self):
        Page.init_connection(
            page_pets,
            extra_links={
                page_mission_reward_daily: {page_menu: page_mission_reward_daily.dynamic_return_button},
                page_menu: {page_knights: page_menu.dynamic_return_button},
            },
        )
        self.assertIs(page_mission_reward_daily.parent, page_menu)
        self.assertIs(page_menu.parent, page_pets)

    def test_menu_dynamic_return_beats_same_depth_home_fallback(self):
        Page.init_connection(
            page_sanctuary_tower,
            extra_links={page_menu: {page_sanctuary_forest: page_menu.dynamic_return_button}},
        )
        self.assertIs(page_menu.parent, page_sanctuary_forest)
        self.assertIs(page_sanctuary_forest.parent, page_sanctuary)

    def test_mail_and_inventory_return_to_previous_page_chain(self):
        Page.init_connection(
            page_pets,
            extra_links={page_inventory_equipment: {page_pets: page_inventory_equipment.dynamic_return_button}},
        )
        self.assertIs(page_inventory_equipment.parent, page_pets)
        Page.clear_connection()

        Page.init_connection(
            page_knights,
            extra_links={page_mail: {page_knights: page_mail.dynamic_return_button}},
        )
        self.assertIs(page_mail.parent, page_knights)

    def test_knights_team_battle_returns_to_knights_home(self):
        Page.init_connection(page_knights)
        self.assertIs(page_knights_team_battle.parent, page_knights)

    def test_inventory_tabs_share_one_overlay_origin(self):
        ui = UI.__new__(UI)
        ui._ui_dynamic_origins = {}
        ui._ui_set_dynamic_origin(page_inventory, page_mail)
        ui._ui_set_dynamic_origin(page_inventory_equipment, page_inventory)
        self.assertIs(ui._ui_get_dynamic_origin(page_inventory), page_mail)
        self.assertIs(ui._ui_get_dynamic_origin(page_inventory_equipment), page_mail)

    def test_episode_selector_route_waits_for_blue_entry_and_returns_to_map(self):
        asset = EPISODE_SELECTOR_ENTRY.buttons[0]
        asset.clear_offset()
        self.addCleanup(asset.clear_offset)
        offset = (8, -6)
        expected = tuple(value + offset[index % 2] for index, value in enumerate(asset.button))
        nav = Route([
            frame(0, pages=[page_episode]),
            frame(.2, image_from_asset(asset, offset=offset), pages=[page_episode]),
            frame(.4, pages=[page_episode_selector]),
        ])
        nav.run_route(page_episode_selector)
        self.assertEqual(len(nav.clicks), 1)
        at, button, area = nav.clicks[0]
        self.assertEqual(at, .2)
        self.assertEqual(button.name, EPISODE_SELECTOR_ENTRY.name)
        self.assertEqual(area, expected)
        self.assertEqual(nav.index, 2)

        nav = Route([
            frame(0, pages=[page_episode_selector]),
            frame(.2, pages=[page_episode]),
        ])
        nav.run_route(page_episode)
        self.assertEqual([button.name for _, button, _ in nav.clicks], ['BACK'])
        self.assertEqual(nav.index, 1)

    def test_episode_selector_registration_scan_and_routes_by_language(self):
        code = """
from types import SimpleNamespace
from unittest.mock import Mock
import module.config.server as server
server.server = {package!r}
server.set_lang({lang!r})
from tasks.base import page
from tasks.base.ui import UI
from tasks.dungeon.dungeon import Combat

registered = 'page_episode_selector' in page.Page.all_pages
assert registered == (server.lang in ('cn', 'global_cn'))
if registered:
    selector = page.page_episode_selector
    assert selector.check_button.name == 'EPISODE_SELECTOR_CHECK'
    assert selector in page.page_episode.links_need_match
    assert page.page_episode.links[selector].name == 'EPISODE_SELECTOR_ENTRY'
    assert selector.links[page.page_episode].name == 'BACK'
    assert selector.shared_toolbar
    assert Combat._is_background_repeat_check_page(None, selector)
    page.Page.init_connection(selector)
    assert page.page_episode.parent == selector
    assert page.page_main.parent == page.page_episode
    page.Page.init_connection(page.page_episode)
    assert selector.parent == page.page_episode
    ui = UI.__new__(UI)
    ui.device = SimpleNamespace(image=object(), screenshot=Mock(side_effect=AssertionError('Unexpected device call')))
    ui.is_in_main = lambda interval=0: False
    ui.appear = lambda button, **kwargs: button is selector.check_button
    assert ui.ui_get_current_page() == selector
    ui.device.screenshot.assert_not_called()
else:
    assert not hasattr(page, 'page_episode_selector')
    assert all(check.name != 'EPISODE_SELECTOR_CHECK'
               for check in page.Page.iter_check_buttons() if hasattr(check, 'name'))
    page.Page.init_connection(page.page_episode)
    assert page.page_main.parent == page.page_episode
page.Page.clear_connection()
"""
        for package, lang in (('CN-Official', 'cn'), ('OVERSEA-Play', 'global_cn'), ('OVERSEA-Play', 'global_en')):
            with self.subTest(package=package, lang=lang):
                result = subprocess.run(
                    [sys.executable, '-B', '-X', 'utf8', '-c', code.format(package=package, lang=lang)],
                    cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=45, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
