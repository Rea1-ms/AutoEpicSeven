# ruff: noqa: E402
import unittest
from module.config import server
server.set_lang("global_cn")
from tests.support.history_fixtures import input_root, read_input as load_image

from pathlib import Path
WORKTREE=Path(__file__).resolve().parents[2]

"""Offline regressions for blank-frame navigation and guild-war recognition.

Run from the checkout under test with its Python interpreter and --lang cn,
--lang global_cn, or --lang global_en. No emulator, config writes, or notifications.
"""





from pathlib import Path

from unittest.mock import patch

import numpy as np



import module.config.server as server

from module.base.button import Button

from tasks.base.assets.assets_base_main_page import MENU_CLOSE

from tasks.base.page import (
    Page, page_inventory, page_inventory_equipment, page_knights,
    page_knights_team_battle,
)

from tasks.base.ui import UI

from tasks.item.assets.assets_item_inventory import EQUIPMENT_CHECK, INVENTORY_CHECK

from tasks.knights.assets.assets_knights_gvg import KNIGHTS_CREST, KNIGHTS_NOT_ENOUGH_PEOPLE

from tasks.knights.assets.assets_knights_main_page import KNIGHTS_CHECK

from tasks.knights.team_battle import KnightsTeamBattleMixin

SCREENSHOTS = input_root("navigation_recognition")
read = load_image

BLACK = read(SCREENSHOTS / 'black.png')

WAR = read(SCREENSHOTS / 'war.png')

class ReplayDevice:
    def __init__(self, frames):
        self.frames = iter(frames)
        self.clicks = []
        self.now = 100.0
        self.image = None

    def screenshot(self):
        try:
            self.image = next(self.frames)
        except StopIteration as exc:
            raise AssertionError('Navigation failed to finish within the supplied frames') from exc
        # Each frame is deliberately slower than the two-second retry guard.
        # A loading frame must remain unrecognized after that guard expires.
        self.now += 3
        return self.image

    def stuck_record_add(self, button):
        pass

    def click(self, button):
        self.clicks.append(button.name)

class ReplayUI(UI):
    def __init__(self, frames):
        self.device = ReplayDevice(frames)
        self.interval_timer = {}

    def handle_ui_recovery(self):
        return False

    def ui_additional(self):
        return False

    def handle_popup_confirm(self):
        return False

    def ui_page_confirm(self, page):
        return False

def reader(frame):
    ui = ReplayUI([frame])
    ui.device.screenshot()
    return ui

class TemplateTests(unittest.TestCase):
    def test_blank_screenshot_is_really_black(self):
        self.assertEqual(BLACK.shape, (720, 1280, 3))
        self.assertFalse(np.any(BLACK))

    def test_flat_frames_never_match_inventory_assets(self):
        for wrapper in (INVENTORY_CHECK, EQUIPMENT_CHECK):
            for value in (0, 80, 255):
                frame = np.full_like(BLACK, value)
                with self.subTest(asset=wrapper.name, value=value):
                    self.assertFalse(wrapper.match_template(frame))
                    self.assertFalse(wrapper.match_template_luma(frame))
                    self.assertFalse(wrapper.match_template_color(frame))
                    self.assertEqual(wrapper.match_multi_template(frame), [])

    def test_real_inventory_assets_still_match(self):
        for wrapper in (INVENTORY_CHECK, EQUIPMENT_CHECK):
            frame = read(wrapper.buttons[0].file)
            with self.subTest(asset=wrapper.name):
                self.assertTrue(wrapper.match_template(frame))
                self.assertTrue(wrapper.match_template_luma(frame))
                self.assertTrue(wrapper.match_template_color(frame))
                self.assertTrue(wrapper.match_multi_template(frame))
                self.assertTrue(wrapper.match_template(frame, direct_match=True))
                self.assertTrue(wrapper.match_template_luma(frame, direct_match=True))
                self.assertTrue(reader(frame).ui_page_appear(page_inventory))
        self.assertTrue(reader(read(EQUIPMENT_CHECK.buttons[0].file)).ui_page_appear(page_inventory_equipment))

    def test_larger_search_preserves_match_offset(self):
        original = INVENTORY_CHECK.buttons[0]
        button = Button(original.file, original.area, original.search, original.color, original.area)
        x, y, right, bottom = original.area
        width, height = right - x, bottom - y
        dx, dy = 7, -6
        frame = BLACK.copy()
        frame[y+dy:y+dy+height, x+dx:x+dx+width] = button.image
        self.assertTrue(button.match_template(frame))
        self.assertEqual(tuple(button._button_offset), (dx, dy))
        self.assertTrue(button.match_template_luma(frame))
        self.assertEqual(tuple(button._button_offset), (dx, dy))
        self.assertEqual(button.match_multi_template(frame), [[x+dx, y+dy]])

    def test_multi_match_preserves_both_positions(self):
        button = INVENTORY_CHECK.buttons[0]
        frame = BLACK.copy()
        height, width = button.image.shape[:2]
        positions = [(16, 635), (56, 671)]
        for x, y in positions:
            frame[y:y+height, x:x+width] = button.image
        self.assertEqual(button.match_multi_template(frame), [list(p) for p in positions])

