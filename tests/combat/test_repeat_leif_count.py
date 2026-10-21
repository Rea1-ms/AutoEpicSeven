# ruff: noqa: E402
"""Step-aware Leif budgets and screenshot replay for Dimensional Hunt."""

import unittest
from unittest.mock import patch

import numpy as np

from module.config import server

server.set_lang("global_cn")

from tasks.dungeon.dungeon import Combat
from tasks.dungeon.repeat import plan_server_repeat_counter_action
from tests.support.hunt import HuntReplay, hunt_image
from tests.support.offline import ControlledClock


MINIMUM = "20261004-224702-554"
INCREASED = "20261004-224705-176"


class RepeatLeifTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")
        self.clock = ControlledClock()
        self.clock.__enter__()
        self.addCleanup(self.clock.__exit__, None, None, None)

    def replay(self, frames=None, **config):
        if frames is None:
            frames = [np.zeros((720, 1280, 3), dtype=np.uint8)]
        replay = HuntReplay(Combat, frames, self.clock)
        replay.task.config.Combat_HuntBoss = "Ogre"
        for key, value in config.items():
            setattr(replay.task.config, key, value)
        return replay

    def test_supplied_counters_and_spectral_core_resource_are_read_correctly(self):
        for fixture, expected in ((MINIMUM, 2), (INCREASED, 4)):
            with self.subTest(fixture=fixture):
                task = self.replay([hunt_image(fixture)]).task
                self.assertEqual(task._ocr_repeat_leif_counter(), (expected, 50))
                resources = task._ocr_server_repeat_resources()
                self.assertEqual(set(resources), {"spectral_core", "leif"})
                self.assertEqual(resources["spectral_core"].value, 4)
                self.assertEqual(resources["leif"].value, 58)

    def test_dimensional_increase_clicks_once_then_confirms_four(self):
        replay = self.replay([hunt_image(MINIMUM), hunt_image(INCREASED)])
        self.assertFalse(replay.task._ensure_repeat_leif_count(4))
        self.assertEqual([(a[1], a[3]) for a in replay.actions], [("REPEAT_COMBAT_TIMES_PLUS", 1)])
        self.assertFalse(hasattr(replay.task, "_repeat_combat_prepared_leif_count"))
        replay.screenshot()
        self.assertTrue(replay.task._ensure_repeat_leif_count(4))
        self.assertEqual(replay.task._repeat_combat_prepared_leif_count, 4)
        self.assertEqual(len(replay.actions), 1)

    def test_default_and_odd_dimensional_budgets_use_reachable_even_counts(self):
        task = self.replay().task
        self.assertEqual(task._repeat_combat_leif_count(), 2)
        for configured, expected in ((1, 2), (2, 2), (3, 2), (5, 4), (49, 48), (50, 50), (99, 50)):
            with self.subTest(configured=configured):
                task.config.Combat_RepeatCombatLeifCount = configured
                self.assertEqual(task._repeat_combat_leif_count(), expected)

    def test_two_leif_step_applies_to_all_dimensional_hunts_only(self):
        task = self.replay().task
        task.config.Combat_RepeatCombatLeifCount = 5
        task.config.Combat_HuntGrade = "Dimensional"
        for boss in ("Wyvern", "Golem", "Banshee", "Azimanak", "Caides", "Ogre"):
            with self.subTest(boss=boss):
                task.config.Combat_HuntBoss = boss
                self.assertEqual(task._repeat_combat_leif_count(), 4)
        task.config.Combat_HuntBoss = "Wyvern"
        task.config.Combat_HuntGrade = "Hell"
        self.assertEqual(task._repeat_combat_leif_count(), 5)
        task.config.Combat_Domain = "SpiritAltar"
        self.assertEqual(task._repeat_combat_leif_count(), 5)

    def test_odd_request_at_minimum_confirms_two_without_clicking(self):
        replay = self.replay([hunt_image(MINIMUM)])
        for request in (1, 2, 3):
            with self.subTest(request=request):
                self.assertTrue(replay.task._ensure_repeat_leif_count(request))
                self.assertEqual(replay.task._repeat_combat_prepared_leif_count, 2)
        self.assertEqual(replay.actions, [])

    def test_decrease_clicks_once_and_retries_only_after_interval(self):
        replay = self.replay([hunt_image(INCREASED)] * 3 + [hunt_image(MINIMUM)])
        self.assertFalse(replay.task._ensure_repeat_leif_count(2))
        replay.screenshot()
        self.assertFalse(replay.task._ensure_repeat_leif_count(2))
        self.assertEqual(len(replay.actions), 1)
        replay.screenshot()
        self.assertFalse(replay.task._ensure_repeat_leif_count(2))
        replay.screenshot()
        self.assertTrue(replay.task._ensure_repeat_leif_count(2))
        self.assertEqual([(a[0], a[1], a[3]) for a in replay.actions], [
            (0, "REPEAT_COMBAT_TIMES_MINUS", 1),
            (2, "REPEAT_COMBAT_TIMES_MINUS", 1),
        ])

    def test_shortcut_costs_use_clicks_rather_than_leif_difference(self):
        for current, target, expected in ((24, 14, "adjust"), (50, 6, "minimum"), (2, 46, "maximum")):
            with self.subTest(current=current, target=target):
                self.assertEqual(plan_server_repeat_counter_action(current, target, 50, step=2), expected)

    def test_invalid_or_unreachable_counter_never_clicks(self):
        for counter in ((0, 50), (1, 50), (3, 50), (52, 50), (2, 1), (2, 99)):
            with self.subTest(counter=counter):
                replay = self.replay()
                with patch.object(replay.task, "_ocr_repeat_leif_counter", return_value=counter):
                    self.assertFalse(replay.task._ensure_repeat_leif_count(4))
                self.assertEqual(replay.actions, [])

    def test_game_cap_rounds_down_before_confirming_target(self):
        replay = self.replay()
        with patch.object(replay.task, "_ocr_repeat_leif_counter", return_value=(48, 49)):
            self.assertTrue(replay.task._ensure_repeat_leif_count(50))
        self.assertEqual(replay.task._repeat_combat_prepared_leif_count, 48)
        self.assertEqual(replay.actions, [])

    def test_dimensional_ignores_stamina_burnout_budget_and_reservation(self):
        task = self.replay(Combat_BurnoutMode="Burnout", Combat_RepeatCombatLeifCount=5).task
        self.assertEqual(task._server_repeat_target_leif_count(544), 4)
        self.assertEqual(task._server_repeat_stamina_budget(544, 4, True), (0, 544))
        task.config.Combat_HuntBoss = "Wyvern"
        task.config.Combat_HuntGrade = "Hell"
        self.assertEqual(task._server_repeat_target_leif_count(544), 6)
        self.assertEqual(task._server_repeat_stamina_budget(544, 6, True), (480, 64))

    def test_normal_combat_still_increments_one_leif_per_click(self):
        replay = self.replay([hunt_image(MINIMUM)], Combat_HuntBoss="Wyvern")
        self.assertFalse(replay.task._ensure_repeat_leif_count(4))
        self.assertEqual([(a[1], a[3]) for a in replay.actions], [("REPEAT_COMBAT_TIMES_PLUS", 2)])
