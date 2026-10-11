# ruff: noqa: E402
"""Episode plans, stage parsing, and fast-combat rules."""

import unittest

from module.config import server

server.set_lang('global_cn')

from tasks.dungeon.episode import EPISODE4_MATERIAL_PLANS, EpisodeNavigateMixin
from tasks.dungeon.dungeon import Combat


class _DummyEpisode(EpisodeNavigateMixin):
    pass


class LegacyRuleTests(unittest.TestCase):
    def test_episode4_material_plan_mapping(self):
        self.assertEqual(EPISODE4_MATERIAL_PLANS["CATALYST_RARE_BENEVOLENT"].stage_label, "1-9")
        self.assertEqual(EPISODE4_MATERIAL_PLANS["BREATH_OF_KARMA"].stage_label, "6-10")
        self.assertEqual(EPISODE4_MATERIAL_PLANS["TRACES_OF_BRILLIANCE"].stage_label, "10-9")
        self.assertEqual(EPISODE4_MATERIAL_PLANS["CATALYST_RARE_SECRET"].stage_label, "2-10")

    def test_extract_episode_number(self):
        dummy = _DummyEpisode()
        self.assertEqual(dummy._extract_episode_number("1. 不愉快的第一印象"), 1)
        self.assertEqual(dummy._extract_episode_number("10. 终末之地"), 10)
        self.assertEqual(dummy._extract_episode_number("第4章"), 4)
        self.assertEqual(dummy._extract_episode_number("无有效数字"), 0)

    def test_episode4_supports_fast_combat(self):
        episode4 = Combat.__new__(Combat)
        episode4._dungeon_domain = lambda: "Episode4"
        episode4._combat_grade = lambda: "5-5"
        self.assertIs(episode4._combat_supports_fast_combat(), True)

        saint37 = Combat.__new__(Combat)
        saint37._dungeon_domain = lambda: "Saint37"
        saint37._combat_grade = lambda: "3-7"
        self.assertIs(saint37._combat_supports_fast_combat(), False)

    def test_episode_target_above_current_should_reset_to_top(self):
        target = EPISODE4_MATERIAL_PLANS["CATALYST_RARE_OATH"]
        current_stage = 7
        self.assertIs(current_stage > target.stage, True)

    def test_episode_target_equal_current_should_not_reset_to_top(self):
        target = EPISODE4_MATERIAL_PLANS["CATALYST_RARE_OATH"]
        current_stage = 5
        self.assertIs(current_stage > target.stage, False)
