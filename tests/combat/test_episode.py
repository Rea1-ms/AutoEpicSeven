# ruff: noqa: E402
from module.config import server as _test_server

_test_server.set_lang("global_cn")

from pathlib import Path


WORKTREE = Path(__file__).resolve().parents[2]
WORKTREE_ROOT = WORKTREE


from tasks.dungeon.episode import EPISODE4_MATERIAL_PLANS, EpisodeNavigateMixin
from tasks.dungeon.dungeon import Combat


class _DummyEpisode(EpisodeNavigateMixin):
    pass


def test_episode4_material_plan_mapping():
    assert EPISODE4_MATERIAL_PLANS["CATALYST_RARE_BENEVOLENT"].stage_label == "1-9"
    assert EPISODE4_MATERIAL_PLANS["BREATH_OF_KARMA"].stage_label == "6-10"
    assert EPISODE4_MATERIAL_PLANS["TRACES_OF_BRILLIANCE"].stage_label == "10-9"
    assert EPISODE4_MATERIAL_PLANS["CATALYST_RARE_SECRET"].stage_label == "2-10"


def test_extract_episode_number():
    dummy = _DummyEpisode()

    assert dummy._extract_episode_number("1. 不愉快的第一印象") == 1
    assert dummy._extract_episode_number("10. 终末之地") == 10
    assert dummy._extract_episode_number("第4章") == 4
    assert dummy._extract_episode_number("无有效数字") == 0


def test_episode4_supports_fast_combat():
    episode4 = Combat.__new__(Combat)
    episode4._dungeon_domain = lambda: "Episode4"
    episode4._combat_grade = lambda: "5-5"
    assert episode4._combat_supports_fast_combat() is True

    saint37 = Combat.__new__(Combat)
    saint37._dungeon_domain = lambda: "Saint37"
    saint37._combat_grade = lambda: "3-7"
    assert saint37._combat_supports_fast_combat() is False


def test_episode_target_above_current_should_reset_to_top():
    target = EPISODE4_MATERIAL_PLANS["CATALYST_RARE_OATH"]
    current_stage = 7

    should_reset = current_stage > target.stage
    assert should_reset is True


def test_episode_target_equal_current_should_not_reset_to_top():
    target = EPISODE4_MATERIAL_PLANS["CATALYST_RARE_OATH"]
    current_stage = 5

    should_reset = current_stage > target.stage
    assert should_reset is False


import unittest

class LegacyRuleTests(unittest.TestCase):
    def test_episode4_material_plan_mapping(self):
        test_episode4_material_plan_mapping()

    def test_extract_episode_number(self):
        test_extract_episode_number()

    def test_episode4_supports_fast_combat(self):
        test_episode4_supports_fast_combat()

    def test_episode_target_above_current_should_reset_to_top(self):
        test_episode_target_above_current_should_reset_to_top()

    def test_episode_target_equal_current_should_not_reset_to_top(self):
        test_episode_target_equal_current_should_not_reset_to_top()
