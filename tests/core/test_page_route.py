# ruff: noqa: E402
from module.config import server as _test_server

_test_server.set_lang("global_cn")

from pathlib import Path


WORKTREE = Path(__file__).resolve().parents[2]
WORKTREE_ROOT = WORKTREE

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


def test_mission_reward_popup_returns_to_menu_chain():
    Page.init_connection(
        page_pets,
        extra_links={
            page_mission_reward_daily: {page_menu: page_mission_reward_daily.dynamic_return_button},
            page_menu: {page_knights: page_menu.dynamic_return_button},
        },
    )
    try:
        assert page_mission_reward_daily.parent == page_menu
        assert page_menu.parent == page_pets
    finally:
        Page.clear_connection()


def test_menu_dynamic_return_beats_same_depth_home_fallback():
    Page.init_connection(
        page_sanctuary_tower,
        extra_links={
            page_menu: {page_sanctuary_forest: page_menu.dynamic_return_button},
        },
    )
    try:
        assert page_menu.parent == page_sanctuary_forest
        assert page_sanctuary_forest.parent == page_sanctuary
    finally:
        Page.clear_connection()


def test_mail_and_inventory_return_to_previous_page_chain():
    Page.init_connection(
        page_pets,
        extra_links={
            page_inventory_equipment: {page_pets: page_inventory_equipment.dynamic_return_button},
        },
    )
    try:
        assert page_inventory_equipment.parent == page_pets
    finally:
        Page.clear_connection()

    Page.init_connection(
        page_knights,
        extra_links={
            page_mail: {page_knights: page_mail.dynamic_return_button},
        },
    )
    try:
        assert page_mail.parent == page_knights
    finally:
        Page.clear_connection()


def test_knights_team_battle_returns_to_knights_home():
    Page.init_connection(page_knights)
    try:
        assert page_knights_team_battle.parent == page_knights
    finally:
        Page.clear_connection()


def test_inventory_tabs_share_one_overlay_origin():
    from tasks.base.ui import UI

    ui = object.__new__(UI)
    ui._ui_dynamic_origins = {}

    ui._ui_set_dynamic_origin(page_inventory, page_mail)
    ui._ui_set_dynamic_origin(page_inventory_equipment, page_inventory)

    assert ui._ui_get_dynamic_origin(page_inventory) == page_mail
    assert ui._ui_get_dynamic_origin(page_inventory_equipment) == page_mail


def main():
    test_mission_reward_popup_returns_to_menu_chain()
    test_menu_dynamic_return_beats_same_depth_home_fallback()
    test_mail_and_inventory_return_to_previous_page_chain()
    test_knights_team_battle_returns_to_knights_home()
    test_inventory_tabs_share_one_overlay_origin()
    print("test_page_route: ok")




import unittest

class LegacyRuleTests(unittest.TestCase):
    def test_mission_reward_popup_returns_to_menu_chain(self):
        test_mission_reward_popup_returns_to_menu_chain()

    def test_menu_dynamic_return_beats_same_depth_home_fallback(self):
        test_menu_dynamic_return_beats_same_depth_home_fallback()

    def test_mail_and_inventory_return_to_previous_page_chain(self):
        test_mail_and_inventory_return_to_previous_page_chain()

    def test_knights_team_battle_returns_to_knights_home(self):
        test_knights_team_battle_returns_to_knights_home()

    def test_inventory_tabs_share_one_overlay_origin(self):
        test_inventory_tabs_share_one_overlay_origin()
