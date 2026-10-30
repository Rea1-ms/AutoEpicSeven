# ruff: noqa: E402
from module.config import server as _test_server

_test_server.set_lang("global_cn")

"""Offline checks for movable hunt and spirit-altar combat entries."""
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

WORKTREE = Path(__file__).resolve().parents[2]
WORKTREE_ROOT = WORKTREE
LANGUAGE = "global_cn"

import module.config.server as server
server.set_lang(LANGUAGE)
server.server = "CN-Official" if LANGUAGE == "cn" else "OVERSEA-Play"

from module.base.utils import load_image
from tasks.base.page import Page, page_combat, page_combat_common, page_combat_stage, page_main
from tasks.dungeon.assets.assets_dungeon_configs_combat_entry import CONBAT_ENTRIES, HUNT, SPIRIT_ALTAR
from tasks.dungeon.dungeon import Combat
from tasks.dungeon.plan import ALTAR_PLAN, HUNT_PLAN


class CombatEntrySearchTests(unittest.TestCase):
    def setUp(self):
        self.clicks = []
        self.combat = object.__new__(Combat)
        self.combat.config = SimpleNamespace()
        self.combat.interval_timer = {}
        self.combat.device = SimpleNamespace(
            image=None,
            screenshot=lambda: None,
            stuck_record_add=lambda button: None,
            click=lambda button: self.clicks.append(tuple(button.button)),
        )
        HUNT.clear_offset()
        SPIRIT_ALTAR.clear_offset()

    def screenshot(self, image):
        self.combat.device.image = image
        self.combat.device.screenshot()

    @staticmethod
    def relocated_image(button, x, y):
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        template = button.buttons[0].image
        height, width = template.shape[:2]
        image[y:y + height, x:x + width] = template
        return image, (x, y, x + width, y + height)

    def test_supplied_board_recognizes_page_and_clicks_moved_altar(self):
        self.screenshot(load_image(CONBAT_ENTRIES.buttons[0].file))
        self.assertTrue(self.combat.ui_page_appear(page_combat_common))
        self.assertTrue(self.combat.ui_page_appear(page_combat))
        self.assertTrue(self.combat._is_combat_general_board())
        for plan, expected in ((ALTAR_PLAN, (360, 185, 384, 213)),
                               (HUNT_PLAN, (667, 186, 691, 215))):
            with self.subTest(plan=plan.name):
                self.assertTrue(self.combat.appear_then_click(plan.entry, interval=0))
                self.assertEqual(self.clicks[-1], expected)

    def test_both_entries_follow_new_card_positions(self):
        for plan in (ALTAR_PLAN, HUNT_PLAN):
            for x in (53, 360, 667, 974):
                with self.subTest(plan=plan.name, x=x):
                    image, expected = self.relocated_image(plan.entry, x, 185)
                    self.screenshot(image)
                    self.assertTrue(self.combat._is_combat_general_board())
                    if plan is ALTAR_PLAN:
                        self.assertTrue(self.combat.ui_page_appear(page_combat_common))
                    self.assertTrue(self.combat.appear_then_click(plan.entry, interval=0))
                    self.assertEqual(self.clicks[-1], expected)

    def test_identical_icons_outside_entry_strip_are_not_clicked(self):
        for plan in (ALTAR_PLAN, HUNT_PLAN):
            with self.subTest(plan=plan.name):
                image, _ = self.relocated_image(plan.entry, 360, 400)
                self.screenshot(image)
                self.assertFalse(self.combat._is_combat_general_board())
                self.assertFalse(self.combat.ui_page_appear(page_combat_common))
                self.assertFalse(self.combat.appear_then_click(plan.entry, interval=0))
        self.assertEqual(self.clicks, [])

    def test_stage_entry_reinitializes_search_without_page_side_effects(self):
        original_searches = [(button, button.search) for button in (HUNT, SPIRIT_ALTAR)]
        for button, search in original_searches:
            self.addCleanup(button.load_search, search)

        for plan in (ALTAR_PLAN, HUNT_PLAN):
            with self.subTest(plan=plan.name):
                # Undo page initialization to prove the dungeon entry owns its
                # search setup. Both icons move outside their source searches.
                for button in (HUNT, SPIRIT_ALTAR):
                    x1, y1, x2, y2 = button.area
                    button.load_search((x1 - 20, y1 - 20, x2 + 20, y2 + 20))
                image, expected = self.relocated_image(plan.entry, 974, 185)
                frames = iter((image, load_image(plan.stage_check.buttons[0].file)))

                def screenshot():
                    self.combat.device.image = next(frames)

                self.combat.device.screenshot = screenshot
                self.combat.is_in_main = lambda interval=0: False
                self.combat._handle_dungeon_additional = lambda: False
                self.combat.interval_timer.clear()
                self.combat.device.screenshot()
                before = len(self.clicks)
                self.assertTrue(self.combat._enter_stage_page(plan, skip_first_screenshot=True))
                self.assertEqual(self.clicks[before:], [expected])

    def test_common_board_routes_remain_connected(self):
        Page.init_connection(page_combat_common)
        self.assertIs(page_main.parent, page_combat_common)
        Page.init_connection(page_main)
        self.assertIs(page_combat_common.parent, page_main)
        self.assertIsNotNone(page_combat_stage.parent)
        self.assertIn(page_combat_common, page_combat_stage.links)
        self.assertIn(SPIRIT_ALTAR, list(Page.iter_check_buttons()))
