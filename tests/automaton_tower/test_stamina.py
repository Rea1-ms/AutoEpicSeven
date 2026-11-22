"""Tower resource recognition and leaf-only refill replays."""

import unittest
from unittest.mock import Mock, patch

from module.config import server
from tasks.automaton_tower.automaton_tower import (
    AUTOMATON_TOWER_BATTLE_START,
    STAMINA_CANCEL,
)
from tasks.automaton_tower.policy import ProfileError
from tasks.automaton_tower.profile_store import ProfileStore, StaminaSettings
from tasks.automaton_tower.stamina import (
    REFILL_BUY,
    REFILL_SELECT_LEAF,
    FloorStamina,
    read_leaf_balance,
    refill_selection,
)
from tests.automaton_tower.test_device_profiles import workspace
from tests.support.automaton_tower import CLEAR, MANIFEST, PREPARE, TowerReplay, capture
from tests.support.offline import ControlledClock, fixture_image

LOW = "Scrcpy-20261118-214443-322"
LEAF = "Scrcpy-20261118-214446-585"
SKYSTONE = "Scrcpy-20261118-214532-584"
TOAST = "Scrcpy-20261118-214538-002"
REFILLED = "Scrcpy-20261118-214539-811"
LOADING = "Scrcpy-20261118-214542-792"
BATTLE = "Scrcpy-20261118-214546-109"
CN_PREPARE = "20261115-222014-084"
START = AUTOMATON_TOWER_BATTLE_START.name
BUY = REFILL_BUY.name
SELECT = REFILL_SELECT_LEAF.name


class TowerStaminaTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")

    def test_prepare_reads_balance_and_cost_separately(self):
        with ControlledClock() as clock:
            replay = TowerReplay([capture(LOW), capture(REFILLED), capture(PREPARE), capture(CN_PREPARE)], clock)
            task = replay.task
            self.assertEqual(task._tower_stamina(), 6)
            self.assertEqual(task._floor_stamina(6), FloorStamina(25, 6))
            self.assertIsNone(task._floor_stamina(360))
            replay.screenshot()
            self.assertEqual(task._tower_stamina(), 86)
            self.assertEqual(task._floor_stamina(86), FloorStamina(25, 86))
            replay.screenshot()
            self.assertEqual(task._floor_stamina(task._tower_stamina()), FloorStamina(15, 358))
            replay.screenshot()
            self.assertEqual(task._floor_stamina(task._tower_stamina()), FloorStamina(15, 14391))
        self.assertEqual(FloorStamina.from_text("20/0"), FloorStamina(20, 0))
        self.assertIsNone(FloorStamina.from_text("25/"))

    def test_reselects_leaf_then_buys_once_and_restarts_on_verified_balance(self):
        with ControlledClock() as clock:
            replay = TowerReplay([
                capture(LOW), capture(SKYSTONE, 0.1), capture(SKYSTONE, 0.3),
                capture(SKYSTONE, 1), capture(LEAF, 0.1), capture(LEAF, 0.1),
                capture(LOW, 0.1), capture(TOAST, 0.1), capture(REFILLED, 0.1),
                capture(LOADING, 0.1), capture(BATTLE, 0.1), capture(CLEAR, 0.1),
            ], clock)
            replay.task.stamina_settings = StaminaSettings(True, 115)
            self.assertEqual(replay.task._run_battle(), "clear")
            action_pairs = [(index, name) for index, name in replay.actions if name in (START, SELECT, BUY)]
            self.assertEqual(action_pairs, [(0, START), (1, SELECT), (3, SELECT), (4, BUY), (7, START)])
            self.assertEqual(replay.index, 11)

    def test_opt_out_and_leaf_reserve_stop_without_spending(self):
        for settings, balance, actions in (
            (StaminaSettings(), 116, []),
            (StaminaSettings(True, 116), 116, [(0, START)]),
            (StaminaSettings(True, 0), 0, [(0, START)]),
        ):
            with self.subTest(settings=settings, balance=balance), ControlledClock() as clock:
                replay = TowerReplay([capture(LOW), capture(LEAF)], clock)
                replay.task.stamina_settings = settings
                with patch("tasks.automaton_tower.automaton_tower.read_leaf_balance", return_value=balance):
                    self.assertEqual(replay.task._run_battle(), "stamina")
                self.assertEqual(replay.actions, actions)

    def test_unreadable_balance_or_ambiguous_glow_never_authorizes_purchase(self):
        leaf = fixture_image(MANIFEST, LEAF)
        skystone = fixture_image(MANIFEST, SKYSTONE)
        self.assertEqual(refill_selection(leaf), "leaf")
        self.assertEqual(refill_selection(skystone), "skystone")
        self.assertEqual(read_leaf_balance(leaf, "cn"), 116)
        self.assertEqual(read_leaf_balance(skystone, "cn"), 116)

        double_glow = leaf.copy()
        double_glow[328:418, 896:903] = skystone[328:418, 896:903]
        no_glow = leaf.copy()
        no_glow[328:418, 378:384] = skystone[328:418, 378:384]
        no_balance = leaf.copy()
        no_balance[411:434, 403:588] = 0
        changed_price = leaf.copy()
        changed_price[389:407, 422:442] = 0
        self.assertIsNone(refill_selection(double_glow))
        self.assertIsNone(refill_selection(no_glow))
        self.assertIsNone(read_leaf_balance(no_balance, "cn"))

        for image in (double_glow, no_glow, no_balance, changed_price):
            with ControlledClock() as clock:
                replay = TowerReplay([capture(LOW), capture(LEAF), capture(LEAF, 13)], clock)
                replay.images[LEAF] = image
                replay.task.stamina_settings = StaminaSettings(True)
                self.assertEqual(replay.task._run_battle(), "stamina")
                self.assertEqual(replay.actions, [(0, START)])

        with ControlledClock() as clock:
            replay = TowerReplay([capture(LOW)] + [capture(SKYSTONE, 1.1)] * 12, clock)
            replay.task.stamina_settings = StaminaSettings(True)
            self.assertEqual(replay.task._run_battle(), "stamina")
            self.assertEqual(replay.actions, [(0, START), (1, SELECT), (2, SELECT), (3, SELECT)])

    def test_dropped_purchase_never_retries_or_switches_to_skystone(self):
        with ControlledClock() as clock:
            replay = TowerReplay([
                capture(LOW), capture(LEAF), capture(LEAF, 6), capture(SKYSTONE, 5),
                capture(LOW, 0.1), capture(LOADING, 2),
            ], clock)
            replay.task.stamina_settings = StaminaSettings(True)
            self.assertEqual(replay.task._run_battle(), "stamina")
            self.assertEqual(replay.actions, [(0, START), (1, BUY)])

    def test_stamina_stop_cancels_without_scheduling_retry(self):
        cancel = STAMINA_CANCEL.name
        cases = (
            ("opt_out", [capture(LOW)], []),
            ("reserve", [capture(LOW), capture(LEAF), capture(LOW, 0.1)], [(0, START), (1, cancel)]),
            (
                "retry", [capture(LOW), capture(LEAF), capture(LEAF, 0.1), capture(None, 0.1),
                          capture(LEAF, 3.1), capture(LOW, 0.1)], [(0, START), (1, cancel), (4, cancel)],
            ),
            ("timeout", [capture(LOW), capture(LEAF), capture(LEAF, 5.1)], [(0, START), (1, cancel)]),
            ("already_open", [capture(LEAF), capture(LOW, 0.1)], [(0, cancel)]),
        )
        for name, frames, actions in cases:
            with self.subTest(name=name), ControlledClock() as clock:
                replay = TowerReplay(frames, clock)
                task = replay.task
                task.config.config_name = "offline"
                task._return_to_main = Mock(side_effect=AssertionError("Stop does not require main page"))
                task._next_floor = Mock(side_effect=AssertionError("Do not advance on low stamina"))
                task.handle_popup_confirm = Mock(side_effect=AssertionError("Generic confirm is unsafe"))
                store = ProfileStore(workspace())
                store.set_stamina("offline", StaminaSettings(name != "opt_out", 116))
                with patch("tasks.automaton_tower.automaton_tower.ProfileStore", return_value=store):
                    self.assertFalse(task.run())
                self.assertEqual(replay.actions, actions)
                self.assertEqual(replay.index, len(frames) - 1)
                task.config.task_delay.assert_not_called()
                task.handle_popup_confirm.assert_not_called()
                task._next_floor.assert_not_called()
                task._return_to_main.assert_not_called()

    def test_unsolicited_dialog_and_unsupported_server_never_purchase(self):
        with ControlledClock() as clock:
            replay = TowerReplay([capture(LEAF)], clock)
            replay.task.stamina_settings = StaminaSettings(True)
            self.assertEqual(replay.task._run_battle(), "stamina")
            try:
                server.set_lang("cn")
                self.assertFalse(replay.task._refill_stamina(FloorStamina(25, 6)))
            finally:
                server.set_lang("global_cn")
            self.assertEqual(replay.actions, [])

    def test_settings_are_opt_in_account_specific_and_reject_invalid_reserves(self):
        store = ProfileStore(workspace())
        self.assertEqual(store.stamina("first"), StaminaSettings())
        store.set_stamina("first", StaminaSettings(True, 5))
        self.assertEqual(store.stamina("first"), StaminaSettings(True, 5))
        self.assertEqual(store.stamina("second"), StaminaSettings())
        for invalid in (-1, True, 1.5, "5", None):
            with self.subTest(invalid=invalid), self.assertRaises(ProfileError):
                StaminaSettings(True, invalid)
