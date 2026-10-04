# ruff: noqa: E402
"""Portrait, same-card selection, default configuration and Ogre regressions."""

import unittest

import numpy as np

from module.config import server

server.set_lang("global_cn")

from module.config.config_updater import ConfigUpdater
from tasks.dungeon.assets.assets_dungeon_configs_combat_element_hunt import (
    DARK_SELECTED,
    ELEMENT_SEARCH,
)
from tasks.dungeon.assets.assets_dungeon_configs_combat_hunt_boss import (
    HUNT_CARD_SEARCH,
    HUNT_CARD_SELECTED,
)
from tasks.dungeon.dungeon import Combat
from tasks.dungeon.hunt import HUNT_BOSSES, HUNT_BOSS_ELEMENTS
from tasks.dungeon.plan import HUNT_PLAN
from tests.support.hunt import DARK_HUNTS, ELEMENT_HUNTS, HuntReplay, hunt_image
from tests.support.offline import ControlledClock


class HuntBossTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")
        self.clock = ControlledClock()
        self.clock.__enter__()
        self.addCleanup(self.clock.__exit__, None, None, None)

    def replay(self, frames, boss="Wyvern"):
        replay = HuntReplay(Combat, frames, self.clock)
        replay.task.config.Combat_HuntBoss = boss
        return replay

    def selected_image(self, image, boss):
        image = image.copy()
        task = self.replay([image], boss).task
        button = task._hunt_boss_button()
        self.assertTrue(task.match_template_luma(button, similarity=.8))
        image[130:720, 1254:1256] = 0
        image[button.button[1]:button.button[3], 1254:1256] = HUNT_CARD_SELECTED.color
        return image

    def test_shared_dark_icon_never_confirms_azimanak_from_selected_ogre(self):
        task = self.replay([hunt_image(DARK_HUNTS)], "Azimanak").task
        DARK_SELECTED.load_search(ELEMENT_SEARCH.area)
        self.assertTrue(task._is_selected_element(DARK_SELECTED))
        self.assertFalse(task._is_selected_hunt_boss(task._hunt_boss_button()))
        task.config.Combat_HuntBoss = "Ogre"
        self.assertTrue(task._is_selected_hunt_boss(task._hunt_boss_button()))

    def test_all_six_portraits_match_only_their_visible_cards(self):
        for fixture, visible in (
            (DARK_HUNTS, {"Azimanak", "Caides", "Ogre"}),
            (ELEMENT_HUNTS, {"Wyvern", "Golem", "Banshee"}),
        ):
            task = self.replay([hunt_image(fixture)]).task
            for boss in HUNT_BOSSES:
                with self.subTest(fixture=fixture, boss=boss):
                    task.config.Combat_HuntBoss = boss
                    button = task._hunt_boss_button()
                    matched = task.match_template_luma(button, similarity=.8)
                    self.assertEqual(matched, boss in visible)
                    if matched:
                        expected = {
                            "Wyvern": (1130, 160, 1200, 225),
                            "Golem": (1130, 365, 1200, 435),
                            "Banshee": (1130, 575, 1200, 645),
                            "Azimanak": (1140, 153, 1210, 223),
                            "Caides": (1110, 350, 1180, 420),
                            "Ogre": (1110, 550, 1190, 622),
                        }
                        self.assertEqual(button.button, expected[boss])
                    self.assertEqual(button.search, (1032, 144, 1253, 720))

    def test_text_changes_and_assets_languages_do_not_change_portrait_selection(self):
        image = hunt_image(DARK_HUNTS)
        for top, bottom in ((242, 310), (441, 513), (645, 720)):
            image[top:bottom, 1030:1253] = 0
        for lang in ("cn", "global_cn", "global_en"):
            with self.subTest(lang=lang):
                server.set_lang(lang)
                task = self.replay([image], "Ogre").task
                self.assertTrue(task._is_selected_hunt_boss(task._hunt_boss_button()))
                task.config.Combat_HuntBoss = "Azimanak"
                self.assertFalse(task._is_selected_hunt_boss(task._hunt_boss_button()))

    def test_selection_border_belongs_to_the_matched_card(self):
        image = self.selected_image(hunt_image(DARK_HUNTS), "Azimanak")
        task = self.replay([image], "Azimanak").task
        self.assertTrue(task._is_selected_hunt_boss(task._hunt_boss_button()))
        task.config.Combat_HuntBoss = "Ogre"
        self.assertFalse(task._is_selected_hunt_boss(task._hunt_boss_button()))

    def test_missing_portrait_never_reuses_a_previous_selected_offset(self):
        task = self.replay([hunt_image(DARK_HUNTS)], "Ogre").task
        button = task._hunt_boss_button()
        self.assertTrue(task._is_selected_hunt_boss(button))
        task.device.image = hunt_image(ELEMENT_HUNTS)
        self.assertFalse(task._is_selected_hunt_boss(button))

    def test_already_selected_target_exits_without_clicking(self):
        image = hunt_image(DARK_HUNTS)
        replay = self.replay([image] * 5, "Ogre")
        self.assertTrue(replay.task._select_element(HUNT_PLAN))
        self.assertEqual(replay.actions, [])

    def test_dropped_click_retries_correct_card_until_positive_selection(self):
        image = hunt_image(DARK_HUNTS)
        selected = self.selected_image(image, "Azimanak")
        replay = self.replay([image] * 8 + [selected] * 5, "Azimanak")
        self.assertTrue(replay.task._select_element(HUNT_PLAN))
        self.assertGreaterEqual(len(replay.actions), 2)
        self.assertTrue(all(action[1] == "AZIMANAK" for action in replay.actions))
        self.assertTrue(all(action[2] == (1140, 153, 1210, 223) for action in replay.actions))
        self.assertTrue(all(action[0] < 8 for action in replay.actions))

    def test_list_scrolls_back_up_to_a_target_above_current_view(self):
        lower = hunt_image(DARK_HUNTS)
        upper = hunt_image(ELEMENT_HUNTS)
        selected = self.selected_image(upper, "Wyvern")
        replay = self.replay([lower] * 4 + [upper] * 5 + [selected] * 5, "Wyvern")
        self.assertTrue(replay.task._select_element(HUNT_PLAN))
        swipes = [a for a in replay.actions if a[1] == "swipe"]
        self.assertEqual(len(swipes), 1)
        self.assertLess(swipes[0][2][1], swipes[0][3][1])
        self.assertTrue(any(a[1] == "WYVERN" for a in replay.actions))

    def test_list_scrolls_down_to_ogre_below_current_view(self):
        upper, lower = hunt_image(ELEMENT_HUNTS), hunt_image(DARK_HUNTS)
        replay = self.replay([upper] * 4 + [lower] * 8, "Ogre")
        self.assertTrue(replay.task._select_element(HUNT_PLAN))
        self.assertEqual(len(replay.actions), 1)
        self.assertEqual(replay.actions[0][1], "swipe")
        self.assertGreater(replay.actions[0][2][1], replay.actions[0][3][1])

    def test_missing_target_stops_at_scroll_limit_without_clicking(self):
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        replay = self.replay([image] * 120, "Ogre")
        self.assertFalse(replay.task._select_element(HUNT_PLAN))
        self.assertEqual(len(replay.actions), replay.task.COMBAT_MAX_SCROLLS)
        self.assertTrue(all(a[1] == "swipe" for a in replay.actions))

    def test_default_wyvern_and_named_targets_ignore_altar_element(self):
        task = self.replay([hunt_image(DARK_HUNTS)]).task
        del task.config.Combat_HuntBoss
        for element in ("Fire", "Nature", "Water", "Dark", "Light"):
            with self.subTest(element=element):
                task.config.Combat_Element = element
                self.assertEqual(task._hunt_boss(), "Wyvern")
                self.assertEqual(task._combat_element(), "Fire")
        for boss, element in HUNT_BOSS_ELEMENTS.items():
            with self.subTest(boss=boss):
                task.config.Combat_HuntBoss = boss
                self.assertEqual(task._hunt_boss(), boss)
                self.assertEqual(task._combat_element(), element)
        task.config.Combat_Domain = "SpiritAltar"
        self.assertEqual(task._combat_element(), "Light")

    def test_ogre_uses_only_dimensional_grade_and_disables_fast_combat(self):
        task = self.replay([hunt_image(DARK_HUNTS)], "Ogre").task
        for grade in ("Mid", "High", "Hell", "Dimensional"):
            with self.subTest(grade=grade):
                task.config.Combat_HuntGrade = grade
                self.assertEqual(task._combat_grade(), "Dimensional")
                self.assertFalse(task._combat_supports_fast_combat())
                self.assertFalse(task._combat_should_use_fast())
        task.config.Combat_HuntBoss = "Azimanak"
        task.config.Combat_HuntGrade = "Hell"
        self.assertEqual(task._combat_grade(), "Hell")
        self.assertTrue(task._combat_supports_fast_combat())

    def test_ogre_clicks_its_top_dimensional_entry_until_prepare_page(self):
        image = hunt_image(DARK_HUNTS)
        replay = self.replay([image] * 12, "Ogre")
        replay.task._is_prepare_page = lambda: replay.index >= 8
        self.assertTrue(replay.task._enter_prepare_page(HUNT_PLAN))
        self.assertGreaterEqual(len(replay.actions), 2)
        self.assertTrue(all(a[1] == "OGRE_DIMENSIONAL" for a in replay.actions))
        self.assertTrue(all(a[2] == (865, 149, 957, 168) for a in replay.actions))
        self.assertTrue(all(a[0] < 8 for a in replay.actions))

    def test_common_search_finds_scrolled_portrait_and_clicks_shifted_position(self):
        image = hunt_image(DARK_HUNTS)
        selected = self.selected_image(image, "Azimanak")
        frames = []
        for source in (image, selected):
            shifted = np.zeros_like(source)
            shifted[344:720, 1032:1258] = source[144:520, 1032:1258]
            frames.append(shifted)
        replay = self.replay([frames[0]] * 8 + [frames[1]] * 5, "Azimanak")
        self.assertEqual(HUNT_CARD_SEARCH.area, (1032, 144, 1253, 720))
        self.assertTrue(replay.task._select_element(HUNT_PLAN))
        self.assertGreaterEqual(len(replay.actions), 2)
        self.assertTrue(all(a[1] == "AZIMANAK" for a in replay.actions))
        self.assertTrue(all(a[2] == (1140, 353, 1210, 423) for a in replay.actions))

    def test_portrait_outside_common_search_never_matches_or_clicks(self):
        source = hunt_image(DARK_HUNTS)
        image = np.zeros_like(source)
        image[153:223, 500:570] = source[153:223, 1140:1210]
        replay = self.replay([image], "Azimanak")
        button = replay.task._hunt_boss_button()
        self.assertFalse(replay.task._is_selected_hunt_boss(button))
        self.assertFalse(replay.task.appear_then_click(button, interval=0))
        self.assertEqual(replay.actions, [])

    def test_selection_border_does_not_depend_on_click_rectangle(self):
        replay = self.replay([hunt_image(DARK_HUNTS)], "Ogre")
        button = replay.task._hunt_boss_button()
        source = button.buttons[0]
        original = source._button
        self.addCleanup(setattr, source, "_button", original)
        source._button = (1110, 650, 1190, 670)
        self.assertTrue(replay.task._is_selected_hunt_boss(button))

    def test_hunt_options_hide_altar_controls_and_ogre_inapplicable_settings(self):
        updater = ConfigUpdater()
        combat_args = updater.args["Combat"]["Combat"]
        self.assertEqual(combat_args["HuntBoss"]["value"], "Wyvern")
        self.assertEqual(combat_args["HuntBoss"]["option"], list(HUNT_BOSSES))
        self.assertNotIn("RepeatCombatCount", combat_args)
        updated = updater.config_update({"Combat": {"Combat": {"Element": "Dark", "RepeatCombatCount": 5}}})
        self.assertEqual(updated["Combat"]["Combat"]["HuntBoss"], "Wyvern")
        self.assertNotIn("RepeatCombatCount", updated["Combat"]["Combat"])
        data = {"Combat": {"Combat": {"Domain": "Hunt", "HuntBoss": "Ogre"}}}
        hidden = updater.get_hidden_args(data)
        for name in ("Element", "AltarGrade", "HuntGrade", "FastCombat", "FastCombatCount", "BurnoutMode"):
            self.assertIn(f"Combat.Combat.{name}", hidden)
        self.assertNotIn("Combat.Combat.HuntBoss", hidden)
        data["Combat"]["Combat"]["Domain"] = "SpiritAltar"
        hidden = updater.get_hidden_args(data)
        self.assertIn("Combat.Combat.HuntBoss", hidden)
        self.assertNotIn("Combat.Combat.Element", hidden)
        self.assertNotIn("Combat.Combat.AltarGrade", hidden)
