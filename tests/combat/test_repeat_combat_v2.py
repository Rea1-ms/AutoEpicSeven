# ruff: noqa: E402
"""Historical manual assertions adapted to the account-free offline runner."""
import unittest
from module.config import server as _test_server
_test_server.set_lang("global_cn")

from tasks.dungeon.repeat import (CombatRepeatMixin, calculate_server_repeat_leif_count, calculate_server_repeat_stamina_budget, plan_server_repeat_counter_action)

class ManualChecks(unittest.TestCase):
    def test_budget_and_counter_01(self):
        assert calculate_server_repeat_leif_count(79) == 0

    def test_budget_and_counter_02(self):
        assert calculate_server_repeat_leif_count(80) == 1

    def test_budget_and_counter_03(self):
        assert calculate_server_repeat_leif_count(544) == 6

    def test_budget_and_counter_04(self):
        assert calculate_server_repeat_stamina_budget(544, 6, True) == (480, 64)

    def test_budget_and_counter_05(self):
        assert calculate_server_repeat_stamina_budget(544, 6, False) == (0, 544)

    def test_budget_and_counter_06(self):
        assert plan_server_repeat_counter_action(50, 6, 50) == "minimum"

    def test_budget_and_counter_07(self):
        assert plan_server_repeat_counter_action(1, 45, 50) == "maximum"

    def test_budget_and_counter_08(self):
        assert plan_server_repeat_counter_action(10, 12, 50) == "adjust"

    def test_slider_positions(self):
        centers = {
                key: [((point.area[0] + point.area[2] - 1) // 2) for point in value]
                for key, value in (
                    ("equipment", CombatRepeatMixin._repeat_slider_points("equipment_score")),
                    ("hero", CombatRepeatMixin._repeat_slider_points("hero_speed")),
                    ("legendary", CombatRepeatMixin._repeat_slider_points("legendary_speed")),
                )
            }
        assert centers == {
                "equipment": [315, 432, 548, 665, 781, 898],
                "hero": [423, 491, 558],
                "legendary": [780, 825, 870, 915],
            }
