"""Saved-run recovery uses explicit component simulations and real recognition inputs."""

import unittest
from collections import deque
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from module.config import server
from module.device.device import Device

server.set_lang("global_cn")

from tasks.brawl.bench import BenchEntry, BenchHero  # noqa: E402
from tasks.brawl.board import BoardCell, FormationGrid  # noqa: E402
from tasks.brawl.formation import FormationOutcome, FormationResult  # noqa: E402
from tasks.brawl.resume import BrawlResume  # noqa: E402
from tasks.brawl.resume_talents import read_resumed_talents  # noqa: E402
from tasks.brawl.roster import (  # noqa: E402
    FormationKind,
    RosterHero,
    RosterState,
)
from tasks.brawl.rules import HEROES, Imprint  # noqa: E402
from tasks.brawl.scanner import (  # noqa: E402
    BenchScanResult,
    BenchSelectionResult,
    ScanOutcome,
    SelectionOutcome,
)
from tasks.brawl.talents import read_empty_talents  # noqa: E402
from tasks.brawl.vision import (  # noqa: E402
    BrawlMode,
    BrawlObservation,
    BrawlScreen,
    BrawlVision,
    ObservationError,
    PreparationPanel,
)
from tests.support.brawl import BrawlReplay, frame  # noqa: E402
from tests.support.offline import ControlledClock  # noqa: E402

PANEL = PreparationPanel(7, 3, 1, 4, 90, 4)
HERO = BenchHero("格伦", Imprint.D)
CELL = (3, 3)
BONUSES = dict.fromkeys(("战士", "骑士", "射手", "魔导士", "盗贼", "精灵师"), 1)


def scan(heroes=(HERO,), panel=PANEL, mode=BrawlMode.NORMAL):
    return BenchScanResult(heroes, panel, mode, ScanOutcome.COMPLETE)


