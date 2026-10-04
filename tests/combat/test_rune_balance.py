# ruff: noqa: E402
from module.config import server as _test_server

_test_server.set_lang("global_cn")

"""Offline rune planning, inventory and combat integration checks."""
import unittest
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

WORKTREE = Path(__file__).resolve().parents[2]
PROJECT_ROOT = WORKTREE

import module.config.server as server

server.set_lang("global_cn")

from module.config.config_updater import ConfigUpdater
from tasks.dungeon.dungeon import Combat
from tasks.dungeon.rune_balance import (
    RUNE_ELEMENTS, RuneStock, choose_rune_target, rune_sample_yields,
)

SCREENSHOT = Path(r"C:\Users\rea1m\Documents\MuMu共享文件夹\Screenshots\MuMu-20260922-233430-724.png")
EXPECTED = {
    "Dark": RuneStock(117, 267, 4),
    "Light": RuneStock(105, 169, 10),
    "Water": RuneStock(143, 223, 2),
    "Fire": RuneStock(121, 173, 11),
    "Nature": RuneStock(23, 199, 1),
}


def shifted_inventory(image, removed):
    """Simulate disappeared zero-stock cards and the following grid reflow."""
    result = image.copy()
    cards = []
    for index in range(28):
        if index in removed:
            continue
        row, col = divmod(index, 7)
        x, y = 141 + col * 99, 149 + row * 133
        cards.append(image[y:y + 133, x:x + 99].copy())
    result[149:681, 141:834] = 0
    for index, card in enumerate(cards):
        row, col = divmod(index, 7)
        x, y = 141 + col * 99, 149 + row * 133
        result[y:y + 133, x:x + 99] = card
    return result


def combat(config=None):
    obj = Combat.__new__(Combat)
    obj.config = SimpleNamespace(
        Combat_Domain="SpiritAltar",
        Combat_AltarBalance=True,
        Combat_Element="Water",
        Combat_AltarGrade="Hell",
        Combat_HuntGrade="Hell",
        Emulator_PackageName="com.stove.epic7.google",
        task_delay=Mock(),
    )
    if config:
        for key, value in config.items():
            setattr(obj.config, key, value)
    obj._read_rune_inventory = Mock(return_value=EXPECTED)
    return obj


class BalanceTests(unittest.TestCase):
    def test_screenshot_selects_nature_hell(self):
        target = choose_rune_target(EXPECTED)
        self.assertEqual((target.element, target.grade), ("Nature", "Hell"))
        self.assertEqual(target.stock.coverage, Fraction(1, 16))
        self.assertEqual(target.stock.next_character_deficits, (22, 0, 15))

    def test_fractional_coverage_avoids_floor_tie(self):
        stocks = {key: RuneStock(45, 22, 15) for key in RUNE_ELEMENTS}
        stocks["Nature"] = RuneStock(45, 22, 1)
        self.assertEqual(choose_rune_target(stocks).element, "Nature")

    def test_zero_and_direct_use_not_synthesis(self):
        stocks = dict(EXPECTED)
        stocks["Light"] = RuneStock(10000, 10000, 0)
        self.assertEqual(choose_rune_target(stocks).element, "Light")
        self.assertEqual(choose_rune_target(stocks).grade, "Hell")

    def test_difficulty_drop_support(self):
        cases = [
            (RuneStock(0, 22, 16), "Pri"),
            (RuneStock(45, 0, 16), "Hell"),
            (RuneStock(0, 0, 16), "Pri"),
            (RuneStock(45, 22, 0), "Hell"),
            (RuneStock(45, 0, 0), "Hell"),
            (RuneStock(0, 22, 0), "Pri"),
            (RuneStock(0, 0, 0), "Pri"),
        ]
        for stock, grade in cases:
            with self.subTest(stock=stock):
                stocks = {key: RuneStock(4500, 2200, 1600) for key in RUNE_ELEMENTS}
                stocks["Dark"] = stock
                self.assertEqual(choose_rune_target(stocks).grade, grade)

    def test_complete_set_targets_next_character(self):
        stock = RuneStock(90, 44, 32)
        self.assertEqual(stock.coverage, 2)
        self.assertEqual(stock.next_character_deficits, (45, 22, 16))

    def test_invalid_counts_and_partial_inventory_rejected(self):
        for value in (-1, True, 1.5):
            with self.assertRaises(ValueError):
                RuneStock(value, 0, 0)
        with self.assertRaises(ValueError):
            choose_rune_target({"Dark": RuneStock(0, 0, 0)})

    def test_sample_is_average_quantity(self):
        self.assertEqual(
            rune_sample_yields(
                RuneStock(45, 22, 16), RuneStock(45, 100, 22),
                grade="Hell", completed_runs=50,
            ),
            (Fraction(0), Fraction(78, 50), Fraction(6, 50)),
        )

    def test_sample_rejects_consumption_wrong_tier_and_unfinished_count(self):
        for after, grade, count in [
            (RuneStock(44, 22, 16), "Hell", 50),
            (RuneStock(46, 22, 16), "Hell", 50),
            (RuneStock(45, 22, 16), "Hell", 0),
            (RuneStock(45, 22, 16), "unknown", 50),
        ]:
            with self.assertRaises(ValueError):
                rune_sample_yields(RuneStock(45, 22, 16), after, grade=grade, completed_runs=count)



