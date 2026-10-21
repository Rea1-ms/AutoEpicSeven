# ruff: noqa: E402
"""Page graph and dynamic-return rules, independent of captured game screens."""

import unittest

from module.config import server

server.set_lang('global_cn')

from tasks.base.page import (
    Page,
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