class ResumeIdentityTests(unittest.TestCase):
    """Every scan, selected card and grid response is an explicit simulation."""

    def setUp(self):
        self.task = object.__new__(BrawlResume)
        self.task.device = SimpleNamespace(image=object(), screenshot=Mock(), click_record_clear=Mock())
        self.task.interval_timer = {}

    def identify(self, before, after, restored=None):
        self.task.select = Mock(side_effect=lambda hero, expected: BenchSelectionResult(
            SelectionOutcome.SELECTED, hero, expected.panel, expected.game_mode))
        self.task._grid_action = Mock()
        self.task.scan = Mock(side_effect=[after, restored or before])
        self.task._probe = Mock(side_effect=lambda checked, occupied: occupied)
        return self.task._identify(before, frozenset({CELL}), CELL)

    def test_full_bench_uses_two_verified_swaps_and_restores_all_imprints(self):
        names = tuple(HEROES)[:10]
        before = scan(tuple(BenchHero(name, Imprint.NONE) for name in names))
        after = replace(before, heroes=before.heroes[:-1] + (BenchHero("小春", Imprint.B),))
        hero, restored = self.identify(before, after)
        self.assertEqual(hero, BenchHero("小春", Imprint.B))
        self.assertIs(restored, before)
        self.assertEqual(self.task.select.call_args_list[0].args, (before.heroes[-1], before))
        self.assertEqual(self.task.select.call_args_list[1].args, (hero, after))
        actions = [call.kwargs["action"] for call in self.task._grid_action.call_args_list]
        self.assertEqual([action.kind for action in actions], [FormationKind.SWAP] * 2)
        self.assertEqual([action.target for action in actions], [CELL, CELL])
        self.task._probe.assert_not_called()
        self.assertEqual([call.args[1] for call in self.task._grid_action.call_args_list],
                         [frozenset({CELL})] * 2)

    def test_confirmed_identity_restorations_reset_the_real_click_guard_only_after_verification(self):
        device = self.task.device
        device.click_record = deque(maxlen=30)
        clicks, resets = [], []

        def clear():
            resets.append(len(clicks))
            Device.click_record_clear(device)

        def click(button):
            Device.click_record_add(device, button)
            Device.click_record_check(device)
            clicks.append(str(button))

        device.click_record_clear = clear
        device.click = click
        before = scan()
        after = scan((BenchHero("卡萝", Imprint.D),))
        self.task.select = Mock(side_effect=lambda hero, expected: BenchSelectionResult(
            SelectionOutcome.SELECTED, hero, expected.panel, expected.game_mode))
        self.task.scan = Mock(side_effect=[after, before] * 4)

        def grid(*args, **kwargs):
            # Only grid observations are simulated. The production action
            # recorder and two-button guard must survive four verified pairs.
            self.task._click_formation((1, 1, 2, 2), "BrawlFormationOpen")
            self.task._click_formation((2, 2, 3, 3), "BrawlFormationTarget")

        self.task._grid_action = Mock(side_effect=grid)
        for _ in range(4):
            hero, restored = self.task._identify(before, {CELL}, CELL)
            self.assertEqual(hero, after.heroes[0])
            self.assertIs(restored, before)
        self.assertEqual(clicks, ["BrawlFormationOpen", "BrawlFormationTarget"] * 8)
        self.assertEqual(resets, [4, 8, 12, 16])
        self.assertEqual(list(device.click_record), [])
        # A wrong restored imprint is not progress, even on preparation.
        self.task.scan = Mock(side_effect=[after, scan((BenchHero("格伦", Imprint.C),))])
        with self.assertRaises(ObservationError):
            self.task._identify(before, {CELL}, CELL)
        self.assertEqual(resets, [4, 8, 12, 16])
        self.assertEqual(len(device.click_record), 4)

    def test_empty_bench_withdraws_reads_then_replaces_the_exact_cell(self):
        before = scan(())
        after = scan((BenchHero("卡萝", Imprint.S),), replace(PANEL, deployed=0))
        hero, restored = self.identify(before, after)
        self.assertEqual(hero, after.heroes[0])
        self.assertEqual(restored, before)
        actions = [call.kwargs["action"] for call in self.task._grid_action.call_args_list]
        self.assertEqual([action.kind for action in actions], [FormationKind.WITHDRAW, FormationKind.PLACE])
        self.assertEqual((actions[0].source, actions[1].target), (CELL, CELL))
        self.task._probe.assert_not_called()
        self.assertEqual([call.args[1] for call in self.task._grid_action.call_args_list],
                         [frozenset({CELL}), frozenset()])

    def test_partial_changed_or_nonconserving_scans_cannot_authorize_restoration(self):
        before = scan((HERO, BenchHero("小春", Imprint.B)))
        valid = replace(before, heroes=(HERO, BenchHero("卡萝", Imprint.S)))
        invalid = (
            replace(valid, outcome=ScanOutcome.TIMEOUT),
            replace(valid, panel=replace(PANEL, coins=91)),
            replace(valid, game_mode=BrawlMode.CHALLENGE),
            replace(valid, heroes=(BenchHero("格伦", Imprint.C), valid.heroes[-1])),
            replace(valid, heroes=before.heroes),
            replace(valid, heroes=valid.heroes + (BenchHero("卡琳", Imprint.NONE),)),
        )
        for after in invalid:
            with self.subTest(after=after), self.assertRaises((ObservationError, ValueError)):
                self.identify(before, after)
            self.assertEqual(self.task._grid_action.call_count, 1)
            self.assertEqual(self.task.select.call_count, 1)

    def test_wrong_selected_hero_stops_before_opening_a_grid(self):
        self.task.select = Mock(return_value=BenchSelectionResult(
            SelectionOutcome.SELECTED, BenchHero("卡萝", Imprint.NONE), PANEL, BrawlMode.NORMAL))
        self.task._grid_action = Mock()
        with self.assertRaises(ObservationError):
            self.task._identify(scan(), {CELL}, CELL)
        self.task._grid_action.assert_not_called()

    def test_failed_restore_or_occupied_probe_never_confirms_an_identity(self):
        after = scan((BenchHero("小春", Imprint.B),))
        with self.assertRaises(ObservationError):
            self.identify(scan(), after, replace(scan(), heroes=(BenchHero("格伦", Imprint.C),)))
        self.task._grid_action = Mock(side_effect=ObservationError("wrong cells"))
        self.task.scan = Mock(return_value=after)
        with self.assertRaises(ObservationError):
            self.task._identify(scan(), {CELL}, CELL)
        self.task._grid_action.assert_called_once()

    def test_whole_recovery_commits_names_locations_talents_and_validity_together(self):
        self.task.scan = Mock(return_value=scan())
        self.task._discover = Mock(return_value=frozenset({CELL}))
        self.task._identify = Mock(return_value=(BenchHero("卡萝", Imprint.S), scan()))
        self.task.refresh = Mock(side_effect=lambda: FormationResult(FormationOutcome.READY, self.task.roster))
        result = self.task.recover(BONUSES)
        self.assertEqual(result.outcome, FormationOutcome.READY)
        self.assertTrue(self.task.valid)
        self.assertEqual(result.roster.heroes, (RosterHero("格伦", Imprint.D), RosterHero("卡萝", Imprint.S, CELL)))
        self.assertEqual(self.task.class_bonuses, BONUSES)

    def test_invalid_scan_challenge_duplicate_identity_and_model_error_leave_no_actionable_session(self):
        for before in (replace(scan(), outcome=ScanOutcome.TIMEOUT), scan(mode=BrawlMode.CHALLENGE)):
            with self.subTest(before=before):
                self.task.scan = Mock(return_value=before)
                self.task._discover = Mock()
                self.assertEqual(self.task.recover(BONUSES).outcome, FormationOutcome.FAILED)
                self.task._discover.assert_not_called()
                self.assertFalse(self.task.valid)
        self.task.scan = Mock(return_value=scan())
        self.task._discover = Mock(return_value=frozenset({CELL}))
        self.task._identify = Mock(return_value=(HERO, scan()))
        self.assertEqual(self.task.recover(BONUSES).outcome, FormationOutcome.FAILED)
        self.assertIsNone(self.task.class_bonuses)
        self.task._identify.side_effect = RuntimeError("model unavailable")
        with self.assertRaisesRegex(RuntimeError, "model unavailable"):
            self.task.recover(BONUSES)
        self.assertFalse(self.task.valid)

    def test_full_inventory_cannot_default_an_unread_class_to_zero(self):
        before = scan(tuple(BenchHero(name, Imprint.NONE) for name in tuple(HEROES)[:10]))
        self.task.scan = Mock(return_value=before)
        self.task._discover = Mock(return_value=frozenset({CELL}))
        self.task._identify = Mock(return_value=(BenchHero("小春", Imprint.B), before))
        self.task._execute_step = Mock()
        missing = {name: value for name, value in BONUSES.items() if name != "骑士"}
        result = self.task.recover(missing)
        self.assertEqual(result.outcome, FormationOutcome.FAILED)
        self.assertFalse(self.task.valid)
        self.assertIsNone(self.task.class_bonuses)
        self.task._execute_step.assert_not_called()

    def test_empty_field_fallback_restores_original_positions_and_rejects_failed_restoration(self):
        # Regression for the removed empty-field fallback: talent recovery may
        # no longer withdraw the restored lineup, even with unread sidebars.
        for fail in (False, True):
            with self.subTest(fail=fail):
                self.task.scan = Mock(return_value=scan())
                self.task._discover = Mock(return_value=frozenset({CELL}))
                self.task._identify = Mock(return_value=(BenchHero("卡萝", Imprint.S), scan()))
                if fail:
                    self.task._identify.side_effect = ObservationError("restore not confirmed")
                self.task._execute_step = Mock()
                self.task.refresh = Mock(side_effect=lambda: FormationResult(FormationOutcome.READY, self.task.roster))
                with patch("tasks.brawl.resume_talents.read_resumed_talents") as resumed, \
                        patch("tasks.brawl.talents.read_empty_talents") as empty:
                    result = self.task.recover(BONUSES)
                resumed.assert_not_called()
                empty.assert_not_called()
                self.task._execute_step.assert_not_called()
                self.assertEqual(result.outcome, FormationOutcome.FAILED if fail else FormationOutcome.READY)
                self.assertEqual(self.task.valid, not fail)

    def test_complete_recovery_checks_each_simulated_exchange_and_preserves_the_original_roster(self):
        # Only the device-facing grid, scan and selection components are
        # simulated. Recovery's real identify loop, roster construction, final
        # reconcile and error gating must all consume the resulting states.
        for bench_count in (0, 1, 6, 7, 10):
            with self.subTest(bench_count=bench_count):
                field = (RosterHero("缪斯黎玛", Imprint.NONE, (2, 1)),
                         RosterHero("卡萝", Imprint.D, (3, 1)),
                         RosterHero("萝里娜", Imprint.D, CELL),
                         RosterHero("未知可能性雅卡泰丝", Imprint.C, (4, 3)))
                names = [name for name in HEROES if name not in {hero.name for hero in field}][:bench_count]
                original = RosterState(tuple(RosterHero(name, Imprint.NONE) for name in names) + field,
                                       replace(PANEL, deployed=4), BrawlMode.NORMAL)
                actual = [original]
                actions = []

                def complete_scan(*, actual=actual, **kwargs):
                    state = actual[0]
                    return scan(tuple(BenchHero(hero.name, hero.imprint) for hero in state.heroes
                                      if hero.cell is None), state.panel)

                def selection(hero, expected, *, complete_scan=complete_scan):
                    self.assertEqual(expected, complete_scan())
                    self.assertIn(hero, expected.heroes)
                    return BenchSelectionResult(SelectionOutcome.SELECTED, hero, expected.panel, expected.game_mode)

                def probe(checked, expected, *, actual=actual, complete_scan=complete_scan):
                    self.assertEqual(checked, complete_scan())
                    occupied = frozenset(hero.cell for hero in actual[0].heroes if hero.cell is not None)
                    self.assertEqual(expected, occupied)
                    return occupied

                def grid(checked, expected, *, action, expected_panel, actual=actual, actions=actions, probe=probe):
                    probe(checked, expected)
                    actions.append(action)
                    heroes = list(actual[0].heroes)
                    if action.kind == FormationKind.WITHDRAW:
                        source = next(hero for hero in heroes if hero.cell == action.source)
                        heroes[heroes.index(source)] = replace(source, cell=None)
                    else:
                        incoming = next(hero for hero in heroes if hero.name == action.hero)
                        self.assertIsNone(incoming.cell)
                        target = next((hero for hero in heroes if hero.cell == action.target), None)
                        if action.kind == FormationKind.SWAP:
                            self.assertIsNotNone(target)
                            heroes[heroes.index(target)] = replace(target, cell=None)
                        else:
                            self.assertIsNone(target)
                        heroes[heroes.index(incoming)] = replace(incoming, cell=action.target)
                    deployed = sum(hero.cell is not None for hero in heroes)
                    panel = replace(actual[0].panel, deployed=deployed)
                    self.assertEqual(expected_panel, panel)
                    actual[0] = replace(actual[0], heroes=tuple(heroes), panel=panel)

                self.task.scan = Mock(side_effect=complete_scan)
                self.task.select = Mock(side_effect=selection)
                self.task._discover = Mock(return_value=frozenset(hero.cell for hero in field))
                self.task._probe = Mock(side_effect=probe)
                self.task._grid_action = Mock(side_effect=grid)

                with patch("tasks.brawl.resume_talents.read_resumed_talents") as resumed, \
                        patch("tasks.brawl.talents.read_empty_talents") as empty:
                    result = self.task.recover(BONUSES)
                self.assertEqual(result.outcome, FormationOutcome.READY)
                self.assertTrue(self.task.valid)
                self.assertEqual(set(result.roster.heroes), set(original.heroes))
                self.assertEqual(actual[0], original)
                self.assertEqual(result.roster.panel, original.panel)
                self.assertEqual(len(actions), 8)
                kinds = ([FormationKind.WITHDRAW, FormationKind.PLACE] * 4 if bench_count == 0
                         else [FormationKind.SWAP] * 8)
                self.assertEqual([action.kind for action in actions], kinds)
                resumed.assert_not_called()
                empty.assert_not_called()
                self.task._probe.assert_called_once()
                self.assertEqual(self.task.class_bonuses, BONUSES)

    def test_final_resource_change_rejects_the_entire_recovery(self):
        self.task.scan = Mock(return_value=scan())
        self.task._discover = Mock(return_value=frozenset({CELL}))
        self.task._identify = Mock(return_value=(BenchHero("卡萝", Imprint.S), scan()))

        def changed_refresh():
            self.task._roster = replace(self.task.roster, panel=replace(PANEL, coins=91))
            return FormationResult(FormationOutcome.READY, self.task.roster)

        self.task.refresh = Mock(side_effect=changed_refresh)
        result = self.task.recover(BONUSES)
        self.assertEqual(result.outcome, FormationOutcome.FAILED)
        self.assertFalse(self.task.valid)
        self.assertIsNone(self.task.class_bonuses)

    def test_absent_or_partial_relic_talents_stop_before_scanning_or_clicking(self):
        self.task.scan = Mock()
        self.task._discover = Mock()
        for bonuses in (None, {}, {"骑士": 1}, {**BONUSES, "战士": 2}):
            with self.subTest(bonuses=bonuses):
                result = self.task.recover(bonuses)
                self.assertEqual(result.outcome, FormationOutcome.FAILED)
                self.assertFalse(self.task.valid)
                self.assertIsNone(self.task.class_bonuses)
                self.task.scan.assert_not_called()
                self.task._discover.assert_not_called()


class ResumeDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.clock = ControlledClock()
        self.clock.__enter__()
        self.addCleanup(self.clock.__exit__, None, None, None)

    def replay(self, frames):
        images = [np.full((720, 1280, 3), index, np.uint8) for index in range(len(frames))]
        replay = BrawlReplay(BrawlResume, images, self.clock)
        replay.panel_reads = []
        replay.bench_reads = []

        def current(image):
            return frames[int(image[0, 0, 0])]

        def grid(image, *, expected_source=None, **kwargs):
            value = current(image).grid
            if value is not None and expected_source is not None:
                source = value.selected_source
                if source is None or (source.screen_row, source.screen_column) != expected_source:
                    return None
            return value

        def panel(image):
            replay.panel_reads.append(replay.index)
            return current(image).observation.panel

        def bench(image, **kwargs):
            replay.bench_reads.append(replay.index)
            return SimpleNamespace()

        patches = (
            patch("tasks.brawl.resume.BrawlVision", side_effect=lambda image: SimpleNamespace(
                observe=lambda **kwargs: replace(current(image).observation, panel=None),
                preparation_panel=lambda: panel(image))),
            patch("tasks.brawl.resume._bench_agrees", side_effect=lambda image, *args, **kwargs: current(image).agrees),
            patch("tasks.brawl.resume.read_bench", side_effect=bench),
            patch("tasks.brawl.resume._selected_entry", return_value=BenchEntry(
                HERO.name, HERO.imprint, (1000, 253, 1060, 278), (943, 268, 977, 303))),
            patch("tasks.brawl.resume.read_formation_grid", side_effect=grid),
        )
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        return replay

    @staticmethod
    def state(kind=BrawlScreen.PREPARATION, *, grid=None, panel=PANEL, mode=BrawlMode.NORMAL, agrees=True):
        return SimpleNamespace(observation=BrawlObservation(kind, panel, game_mode=mode), grid=grid, agrees=agrees)

    def test_plus_discovery_cancels_once_and_preserves_the_production_interval(self):
        cell = BoardCell(3, 3, (731, 409))
        grid = FormationGrid((cell,), (cell,), None, (), PANEL)
        replay = self.replay([self.state()] + [self.state(BrawlScreen.FORMATION_SELECTION, grid=grid)] * 12
                             + [self.state()])
        # Other completed actions cannot delay a verified discovery request.
        # Repeated grids still never repeat the single cancellation.
        replay.task.interval_reset(replay.task.ACTION_TIMER, interval=3)
        self.assertEqual(replay.task._discover(scan()), frozenset({CELL}))
        self.assertEqual([action[1] for action in replay.actions], ["BrawlResumeDiscover", "BrawlResumeCancelRound"])
        self.assertEqual([action[0] for action in replay.actions], [0, 1])
        self.assertEqual(replay.panel_reads, [0, 1, 13])
        self.assertEqual(replay.bench_reads, [0, 1, 13])

    def test_unknown_or_incomplete_grids_time_out_without_cancelling_or_moving(self):
        for pending in (self.state(BrawlScreen.UNKNOWN), self.state(BrawlScreen.FORMATION_SELECTION)):
            with self.subTest(pending=pending):
                replay = self.replay([self.state()] + [pending] * 30)
                with self.assertRaises(ObservationError):
                    replay.task._discover(scan())
                self.assertEqual([action[1] for action in replay.actions], ["BrawlResumeDiscover"])

    def test_empty_bench_candidate_search_has_a_fixed_limit_without_repeated_cells(self):
        from collections import deque

        from module.device.device import Device

        replay = self.replay([self.state()] * 120)
        replay.seconds = 0.0625
        guard = SimpleNamespace(click_record=deque(maxlen=30), click_record_clear=Mock())
        record_click = replay.click

        def guarded_click(button):
            # Use the production guard as an unbound method on a plain record.
            # No Device is created and no constructor/connection is invoked.
            guard.click_record.append(str(button))
            Device.click_record_check(guard)
            record_click(button)

        replay.click = guarded_click
        with self.assertRaisesRegex(ObservationError, "No requested field cell"):
            replay.task._discover(scan(()))
        self.assertEqual(len(replay.actions), 28)
        self.assertEqual(len({action[2] for action in replay.actions}), 28)
        self.assertTrue(all(action[1].startswith("BrawlResumeDiscover") for action in replay.actions))
        gaps = [(after[0] - before[0]) * replay.seconds
                for before, after in zip(replay.actions, replay.actions[1:])]
        self.assertTrue(all(0.2 < gap <= 0.25 for gap in gaps), gaps)
        self.assertLess(replay.actions[-1][0] * replay.seconds, 7)
        guard.click_record_clear.assert_not_called()

    def test_empty_bench_selects_each_candidate_once_until_a_positive_source_is_seen(self):
        cell = BoardCell(0, 1, (545, 241))
        grid = FormationGrid((cell,), (cell,), cell, (), PANEL)
        # Four fresh observations span the short probe interval. Once a source
        # appears, repeated grid frames must never authorize another candidate;
        # cancellation follows positive grid evidence without a fixed wait.
        replay = self.replay([self.state()] * 5 + [self.state(BrawlScreen.FORMATION_SELECTION, grid=grid)] * 8
                             + [self.state()])
        replay.seconds = 0.0625
        self.assertEqual(replay.task._discover(scan(())), frozenset({(0, 1)}))
        self.assertEqual([action[0] for action in replay.actions], [0, 4, 5])
        self.assertEqual([action[1] for action in replay.actions],
                         ["BrawlResumeDiscover0_0", "BrawlResumeDiscover0_1", "BrawlResumeCancelRound"])
        self.assertEqual(len({action[2] for action in replay.actions[:2]}), 2)
        self.assertEqual(replay.panel_reads, [0, 4, 5, 13])
        self.assertEqual(replay.bench_reads, [])

    def test_real_occluded_discovery_cancels_once_after_the_tenth_empty_bench_candidate(self):
        selected = frame("resume-discovery-occluded")
        panel = BrawlVision(selected).preparation_panel()
        preparation = np.zeros_like(selected)
        # Only the surrounding preparation frames are simulated. The actual
        # failed capture supplies scene, counters, empty bench and grid markers;
        # neither the grid reader nor its source/count checks are replaced.
        replay = BrawlReplay(BrawlResume, [preparation] * 10 + [selected] * 12 + [preparation], self.clock)
        observe = BrawlVision
        from tasks.brawl.withdraw import _bench_agrees

        def vision(image):
            if image is selected:
                return observe(image)
            return SimpleNamespace(observe=lambda **kwargs: BrawlObservation(
                BrawlScreen.PREPARATION, panel, game_mode=BrawlMode.NORMAL))

        def bench(image, current, before, **kwargs):
            return _bench_agrees(image, current, before, **kwargs) if image is selected else True

        with patch("tasks.brawl.resume.BrawlVision", side_effect=vision), \
                patch("tasks.brawl.resume._bench_agrees", side_effect=bench):
            occupied = replay.task._discover(scan((), panel))
        self.assertEqual(occupied, frozenset({(2, 1), (3, 1), (3, 3), (4, 3)}))
        names = [f"BrawlResumeDiscover{row}_{column}" for row, column in
                 ((0, 0), (0, 1), (0, 2), (0, 3), (1, 0), (1, 1), (1, 2), (1, 3), (2, 0), (2, 1))]
        self.assertEqual([action[1] for action in replay.actions], names + ["BrawlResumeCancelRound"])
        # The first positive grid at frame 10 authorizes cancellation without
        # another fixed delay; repeated selected frames cannot repeat it.
        self.assertEqual([action[0] for action in replay.actions], list(range(10)) + [10])
        self.assertEqual(replay.index, 22)

    def test_unrequested_grid_context_change_and_changed_bench_never_authorize_cancel(self):
        # A plus click that never opens selection has no actionable boundary;
        # template-only waiting must reach the real 12-second deadline. In an
        # actual grid, changed context must instead fail before cancellation.
        cases = ([self.state(BrawlScreen.FORMATION_SELECTION)],
                 [self.state()] + [self.state(panel=replace(PANEL, coins=91))] * 30,
                 [self.state(), self.state(mode=BrawlMode.CHALLENGE)],
                 [self.state()] + [self.state(agrees=False)] * 30,
                 [self.state()] + [self.state(BrawlScreen.FORMATION_SELECTION,
                                             panel=replace(PANEL, coins=91))] * 8,
                 [self.state()] + [self.state(BrawlScreen.FORMATION_SELECTION, agrees=False)] * 8)
        for heroes in ((HERO,), ()):
            for frames in cases:
                with self.subTest(heroes=heroes, frames=frames):
                    replay = self.replay(frames)
                    with self.assertRaises(ObservationError):
                        replay.task._discover(scan(heroes))
                    self.assertTrue(all(action[1].startswith("BrawlResumeDiscover") for action in replay.actions))
                    self.assertLessEqual(len(replay.actions), 1)

    def test_real_right_occluded_source_withdraws_once_without_wait_frame_ocr(self):
        selected = frame("resume-source-right-occluded")
        real_vision = BrawlVision
        panel = real_vision(selected).preparation_panel()
        after_panel = replace(panel, deployed=3)
        preparation = np.zeros_like(selected)
        after = np.ones_like(selected)
        replay = BrawlReplay(BrawlResume, [preparation] + [selected] * 11 + [after], self.clock)
        reads = []
        read_panel = real_vision.preparation_panel

        def vision(image):
            if image is selected:
                return real_vision(image)
            return SimpleNamespace(observe=lambda **kwargs: BrawlObservation(
                BrawlScreen.PREPARATION, after_panel if image is after else panel,
                game_mode=BrawlMode.NORMAL))

        def panel_read(vision):
            reads.append(replay.index)
            return read_panel(vision)

        from tasks.brawl.withdraw import _bench_agrees

        def bench(image, current, checked, **kwargs):
            # The two surrounding preparation frames are explicitly simulated;
            # the captured grid still uses its real positive empty-queue asset.
            return _bench_agrees(image, current, checked, **kwargs) if image is selected else True

        action = SimpleNamespace(kind=FormationKind.WITHDRAW, source=(3, 3), target=None)
        with patch("tasks.brawl.formation.BrawlVision", side_effect=vision), \
                patch("tasks.brawl.formation._bench_agrees", side_effect=bench), \
                patch.object(real_vision, "preparation_panel", panel_read), \
                patch("tasks.brawl.formation.read_bench", side_effect=AssertionError("empty bench needs no OCR")), \
                patch("tasks.brawl.withdraw.read_bench", side_effect=AssertionError("empty bench needs no OCR")):
            replay.task._grid_action(scan((), panel), {(2, 1), (3, 1), (3, 3), (4, 3)},
                                     action=action, expected_panel=after_panel)
        self.assertEqual([action[:2] for action in replay.actions],
                         [(0, "BrawlFormationOpen"), (1, "BrawlFormationWithdraw")])
        self.assertEqual(replay.actions[0][2], (731, 409, 732, 410))
        self.assertEqual(reads, [1])
        self.assertEqual(replay.index, 12)

    def test_preparation_scene_can_be_checked_without_reading_counters(self):
        self.frame = frame("resume-preparation")
        with patch.object(BrawlVision, "preparation_panel", side_effect=AssertionError("unexpected OCR")):
            current = BrawlVision(self.frame).observe(read_panel=False)
        self.assertEqual(current.state, BrawlScreen.PREPARATION)
        self.assertEqual(current.game_mode, BrawlMode.NORMAL)
        self.assertIsNone(current.panel)

    def test_reported_round13_recovery_withdraws_once_from_anchorless_grid(self):
        selected = frame("resume-round13-withdraw")
        real_vision = BrawlVision
        panel = real_vision(selected).preparation_panel()
        after_panel = replace(panel, deployed=7)
        preparation = np.zeros_like(selected)
        after = np.ones_like(selected)
        replay = BrawlReplay(BrawlResume, [preparation] + [selected] * 11 + [after], self.clock)
        reads = []
        read_panel = real_vision.preparation_panel

        def vision(image):
            if image is selected:
                return real_vision(image)
            # Only the selected eight-hero grid is captured. Preparation and
            # the seven-hero return are explicit simulation; this replay does
            # not establish a real completed recovery or the removed identity.
            return SimpleNamespace(observe=lambda **kwargs: BrawlObservation(
                BrawlScreen.PREPARATION, after_panel if image is after else panel,
                game_mode=BrawlMode.NORMAL))

        def panel_read(vision):
            reads.append(replay.index)
            return read_panel(vision)

        from tasks.brawl.withdraw import _bench_agrees

        def bench(image, current, checked, **kwargs):
            return _bench_agrees(image, current, checked, **kwargs) if image is selected else True

        action = SimpleNamespace(kind=FormationKind.WITHDRAW, source=(0, 3), target=None)
        cells = {(0, 3), (1, 3), (2, 0), (2, 3), (3, 0), (3, 3), (4, 3), (5, 3)}
        with patch("tasks.brawl.formation.BrawlVision", side_effect=vision), \
                patch("tasks.brawl.formation._bench_agrees", side_effect=bench), \
                patch.object(real_vision, "preparation_panel", panel_read), \
                patch("tasks.brawl.formation.read_bench", side_effect=AssertionError("empty bench needs no OCR")), \
                patch("tasks.brawl.withdraw.read_bench", side_effect=AssertionError("empty bench needs no OCR")):
            replay.task._grid_action(scan((), panel), cells, action=action, expected_panel=after_panel)
        self.assertIs(replay.image, after)
        self.assertEqual(replay.actions, [(0, "BrawlFormationOpen", (794, 241, 795, 242)),
                                          (1, "BrawlFormationWithdraw", (750, 209, 767, 226))])
        self.assertEqual(reads, [1])
        self.assertEqual(replay.index, 12)


class ResumedTalentTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")

    def normal(self):
        self.frame = frame("normal-round2")
        panel = BrawlVision(self.frame).observe().panel
        return RosterState((RosterHero("卡萝", Imprint.NONE, CELL),), panel, BrawlMode.NORMAL)

    def test_real_later_round_subtracts_only_the_deployed_profession(self):
        roster = self.normal()
        self.assertEqual(read_resumed_talents(self.frame, roster), BONUSES)
        wrong = replace(roster, heroes=(RosterHero("格伦", Imprint.NONE, CELL),))
        with self.assertRaises(ObservationError):
            read_resumed_talents(self.frame, wrong)

    def test_real_glowing_count_above_threshold_is_not_capped(self):
        self.frame = frame("full-swap-after")
        panel = BrawlVision(self.frame).observe().panel
        # Field identities are explicit simulation, not inferred from sprites.
        names = ("小春", "萝里娜", "卡萝", "卡琳", "赛兹", "仆人克萝爱", "黎洁特",
                 "塔玛林尔", "雅卡泰丝", "未知可能性雅卡泰丝")
        roster = RosterState(tuple(RosterHero(name, Imprint.NONE, (index // 4, index % 4))
                                   for index, name in enumerate(names)), panel, BrawlMode.NORMAL)
        self.assertEqual(read_resumed_talents(self.frame, roster), BONUSES)

    def test_current_context_missing_icons_and_dimmed_sidebar_cannot_supply_defaults(self):
        roster = self.normal()
        original = self.frame.copy()
        with self.assertRaises(ObservationError):
            read_resumed_talents(self.frame, replace(roster, panel=replace(roster.panel, coins=56)))
        for area in ((26, 421, 55, 448), (24, 656, 94, 679)):
            self.frame = original.copy()
            x1, y1, x2, y2 = area
            self.frame[y1:y2, x1:x2] = 0
            with self.assertRaises(ObservationError):
                read_resumed_talents(self.frame, roster)

    def test_unread_wrong_denominator_and_impossible_counts_are_rejected(self):
        roster = self.normal()
        values = [(value, .99) for value in ("1/6", "1/6", "1/6", "2/4", "1/6", "1/4")]
        invalid = (values[:-1], [("", .99), *values[1:]], [("1/6", .89), *values[1:]],
                   [("1/4", .99), *values[1:]], [("3/6", .99), *values[1:]])
        for result in invalid:
            with self.subTest(result=result), \
                    patch("tasks.brawl.resume_talents.Ocr.ocr_multi_lines", return_value=result), \
                    self.assertRaises(ObservationError):
                read_resumed_talents(self.frame, roster)

    def test_verified_later_empty_field_can_read_hidden_locked_professions(self):
        self.frame = frame("challenge-opening")
        observed = BrawlVision(self.frame).observe()
        later = replace(observed, panel=replace(observed.panel, round_number=7))
        self.assertEqual(read_empty_talents(self.frame, observation=later), {**BONUSES, "精灵师": 0})