class PageTests(unittest.TestCase):
    def tearDown(self):
        Page.clear_connection()

    def test_black_frame_matches_no_registered_page(self):
        ui = reader(BLACK)
        matched = [p.name for p in Page.iter_pages() if p.check_button is not None and ui.ui_page_appear(p)]
        self.assertEqual(matched, [])

    def test_guild_war_task_and_navigation_agree_on_reported_frame(self):
        ui = reader(WAR)
        ui.TEAM_BATTLE_HOME_SIMILARITY = KnightsTeamBattleMixin.TEAM_BATTLE_HOME_SIMILARITY
        self.assertFalse(KNIGHTS_CREST.match_template(WAR, similarity=0.85))
        self.assertTrue(KnightsTeamBattleMixin._is_team_battle_home(ui))
        self.assertTrue(ui.ui_page_appear(page_knights_team_battle))
        self.assertFalse(ui.ui_page_appear(page_inventory))

    def test_insufficient_members_remains_recognizable(self):
        ui = reader(read(KNIGHTS_NOT_ENOUGH_PEOPLE.buttons[0].file))
        self.assertTrue(ui.ui_page_appear(page_knights_team_battle))

    def test_crest_threshold_does_not_lower_insufficient_members_threshold(self):
        ui = reader(BLACK)
        with patch.object(ui, 'appear', side_effect=[False, False]) as appear:
            self.assertFalse(ui.ui_page_appear(page_knights_team_battle))
        self.assertEqual(appear.call_args_list[0].kwargs['similarity'], 0.7)
        self.assertNotIn('similarity', appear.call_args_list[1].kwargs)

    def test_team_battle_has_direct_return_route(self):
        Page.init_connection(page_knights)
        self.assertEqual(page_knights_team_battle.parent, page_knights)
        self.assertEqual(page_knights_team_battle.links[page_knights].name, 'BACK')

    def test_reported_error_frame_matches_team_battle(self):
        frame = read(SCREENSHOTS / "error.png")
        self.assertTrue(reader(frame).ui_page_appear(page_knights_team_battle))

class NavigationTests(unittest.TestCase):
    def tearDown(self):
        Page.clear_connection()

    def navigate(self, frames):
        ui = ReplayUI(frames)
        with patch('module.base.timer.time', side_effect=lambda: ui.device.now):
            ui.device.screenshot()
            ui.ui_goto(page_knights)
        return ui.device.clicks

    def test_menu_to_knights_does_not_click_back_during_black_loading(self):
        clicks = self.navigate([
            read(MENU_CLOSE.buttons[0].file), BLACK, BLACK,
            read(KNIGHTS_CHECK.buttons[0].file),
        ])
        self.assertEqual(clicks, ['MENU_GOTO_KNIGHTS'])

    def test_guild_war_returns_with_one_back(self):
        clicks = self.navigate([WAR, BLACK, read(KNIGHTS_CHECK.buttons[0].file)])
        self.assertEqual(clicks, ['BACK'])

    def test_insufficient_members_returns_with_one_back(self):
        clicks = self.navigate([
            read(KNIGHTS_NOT_ENOUGH_PEOPLE.buttons[0].file), BLACK,
            read(KNIGHTS_CHECK.buttons[0].file),
        ])
        self.assertEqual(clicks, ['BACK'])