class MeasuredYieldTests(unittest.TestCase):
    @staticmethod
    def stocks(counts):
        stocks = {key: RuneStock(4500, 2200, 1600) for key in RUNE_ELEMENTS}
        stocks["Nature"] = RuneStock(*counts)
        return stocks

    def test_plan_supplies_a_whole_hero_and_is_cheaper_than_upper_only(self):
        from tasks.dungeon.rune_balance import RUNE_DROP_SAMPLES
        target = choose_rune_target(self.stocks((0, 0, 0)))
        self.assertEqual(set(target.expected_runs), {"Pri", "Hell"})
        for tier, need in enumerate((45, 22, 16)):
            supplied = sum(
                runs * RUNE_DROP_SAMPLES[grade][1].counts[tier] / RUNE_DROP_SAMPLES[grade][0]
                for grade, runs in target.expected_runs.items()
            )
            self.assertGreaterEqual(supplied + 1e-8, need)
        self.assertAlmostEqual(target.estimated_stamina, 986.4995864350703, places=6)
        self.assertLess(target.estimated_stamina, 16 / (32 / 363) * 11)

    def test_current_bottleneck_changes_next_difficulty(self):
        low_epic = choose_rune_target(self.stocks((23, 199, 1)))
        low_normal = choose_rune_target(self.stocks((23, 199, 9)))
        self.assertEqual(low_epic.grade, "Hell")
        self.assertEqual(low_normal.grade, "Pri")
        self.assertIn("Hell", low_normal.expected_runs)
        self.assertIn("Pri", low_normal.expected_runs)

    def test_middle_and_upper_remain_available_for_better_samples(self):
        from tasks.dungeon.rune_balance import RUNE_DROP_SAMPLES
        samples = dict(RUNE_DROP_SAMPLES)
        samples["Mid"] = (1, RuneStock(45, 22, 0))
        self.assertEqual(
            choose_rune_target(self.stocks((0, 0, 16)), samples=samples).grade, "Mid"
        )
        samples = dict(RUNE_DROP_SAMPLES)
        samples["High"] = (1, RuneStock(45, 22, 16))
        self.assertEqual(
            choose_rune_target(self.stocks((0, 0, 0)), samples=samples).grade, "High"
        )

    def test_stamina_costs_are_part_of_the_decision(self):
        from tasks.dungeon.burnout import ALTAR_STAMINA_COST
        costs = dict(ALTAR_STAMINA_COST)
        costs["Hell"] = 1000
        self.assertEqual(
            choose_rune_target(self.stocks((45, 22, 0)), stamina_costs=costs).grade,
            "High",
        )

    def test_same_means_with_double_sample_size_give_the_same_plan(self):
        from tasks.dungeon.rune_balance import RUNE_DROP_SAMPLES
        doubled = {
            grade: (runs * 2, RuneStock(*(count * 2 for count in stock.counts)))
            for grade, (runs, stock) in RUNE_DROP_SAMPLES.items()
        }
        first = choose_rune_target(self.stocks((23, 199, 1)))
        second = choose_rune_target(self.stocks((23, 199, 1)), samples=doubled)
        self.assertEqual(first.grade, second.grade)
        self.assertEqual(first.expected_runs, second.expected_runs)
        self.assertAlmostEqual(first.estimated_stamina, second.estimated_stamina)

    def test_invalid_or_infeasible_samples_stop_without_fallback(self):
        from tasks.dungeon.rune_balance import RUNE_DROP_SAMPLES
        invalid = []
        samples = dict(RUNE_DROP_SAMPLES)
        samples["Pri"] = (0, RuneStock(1209, 0, 0))
        invalid.append(samples)
        samples = dict(RUNE_DROP_SAMPLES)
        samples["Pri"] = (302, RuneStock(1209, 1, 0))
        invalid.append(samples)
        invalid.append({grade: (10, RuneStock(0, 0, 0)) for grade in RUNE_DROP_SAMPLES})
        invalid.append({"Pri": RUNE_DROP_SAMPLES["Pri"]})
        for samples in invalid:
            with self.assertRaises(ValueError):
                choose_rune_target(self.stocks((0, 0, 0)), samples=samples)

    def test_invalid_costs_are_rejected(self):
        from tasks.dungeon.burnout import ALTAR_STAMINA_COST
        for value in (0, -1, float("inf"), float("nan"), True):
            costs = dict(ALTAR_STAMINA_COST)
            costs["Hell"] = value
            with self.assertRaises(ValueError):
                choose_rune_target(self.stocks((0, 0, 0)), stamina_costs=costs)




class IntegrationTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")

    def test_target_applies_without_changing_manual_settings(self):
        obj = combat()
        obj._prepare_rune_balance_target()
        self.assertEqual((obj._combat_element(), obj._combat_grade()), ("Nature", "Hell"))
        self.assertEqual((obj.config.Combat_Element, obj.config.Combat_AltarGrade), ("Water", "Hell"))

    def test_disabled_or_other_domain_does_not_read_inventory(self):
        for config, element in (({"Combat_AltarBalance": False}, "Water"), ({"Combat_Domain": "Hunt"}, "Fire")):
            obj = combat(config)
            obj._prepare_rune_balance_target()
            obj._read_rune_inventory.assert_not_called()
            self.assertEqual(obj._combat_element(), element)

    def test_session_preserves_before_snapshot_and_after_refreshes(self):
        obj = combat()
        obj._uses_server_repeat_combat = lambda: True
        obj._prepare_rune_balance_target()
        session = obj._combat_runtime_build()
        self.assertEqual(session["rune_balance_before"]["Nature"], [23, 199, 1])
        self.assertEqual((session["element"], session["grade"]), ("Nature", "Hell"))
        updated = dict(EXPECTED)
        updated["Nature"] = RuneStock(45, 199, 16)
        obj._read_rune_inventory.return_value = updated
        obj._rune_balance_after_settled(session)
        self.assertEqual(obj._combat_element(), "Water")

    def test_running_background_combat_never_reads_inventory(self):
        obj = combat()
        obj.device = SimpleNamespace(app_is_running=lambda: True, image=object())
        obj._prepare_background_repeat_check_context = Mock()
        obj._adopt_existing_background_repeat_combat = Mock()
        obj._combat_runtime_active = lambda: True
        obj._combat_runtime_session = lambda: {"active": True, "state": "result"}
        obj._watch_repeat_combat = Mock(return_value="running")
        obj._delay_running_repeat_combat = Mock()
        self.assertTrue(obj.run())
        obj._read_rune_inventory.assert_not_called()

    def test_cn_configuration_skips_before_accessing_global_assets(self):
        server.set_lang("cn")
        obj = combat({"Emulator_PackageName": "com.zlongame.cn.epicseven"})
        self.assertTrue(obj.run())
        obj._read_rune_inventory.assert_not_called()

    def test_setting_visibility(self):
        updater = ConfigUpdater()
        hidden = updater.get_hidden_args({"Combat": {"Combat": {"Domain": "Hunt"}}})
        self.assertIn("Combat.Combat.AltarBalance", hidden)
        hidden = updater.get_hidden_args({"Combat": {"Combat": {"Domain": "SpiritAltar", "AltarBalance": True}}})
        self.assertIn("Combat.Combat.Element", hidden)
        self.assertIn("Combat.Combat.AltarGrade", hidden)
        self.assertNotIn("Combat.Combat.AltarBalance", hidden)
