# ruff: noqa: E402
"""Step-aware Leif budgets and screenshot replay for Dimensional Hunt."""

import unittest
from datetime import timedelta
from unittest.mock import patch

import numpy as np

from module.config import server

server.set_lang("global_cn")

from tasks.dungeon.dungeon import Combat
from tasks.dungeon.repeat import plan_server_repeat_counter_action
from tests.support.hunt import HuntReplay, hunt_image
from tests.support.interaction import InteractionReplay, scene
from tests.support.offline import ControlledClock
from tests.support.resources import ResourceConfig


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


class RepeatPreparationTests(unittest.TestCase):
    """Synthetic tab/configuration observations retain production click timers."""

    def setUp(self):
        server.set_lang("global_cn")
        self.clock = ControlledClock()
        self.clock.__enter__()
        self.addCleanup(self.clock.__exit__, None, None, None)

    @staticmethod
    def battle(*, priority=True, current=2, duration=600, seconds=0.2):
        return scene(
            "REPEAT_COMBAT_MENU_CHECK", "TAB_AUTO_BATTLE_SETTINGS_CHECK",
            "TAB_AUTO_GEAR_SORT_SETTINGS", "REPEAT_COMBAT_TIMES_PLUS",
            "PRIORITIZE_OWNED_STAMINA_ON" if priority else "PRIORITIZE_OWNED_STAMINA_OFF",
            current=current, duration=duration, seconds=seconds,
        )

    @staticmethod
    def gear(*, mode="Extract", selected=True, otherworld=False, seconds=0.2):
        name = "EXTRACT" if mode == "Extract" else "SELL"
        selection = "SELECTED" if selected else "UNSELECTED"
        return scene(
            "REPEAT_COMBAT_MENU_CHECK", "TAB_AUTO_GEAR_SORT_SETTINGS_CHECK",
            "TAB_AUTO_BATTLE_SETTINGS", "REPEAT_START",
            f"GEAR_{name}_{selection}", "HERO_SPEED_FILTER_ON",
            "LEGENDARY_SPEED_FILTER_ON", "FLAT_MAIN_STAT_EXCEPT_SPEED_ON",
            "EXCLUDE_OTHERWORLD_LEGENDARY_ON" if otherworld else "EXCLUDE_OTHERWORLD_LEGENDARY_OFF",
            seconds=seconds,
        )

    def frames(self, *, mode="Extract", duration_frames=None, initial_gear=False):
        if duration_frames is None:
            duration_frames = [self.battle()]
        return (
            [self.gear(mode=mode)] if initial_gear else
            [scene("REPEAT_COMBAT_MENU", seconds=0.2)]
        ) + [
            self.battle(priority=False), self.battle(priority=False),
            self.battle(current=1), self.battle(current=1), self.battle(),
            *duration_frames,
            self.gear(mode=mode, selected=False), self.gear(mode=mode, selected=False),
            *[self.gear(mode=mode)] * 8,
            self.gear(mode=mode, otherworld=True),
            self.gear(mode=mode, otherworld=True),
            self.gear(mode=mode, otherworld=True, seconds=1.2),
            scene("REPEAT_COMBAT_CHECK", seconds=0.2),
        ]

    def replay(self, frames, *, mode="Extract"):
        replay = InteractionReplay(Combat, frames, self.clock)
        task = replay.task
        task.config = ResourceConfig()
        task.config.Combat_Domain = "Hunt"
        task.config.Combat_HuntGrade = "Hell"
        task.config.Combat_BurnoutMode = "None"
        task.config.Combat_RepeatCombatLeifCount = 2
        task.config.Combat_RepeatCombatGearMode = mode
        task.config.Combat_RepeatCombatExcludeOtherworldLegendary = True
        task.match_color = replay.appear
        task._handle_repeat_count_overlay_additional = lambda: False
        task._raise_if_package_full = lambda: None
        task._handle_dungeon_network_error = lambda **kwargs: False
        task.handle_ui_recovery = lambda: False
        task._ocr_repeat_leif_counter = lambda: (replay.frame.get("current", 0), 50)
        replay.duration_reads = []

        def read_duration():
            self.assertTrue(task._is_repeat_battle_tab())
            self.assertEqual(replay.frame["current"], 2)
            self.assertIn("PRIORITIZE_OWNED_STAMINA_ON", replay.frame["buttons"])
            self.assertEqual(replay.clicks("TAB_AUTO_GEAR_SORT_SETTINGS"), [])
            replay.duration_reads.append(replay.index)
            return timedelta(seconds=replay.frame["duration"])

        def ready_slider(key, target, ocr_button, scan_state):
            # Slider recognition is supplied explicitly; this replay exercises
            # the production tab loop, toggles, counters and action intervals.
            self.assertTrue(task._is_repeat_gear_tab())
            return True

        def multi_click(button, n, **kwargs):
            self.assertEqual(n, 1)
            replay.click(button)

        task._ocr_repeat_remaining_duration = read_duration
        task._ensure_repeat_slider = ready_slider
        replay.multi_click = multi_click
        return replay

    def assert_prepared_and_started(self, replay, duration, *, battle_clicks=0):
        task = replay.task
        self.assertTrue(task._prepare_repeat_combat())
        prepared_frame = replay.index
        self.assertTrue(task._is_repeat_gear_tab())
        self.assertIn("EXCLUDE_OTHERWORLD_LEGENDARY_ON", replay.frame["buttons"])
        self.assertEqual(task._repeat_combat_prepared_leif_count, 2)
        self.assertEqual(task._repeat_combat_estimated_duration, timedelta(seconds=duration))
        self.assertEqual(replay.clicks("REPEAT_START"), [])
        self.assertEqual(len(replay.clicks("TAB_AUTO_BATTLE_SETTINGS")), battle_clicks)
        self.assertEqual(replay.duration_reads[-1], replay.clicks("TAB_AUTO_GEAR_SORT_SETTINGS")[0])
        self.assertTrue(task._run_repeat_combat())
        self.assertEqual(replay.clicks("REPEAT_START")[0], prepared_frame)
        replay.assert_retried(self, "REPEAT_START", task.COMBAT_START_INTERVAL_SECONDS)
        self.assertEqual(replay.index, len(replay.frames) - 1)
        self.assertEqual(len(replay.clicks("TAB_AUTO_BATTLE_SETTINGS")), battle_clicks)
        if duration:
            self.assertIsNotNone(task._repeat_combat_expected_finish_at)
        else:
            self.assertIsNone(task._repeat_combat_expected_finish_at)

    def test_read_duration_before_gear_and_start_on_gear_tab_in_both_clients(self):
        for lang in ("cn", "global_cn"):
            for mode in ("Extract", "Sell"):
                with self.subTest(lang=lang, mode=mode):
                    server.set_lang(lang)
                    replay = self.replay(self.frames(mode=mode), mode=mode)
                    self.assert_prepared_and_started(replay, 600)
                    self.assertEqual(replay.duration_reads, [6])
                    self.assertEqual(replay.clicks("TAB_AUTO_GEAR_SORT_SETTINGS"), [6])
                    self.assertEqual(replay.clicks("PRIORITIZE_OWNED_STAMINA_OFF"), [2])
                    self.assertEqual(replay.clicks("REPEAT_COMBAT_TIMES_PLUS"), [4])
                    self.assertEqual(replay.clicks("EXCLUDE_OTHERWORLD_LEGENDARY_OFF"), [16])

    def test_duration_retries_on_battle_tab_then_switches_immediately_when_read(self):
        durations = [self.battle(duration=0)] * 3 + [self.battle(duration=120)]
        replay = self.replay(self.frames(duration_frames=durations))
        self.assert_prepared_and_started(replay, 120)
        self.assertEqual(replay.duration_reads, [6, 7, 8, 9])
        self.assertEqual(replay.clicks("TAB_AUTO_GEAR_SORT_SETTINGS"), [9])

    def test_unreadable_duration_keeps_short_polling_and_starts_from_gear(self):
        # The production dual timer needs both >3 seconds and >8 checks;
        # eight unreadable observations must not pretend the timeout fired.
        durations = [self.battle(duration=0, seconds=0.5)] * 9
        replay = self.replay(self.frames(duration_frames=durations))
        self.assert_prepared_and_started(replay, 0)
        self.assertEqual(replay.duration_reads, list(range(6, 15)))
        self.assertEqual(replay.clicks("TAB_AUTO_GEAR_SORT_SETTINGS"), [14])

    def test_entry_on_gear_tab_returns_to_battle_only_before_preparation(self):
        replay = self.replay(self.frames(initial_gear=True))
        self.assert_prepared_and_started(replay, 600, battle_clicks=1)
        self.assertEqual(replay.clicks("TAB_AUTO_BATTLE_SETTINGS"), [0])

    def test_dropped_gear_tab_click_retries_without_reading_duration_again(self):
        durations = [self.battle()] * 7
        replay = self.replay(self.frames(duration_frames=durations))
        self.assert_prepared_and_started(replay, 600)
        self.assertEqual(replay.duration_reads, [6])
        replay.assert_retried(self, "TAB_AUTO_GEAR_SORT_SETTINGS", 1)
