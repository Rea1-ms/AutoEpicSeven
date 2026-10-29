import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from module.config import server

server.set_lang("global_cn")

from tasks.brawl.bench import BenchEntry, BenchHero, BenchViewport  # noqa: E402
from tasks.brawl.board import BoardCell, BoardObservation, FormationGrid  # noqa: E402
from tasks.brawl.cursor import Cursor  # noqa: E402
from tasks.brawl.field import FieldHero, FieldObservation  # noqa: E402
from tasks.brawl.formation import BrawlFormation, FormationOutcome  # noqa: E402
from tasks.brawl.placement import (  # noqa: E402
    BrawlPlacement,
    PlacementOutcome,
    PlacementRequest,
    capture_placement,
)
from tasks.brawl.roster import (  # noqa: E402
    FormationKind,
    FormationStep,
    RosterHero,
    RosterState,
)
from tasks.brawl.rules import Imprint, RuleInputError  # noqa: E402
from tasks.brawl.scanner import (  # noqa: E402
    BenchScanResult,
    BenchSelectionResult,
    ScanOutcome,
    SelectionOutcome,
)
from tasks.brawl.vision import (  # noqa: E402
    BrawlMode,
    BrawlObservation,
    BrawlScreen,
    BrawlVision,
    ObservationError,
    PreparationPanel,
)
from tasks.brawl.withdraw import WithdrawControl  # noqa: E402
from tests.support.brawl import BrawlReplay, frame  # noqa: E402
from tests.support.offline import ControlledClock  # noqa: E402

PANEL = PreparationPanel(1, 3, 0, 4, 10, 0)
HERO = BenchHero("格伦", Imprint.D)
CELL = BoardCell(3, 3, (731, 409))
BOARD = BoardObservation((CELL,), yellow_destinations=(CELL,))
ENTRY = BenchEntry(HERO.name, HERO.imprint, (1000, 253, 1060, 278), (943, 268, 977, 303))
REQUEST = PlacementRequest(HERO, PANEL, BrawlMode.NORMAL, 3, 3)


def step(state=BrawlScreen.PREPARATION, *, panel=PANEL, mode=BrawlMode.NORMAL,
         entry=ENTRY, board=BOARD, heroes=()):
    observation = BrawlObservation(state, panel, game_mode=mode)
    field = FieldObservation(tuple(heroes), panel)
    return SimpleNamespace(observation=observation, entry=entry, board=board, field=field)


def finished(*, name="格伦", foot=(731, 409), panel=None):
    panel = panel if panel is not None else replace(PANEL, deployed=1)
    return step(panel=panel, heroes=(FieldHero(name, (714, 287, 752, 350), foot),))


class PlacementTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")
        self.clock = ControlledClock()
        self.clock.__enter__()
        self.addCleanup(self.clock.__exit__, None, None, None)

    def replay(self, steps, *, seconds=.5):
        images = []
        for index in range(len(steps)):
            image = np.zeros((720, 1280, 3), np.uint8)
            image[0, 0, 0] = index
            images.append(image)
        replay = BrawlReplay(BrawlPlacement, images, self.clock, seconds)

        def current(image):
            return steps[int(image[0, 0, 0])]

        patches = (
            patch("tasks.brawl.placement.BrawlVision", side_effect=lambda image: SimpleNamespace(
                observe=lambda: current(image).observation,
                preparation_panel=lambda: current(image).observation.panel)),
            patch("tasks.brawl.placement._selected_entry", side_effect=lambda image, observation:
                  current(image).entry),
            patch("tasks.brawl.placement.read_board", side_effect=lambda image, **kwargs:
                  current(image).board),
            patch("tasks.brawl.placement.read_field", side_effect=lambda image, **kwargs:
                  current(image).field),
        )
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.frame = replay.image
        return replay

    def test_captured_glen_deployment_confirms_identity_and_destination(self):
        self.frame = frame("deploy-glen-ready")
        request = capture_placement(self.frame, 3, 3)
        self.assertEqual(request, REQUEST)
        # Captured stable states plus explicit duplicates simulate delayed
        # responses. Clicks themselves never advance the supplied sequence.
        replay = BrawlReplay(BrawlPlacement,
                             ["deploy-glen-ready"] * 7 + ["deploy-glen-grid"] * 2 + ["field-glen"],
                             self.clock)
        result = replay.task.place_selected(request)
        self.assertEqual(result, PlacementOutcome.CONFIRMED)
        self.assertEqual(replay.actions, [(0, "BrawlPlacementOpen", (943, 268, 977, 303)),
                                          (7, "BrawlPlacementCell", (731, 409, 732, 410))])

    def test_capture_rejects_unsupported_hero_full_field_and_nonpreparation(self):
        for name in ("queue-top", "field-five-bench", "deploy-glen-grid", "combat"):
            with self.subTest(sample=name):
                self.frame = frame(name)
                with self.assertRaises(ObservationError):
                    capture_placement(self.frame, 3, 3)
        self.frame = frame("deploy-glen-ready")
        for row, column in ((7, 0), (-1, 0), (0, 4), (True, 0)):
            with self.subTest(row=row, column=column), self.assertRaises(ValueError):
                capture_placement(self.frame, row, column)

    def test_plus_retries_only_until_grid_and_destination_click_is_not_repeated(self):
        grid = step(BrawlScreen.FORMATION_SELECTION)
        replay = self.replay([step()] * 8 + [grid] * 33)
        result = replay.task.place_selected(REQUEST)
        self.assertEqual(result, PlacementOutcome.TIMEOUT)
        self.assertEqual([action[:2] for action in replay.actions], [
            (0, "BrawlPlacementOpen"), (7, "BrawlPlacementOpen"), (8, "BrawlPlacementCell")])

    def test_new_grid_advances_without_a_previous_action_cooldown(self):
        replay = self.replay([step(), step(BrawlScreen.FORMATION_SELECTION), finished()], seconds=.1)
        replay.task.interval_reset(replay.task.ACTION_TIMER, interval=3)
        self.assertEqual(replay.task.place_selected(REQUEST), PlacementOutcome.CONFIRMED)
        self.assertEqual([action[:2] for action in replay.actions], [
            (0, "BrawlPlacementOpen"), (1, "BrawlPlacementCell")])
        self.assertEqual(replay.index, 2)

    def test_count_change_alone_or_wrong_hero_position_cannot_confirm(self):
        for after in (step(panel=replace(PANEL, deployed=1)), finished(name="卡琳"),
                      finished(foot=(607, 409)), finished(panel=PANEL)):
            with self.subTest(after=after):
                replay = self.replay([step()] + [step(BrawlScreen.FORMATION_SELECTION)] * 7
                                     + [after] * 30)
                self.assertEqual(replay.task.place_selected(REQUEST), PlacementOutcome.TIMEOUT)
                self.assertEqual(len(replay.actions), 2)

    def test_unknown_frames_and_delayed_count_keep_the_single_pending_destination(self):
        replay = self.replay([step()] + [step(BrawlScreen.FORMATION_SELECTION)] * 7
                             + [step(BrawlScreen.UNKNOWN, panel=None), step(), finished()])
        self.assertEqual(replay.task.place_selected(REQUEST), PlacementOutcome.CONFIRMED)
        self.assertEqual(len(replay.actions), 2)

    def test_late_positive_destination_exits_before_timeout(self):
        replay = self.replay([step(), step(BrawlScreen.FORMATION_SELECTION), finished()], seconds=13)
        # The grid itself is not success and must not receive a late click.
        self.assertEqual(replay.task.place_selected(REQUEST), PlacementOutcome.TIMEOUT)
        self.assertEqual(len(replay.actions), 1)
        replay = self.replay([step()] + [step(BrawlScreen.FORMATION_SELECTION)] * 7 + [finished()])
        original = replay.screenshot

        def slow_final_frame():
            original()
            if replay.index == 8:
                replay.clock.advance(13)

        replay.screenshot = slow_final_frame
        self.assertEqual(replay.task.place_selected(REQUEST), PlacementOutcome.CONFIRMED)
        self.assertEqual(len(replay.actions), 2)

    def test_other_pages_and_changed_preparation_scope_stop_before_more_actions(self):
        for after, outcome in (
            (step(BrawlScreen.COMBAT), PlacementOutcome.SCENE_CHANGED),
            (step(mode=BrawlMode.CHALLENGE), PlacementOutcome.CONTEXT_CHANGED),
            (step(panel=replace(PANEL, coins=20)), PlacementOutcome.CONTEXT_CHANGED),
            (step(panel=replace(PANEL, round_number=2)), PlacementOutcome.CONTEXT_CHANGED),
            (step(entry=replace(ENTRY, name="卡琳")), PlacementOutcome.SELECTION_CHANGED),
            (step(entry=replace(ENTRY, imprint=Imprint.C)), PlacementOutcome.SELECTION_CHANGED),
        ):
            with self.subTest(after=after):
                replay = self.replay([step(), after])
                self.assertEqual(replay.task.place_selected(REQUEST), outcome)
                self.assertEqual(len(replay.actions), 1)

    def test_occupied_source_or_unverified_yellow_target_never_gets_clicked(self):
        invalid = (
            replace(BOARD, selected_source=CELL),
            replace(BOARD, occupied_swap_targets=(CELL,), yellow_destinations=()),
            replace(BOARD, yellow_destinations=()),
        )
        for board in invalid:
            with self.subTest(board=board):
                replay = self.replay([step()] + [step(BrawlScreen.FORMATION_SELECTION, board=board)] * 8)
                self.assertEqual(replay.task.place_selected(REQUEST), PlacementOutcome.TARGET_UNAVAILABLE)
                self.assertEqual(len(replay.actions), 1)

    def test_missing_grid_or_selection_waits_without_blind_target_click(self):
        for grid in (step(BrawlScreen.FORMATION_SELECTION, board=None),
                     step(BrawlScreen.FORMATION_SELECTION, entry=None)):
            with self.subTest(grid=grid):
                replay = self.replay([step()] + [grid] * 30)
                self.assertEqual(replay.task.place_selected(REQUEST), PlacementOutcome.TIMEOUT)
                self.assertEqual(len(replay.actions), 1)

    def test_grid_reopening_after_unread_preparation_does_not_repeat_placement(self):
        # Returning to preparation invalidates the old grid both before and
        # after the destination click, even when its numeric panel is unread.
        for grid_frames, action_count in ((1, 1), (7, 2)):
            with self.subTest(grid_frames=grid_frames):
                # An unread board prevents the target in the pre-action case;
                # elapsed time is no longer used to keep a valid target idle.
                grid = step(BrawlScreen.FORMATION_SELECTION, board=None) if action_count == 1 else step(
                    BrawlScreen.FORMATION_SELECTION)
                replay = self.replay([step()] + [grid] * grid_frames
                                     + [step(panel=None), step(BrawlScreen.FORMATION_SELECTION)] * 14)
                self.assertEqual(replay.task.place_selected(REQUEST), PlacementOutcome.SCENE_CHANGED)
                self.assertEqual(len(replay.actions), action_count)

    def test_unknown_first_frame_and_stale_grid_never_authorize_a_target_click(self):
        replay = self.replay([step(BrawlScreen.UNKNOWN, panel=None),
                             step(BrawlScreen.FORMATION_SELECTION)])
        self.assertEqual(replay.task.place_selected(REQUEST), PlacementOutcome.SCENE_CHANGED)
        self.assertEqual(replay.actions, [])

    def test_fresh_first_frame_rejects_a_changed_selected_hero(self):
        replay = self.replay([step(), step(entry=replace(ENTRY, name="卡琳"))])
        self.assertEqual(replay.task.place_selected(REQUEST, skip_first_screenshot=False),
                         PlacementOutcome.SELECTION_CHANGED)
        self.assertEqual(replay.actions, [])


class NewGameFormationTests(unittest.TestCase):
    """Explicit simulated observations exercise the production interaction loop.

    No frame in this class claims captured pixels. Readers provide independently
    specified observations, while the real clock, timer, action order and roster
    transition checks remain active. Clicks do not advance the supplied frames.
    """

    def setUp(self):
        server.set_lang("global_cn")
        self.clock = ControlledClock()
        self.clock.__enter__()
        self.addCleanup(self.clock.__exit__, None, None, None)

    @staticmethod
    def scan(heroes=(HERO,), panel=PANEL, outcome=ScanOutcome.COMPLETE):
        return BenchScanResult(heroes, panel, BrawlMode.NORMAL, outcome)

    @staticmethod
    def state_frame(state=BrawlScreen.PREPARATION, *, panel=PANEL, entries=(ENTRY,),
                    grid=None, selected=ENTRY, empty=False, control=None,
                    mode=BrawlMode.NORMAL):
        return SimpleNamespace(
            observation=BrawlObservation(state, panel, game_mode=mode),
            viewport=BenchViewport(entries, panel), grid=grid, selected=selected,
            empty=empty, control=control)

    def replay(self, frames):
        images = []
        for index in range(len(frames)):
            image = np.zeros((720, 1280, 3), np.uint8)
            image[0, 0, 0] = index
            images.append(image)
        replay = BrawlReplay(BrawlFormation, images, self.clock)

        def current(image):
            return frames[int(image[0, 0, 0])]

        def vision(image):
            value = current(image)
            return SimpleNamespace(observe=lambda **kwargs: value.observation,
                                   preparation_panel=lambda: value.observation.panel,
                                   matches=lambda *args, **kwargs: value.empty)

        def grid(image, *, expected_source, **kwargs):
            value = current(image).grid
            # Preserve the production reader's source-proof contract. A frame
            # selecting another source is unreadable for the requested action.
            if value is not None and expected_source is not None:
                source = value.selected_source
                if source is None or (source.screen_row, source.screen_column) != expected_source:
                    return None
            return value

        patches = (
            patch("tasks.brawl.formation.BrawlVision", side_effect=vision),
            patch("tasks.brawl.withdraw.BrawlVision", side_effect=vision),
            patch("tasks.brawl.withdraw.read_bench", side_effect=lambda image, **kwargs:
                  current(image).viewport),
            patch("tasks.brawl.formation.read_bench", side_effect=lambda image, **kwargs:
                  current(image).viewport),
            patch("tasks.brawl.formation._selected_entry", side_effect=lambda image, observation, **kwargs:
                  current(image).selected),
            patch("tasks.brawl.formation.read_formation_grid", side_effect=grid),
            patch("tasks.brawl.formation.read_withdraw_control", side_effect=lambda image, **kwargs:
                  current(image).control),
        )
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.frame = replay.image
        return replay

    @staticmethod
    def initialize(task, heroes, panel=PANEL):
        task._roster = RosterState(heroes, panel, BrawlMode.NORMAL)
        task._scan_result = NewGameFormationTests.scan(
            tuple(BenchHero(hero.name, hero.imprint) for hero in heroes if hero.cell is None), panel)
        task._valid = True
        return task.roster

    def test_probe_cancels_through_fresh_bench_name_and_preserves_interval(self):
        panel = replace(PANEL, deployed=1)
        fresh_entry = replace(ENTRY, name_area=(1010, 300, 1080, 325))
        grid = FormationGrid((CELL,), (CELL,), None, (), panel)
        prep = self.state_frame(panel=panel, entries=(fresh_entry,))
        selected = self.state_frame(BrawlScreen.FORMATION_SELECTION, panel=panel,
                                    entries=(fresh_entry,), grid=grid)
        replay = self.replay([prep] + [selected] * 7 + [prep])
        result = replay.task._grid_action(self.scan(panel=panel), frozenset({(3, 3)}))
        self.assertEqual(result, frozenset({(3, 3)}))
        self.assertEqual(replay.actions, [
            (0, "BrawlFormationOpen", ENTRY.plus_area),
            (1, "BrawlFormationCancelRound", (620, 15, 660, 30))])
        self.assertTrue(all(area != (1205, 280, 1260, 310) for _, _, area in replay.actions))
        self.assertEqual(replay.click_record_clears, [])

    def test_grid_open_retries_before_selection_and_advances_without_extra_delay(self):
        panel = replace(PANEL, deployed=1)
        for empty in (False, True):
            with self.subTest(empty_bench=empty):
                entries = () if empty else (ENTRY,)
                prep = self.state_frame(panel=panel, entries=entries, empty=empty)
                grid = FormationGrid((CELL,), (CELL,), CELL if empty else None, (), panel)
                selected = self.state_frame(BrawlScreen.FORMATION_SELECTION, panel=panel,
                                            entries=entries, empty=empty, grid=grid)
                replay = self.replay([prep] * 8 + [selected, prep])
                checked = self.scan((), panel) if empty else self.scan(panel=panel)
                # A new verified interaction starts immediately, even when the
                # preceding interaction used this same opening control. Only a
                # repeat within this pending interaction owns the cooldown.
                replay.task.interval_reset("BrawlFormationOpen", interval=3)
                self.assertEqual(replay.task._probe(checked, {(3, 3)}), frozenset({(3, 3)}))
                area = (731, 409, 732, 410) if empty else ENTRY.plus_area
                self.assertEqual(replay.actions, [
                    (0, "BrawlFormationOpen", area), (7, "BrawlFormationOpen", area),
                    (8, "BrawlFormationCancelRound", replay.task.ROUND_CANCEL_AREA)])
                self.assertEqual(replay.index, 9)
                self.assertEqual(replay.click_record_clears, [])

    def test_grid_open_retries_keep_the_original_deadline_and_attempt_limit(self):
        panel = replace(PANEL, deployed=1)
        for empty in (False, True):
            with self.subTest(empty_bench=empty):
                prep = self.state_frame(panel=panel, entries=() if empty else (ENTRY,), empty=empty)
                replay = self.replay([prep] * 30)
                checked = self.scan((), panel) if empty else self.scan(panel=panel)
                with patch("tasks.brawl.formation.logger.warning") as warning, \
                        self.assertRaisesRegex(ObservationError, "Formation grid did not open"):
                    replay.task._probe(checked, {(3, 3)})
                self.assertEqual([item[:2] for item in replay.actions], [
                    (0, "BrawlFormationOpen"), (7, "BrawlFormationOpen"), (14, "BrawlFormationOpen")])
                self.assertEqual(replay.index, 25)
                self.assertIn("open_attempts=3", warning.call_args.args[0])
                self.assertEqual(replay.click_record_clears, [])
        for reader in ("read_bench", "_selected_entry"):
            with self.subTest(slow_reader=reader):
                prep = self.state_frame(panel=panel)
                replay = self.replay([prep] * 30)

                def delayed_read(*args, replay=replay,
                                 result=prep.viewport if reader == "read_bench" else prep.selected, **kwargs):
                    if replay.index == 7:
                        self.clock.advance(10)
                    return result

                with patch(f"tasks.brawl.formation.{reader}", side_effect=delayed_read), \
                        self.assertRaises(ObservationError):
                    replay.task._probe(self.scan(panel=panel), {(3, 3)})
                self.assertEqual(replay.actions, [(0, "BrawlFormationOpen", ENTRY.plus_area)])
                self.assertEqual(replay.index, 7)

    def test_grid_open_retry_rechecks_current_context_and_selected_bench_control(self):
        panel = replace(PANEL, deployed=1)
        prep = self.state_frame(panel=panel)
        invalid = [
            self.state_frame(BrawlScreen.UNKNOWN, panel=None),
            self.state_frame(panel=None),
            self.state_frame(panel=panel, mode=None),
            self.state_frame(panel=panel, mode=BrawlMode.CHALLENGE),
            self.state_frame(panel=panel, entries=(replace(ENTRY, imprint=Imprint.C),)),
            self.state_frame(panel=panel, selected=replace(ENTRY, name="卡萝")),
            self.state_frame(panel=panel, selected=None),
        ]
        for field in ("round_number", "level", "deployed", "capacity", "coins", "synergy_tokens"):
            invalid.append(self.state_frame(panel=replace(panel, **{field: getattr(panel, field) + 1})))
        for index, invalid_frame in enumerate(invalid):
            with self.subTest(invalid_evidence=index):
                replay = self.replay([prep] * 7 + [invalid_frame] * 30)
                with self.assertRaises(ObservationError):
                    replay.task._probe(self.scan(panel=panel), {(3, 3)})
                self.assertEqual(replay.actions, [(0, "BrawlFormationOpen", ENTRY.plus_area)])
                self.assertEqual(replay.click_record_clears, [])
        fresh_entry = replace(ENTRY, plus_area=(943, 320, 977, 355))
        moved = self.state_frame(panel=panel, entries=(fresh_entry,), selected=fresh_entry)
        grid = FormationGrid((CELL,), (CELL,), None, (), panel)
        selected = self.state_frame(BrawlScreen.FORMATION_SELECTION, panel=panel, grid=grid,
                                    entries=(fresh_entry,), selected=fresh_entry)
        replay = self.replay([prep] * 7 + [moved, selected, moved])
        self.assertEqual(replay.task._probe(self.scan(panel=panel), {(3, 3)}), frozenset({(3, 3)}))
        self.assertEqual(replay.actions, [
            (0, "BrawlFormationOpen", ENTRY.plus_area),
            (7, "BrawlFormationOpen", fresh_entry.plus_area),
            (8, "BrawlFormationCancelRound", replay.task.ROUND_CANCEL_AREA)])

    def test_seen_grid_identity_ends_open_retries_before_panel_or_mode_is_complete(self):
        panel = replace(PANEL, deployed=1)
        prep = self.state_frame(panel=panel)
        for mode in (BrawlMode.NORMAL, None):
            with self.subTest(incomplete_mode=mode):
                selected = self.state_frame(BrawlScreen.FORMATION_SELECTION, panel=None, mode=mode)
                replay = self.replay([prep] + [selected] * 7 + [prep] * 30)
                with self.assertRaisesRegex(ObservationError, "Selection closed before its requested action"):
                    replay.task._probe(self.scan(panel=panel), {(3, 3)})
                self.assertEqual(replay.actions, [(0, "BrawlFormationOpen", ENTRY.plus_area)])
                self.assertEqual(replay.index, 8)
                grid = FormationGrid((CELL,), (CELL,), None, (), panel)
                readable = self.state_frame(BrawlScreen.FORMATION_SELECTION, panel=panel, grid=grid)
                replay = self.replay([prep, selected, readable, prep])
                replay.seconds = .1
                self.assertEqual(replay.task._probe(self.scan(panel=panel), {(3, 3)}), frozenset({(3, 3)}))
                self.assertEqual(replay.actions, [
                    (0, "BrawlFormationOpen", ENTRY.plus_area),
                    (2, "BrawlFormationCancelRound", replay.task.ROUND_CANCEL_AREA)])
                self.assertEqual(replay.index, 3)

    def test_probe_uses_bench_plus_when_field_has_room_and_rejects_a_selected_source(self):
        panel = replace(PANEL, level=4, deployed=4, capacity=5)
        cells = (CELL, BoardCell(2, 3, (794, 353)), BoardCell(3, 0, (358, 409)),
                 BoardCell(4, 3, (794, 465)))
        expected = frozenset((cell.screen_row, cell.screen_column) for cell in cells)
        for source in (None, CELL):
            with self.subTest(selected_source=source):
                grid = FormationGrid(cells, cells, source, (), panel)
                prep = self.state_frame(panel=panel)
                selected = self.state_frame(BrawlScreen.FORMATION_SELECTION, panel=panel, grid=grid)
                replay = self.replay([prep] + [selected] * 7 + [prep])
                if source is None:
                    self.assertEqual(replay.task._probe(self.scan(panel=panel), expected), expected)
                    self.assertEqual(replay.actions, [
                        (0, "BrawlFormationOpen", ENTRY.plus_area),
                        (1, "BrawlFormationCancelRound", (620, 15, 660, 30))])
                else:
                    with self.assertRaises(ObservationError):
                        replay.task._probe(self.scan(panel=panel), expected)
                    self.assertEqual(len(replay.actions), 1)
                self.assertEqual(replay.actions[0][2], ENTRY.plus_area)

    def test_full_field_probe_uses_plus_and_advances_on_the_first_confirmed_grid(self):
        panel = PreparationPanel(5, 4, 5, 5, 60, 0)
        cells = (BoardCell(0, 0, (420, 241)), BoardCell(1, 3, (731, 297)),
                 BoardCell(3, 0, (358, 409)), CELL, BoardCell(4, 3, (794, 465)))
        expected = frozenset((cell.screen_row, cell.screen_column) for cell in cells)
        grid = FormationGrid(cells, cells, None, (), panel)
        prep = self.state_frame(panel=panel)
        selected = self.state_frame(BrawlScreen.FORMATION_SELECTION, panel=panel, grid=grid)
        replay = self.replay([prep] + [selected] * 3 + [prep])
        # A preceding independent action cannot delay a verified preparation.
        # The new grid authorizes cancellation on the next frame; repeated
        # selected frames still cannot repeat that cancel before its interval.
        replay.task.interval_reset(replay.task.ACTION_TIMER, interval=replay.task.ACTION_INTERVAL)
        self.assertEqual(replay.task._probe(self.scan(panel=panel), expected), expected)
        self.assertEqual(replay.actions, [
            (0, "BrawlFormationOpen", ENTRY.plus_area),
            (1, "BrawlFormationCancelRound", replay.task.ROUND_CANCEL_AREA)])
        self.assertFalse(replay.task.interval_is_reached(
            "BrawlFormationCancelRound", interval=replay.task.ACTION_INTERVAL))

    def test_reported_first_cell_move_uses_real_selection_controls_without_repeating_the_target(self):
        selected = frame("selected-first-cell")
        real_vision = BrawlVision
        panel = real_vision(selected).preparation_panel()
        prep, after = np.zeros_like(selected), np.ones_like(selected)
        replay = BrawlReplay(BrawlFormation, [prep] + [selected] * 4 + [after], self.clock, seconds=.1)
        scan = self.scan((BenchHero("克莉丝缇", Imprint.D),), panel)
        action = FormationStep(FormationKind.MOVE, "萝里娜", (0, 0), (2, 3), None, 0)

        def vision(image):
            if image is selected:
                return real_vision(image)
            return SimpleNamespace(observe=lambda **kwargs: BrawlObservation(
                BrawlScreen.PREPARATION, panel, game_mode=BrawlMode.NORMAL))

        from tasks.brawl.withdraw import _bench_agrees

        def bench(image, current, checked, **kwargs):
            return _bench_agrees(image, current, checked, **kwargs) if image is selected else True

        with patch("tasks.brawl.formation.BrawlVision", side_effect=vision), \
                patch("tasks.brawl.formation._bench_agrees", side_effect=bench):
            replay.task._grid_action(scan, {(0, 0), (1, 3), (3, 0), (3, 3), (4, 3)},
                                     action=action, expected_panel=panel)
        self.assertEqual(replay.actions, [
            (0, "BrawlFormationOpen", (420, 241, 421, 242)),
            (1, "BrawlFormationTarget", (794, 353, 795, 354))])
        self.assertEqual(replay.index, 5)

    def test_probe_empty_bench_requires_positive_empty_marker_before_cancel(self):
        panel = replace(PANEL, deployed=1)
        grid = FormationGrid((CELL,), (CELL,), CELL, (), panel)
        for positive in (True, False):
            with self.subTest(empty_marker=positive):
                prep = self.state_frame(panel=panel, entries=(), empty=True)
                selected = self.state_frame(BrawlScreen.FORMATION_SELECTION, panel=panel,
                                            entries=(), empty=positive, grid=grid)
                replay = self.replay([prep] + [selected] * 7 + [prep])
                if positive:
                    self.assertEqual(replay.task._grid_action(self.scan((), panel), {(3, 3)}),
                                     frozenset({(3, 3)}))
                    self.assertEqual(replay.actions[-1],
                                     (1, "BrawlFormationCancelRound", (620, 15, 660, 30)))
                else:
                    with self.assertRaises(ObservationError):
                        replay.task._grid_action(self.scan((), panel), {(3, 3)})
                    self.assertEqual(len(replay.actions), 1)

    def test_place_waits_for_delayed_count_without_repeating_destination(self):
        action = FormationStep(FormationKind.PLACE, "格伦", None, (3, 3), None, 0)
        grid = FormationGrid((CELL,), (), None, (CELL,), PANEL)
        selected = self.state_frame(BrawlScreen.FORMATION_SELECTION, grid=grid)
        after = replace(PANEL, deployed=1)
        replay = self.replay([self.state_frame()] + [selected] * 7
                             + [self.state_frame(BrawlScreen.UNKNOWN, panel=None),
                                self.state_frame(), self.state_frame(panel=after)])
        replay.task._grid_action(self.scan(), frozenset(), action=action, expected_panel=after)
        self.assertEqual(replay.actions, [
            (0, "BrawlFormationOpen", ENTRY.plus_area),
            (1, "BrawlFormationTarget", (731, 409, 732, 410))])

    def test_move_swap_and_withdraw_use_only_their_positive_controls(self):
        panel = replace(PANEL, deployed=1)
        target = BoardCell(2, 3, (794, 353))
        actions = (
            FormationStep(FormationKind.MOVE, "卡琳", (3, 3), (2, 3), None, 0),
            FormationStep(FormationKind.SWAP, "格伦", None, (3, 3), "卡琳", 0),
            FormationStep(FormationKind.WITHDRAW, "卡琳", (3, 3), None, None, 0),
        )
        for action in actions:
            with self.subTest(kind=action.kind):
                source = None if action.kind == FormationKind.SWAP else CELL
                grid = FormationGrid((CELL, target), (CELL,), source, (target,), panel)
                control = WithdrawControl(CELL, (681, 372, 711, 399), panel)
                selected = self.state_frame(BrawlScreen.FORMATION_SELECTION, panel=panel,
                                            grid=grid, control=control)
                after = replace(panel, deployed=0) if action.kind == FormationKind.WITHDRAW else panel
                replay = self.replay([self.state_frame(panel=panel)] + [selected] * 7
                                     + [self.state_frame(panel=after)])
                replay.task._grid_action(self.scan(panel=panel), {(3, 3)},
                                         action=action, expected_panel=after)
                name = "BrawlFormationWithdraw" if action.kind == FormationKind.WITHDRAW else "BrawlFormationTarget"
                area = control.button_area if action.kind == FormationKind.WITHDRAW else (
                    (731, 409, 732, 410) if action.kind == FormationKind.SWAP else (794, 353, 795, 354))
                self.assertEqual(replay.actions[-1], (1, name, area))
                self.assertEqual(len(replay.actions), 2)

    def test_wrong_source_missing_destination_and_changed_occupation_never_act(self):
        panel = replace(PANEL, deployed=1)
        target = BoardCell(2, 3, (794, 353))
        action = FormationStep(FormationKind.MOVE, "卡琳", (3, 3), (2, 3), None, 0)
        invalid = (
            FormationGrid((CELL, target), (target,), target, (), panel),
            FormationGrid((CELL,), (CELL,), CELL, (), panel),
            FormationGrid((CELL, target), (CELL, target), CELL, (target,), panel),
        )
        for grid in invalid:
            with self.subTest(grid=grid):
                selected = self.state_frame(BrawlScreen.FORMATION_SELECTION, panel=panel, grid=grid)
                replay = self.replay([self.state_frame(panel=panel)] + [selected] * 30)
                with self.assertRaises(ObservationError):
                    replay.task._grid_action(self.scan(panel=panel), {(3, 3)},
                                             action=action, expected_panel=panel)
                self.assertEqual(len(replay.actions), 1)

    def test_move_without_yellow_target_still_requires_post_action_occupation(self):
        panel = PreparationPanel(4, 4, 4, 5, 20, 0)
        target = BoardCell(2, 3, (794, 353))
        others = (BoardCell(1, 3, (731, 297)), BoardCell(3, 0, (358, 409)),
                  BoardCell(4, 3, (794, 465)))
        occupied = (*others, CELL)
        grid = FormationGrid((*occupied, target), occupied, CELL, (), panel)
        entry = replace(ENTRY, name="克莉丝缇")
        ready = self.state_frame(panel=panel, entries=(entry,), selected=entry)
        selected = self.state_frame(BrawlScreen.FORMATION_SELECTION, panel=panel,
                                    entries=(entry,), selected=entry, grid=grid)
        heroes = (RosterHero(entry.name, entry.imprint), RosterHero("卡萝", Imprint.D, (1, 3)),
                  RosterHero("缪斯黎玛", Imprint.NONE, (3, 0)),
                  RosterHero("萝里娜", Imprint.D, (3, 3)),
                  RosterHero("未知可能性雅卡泰丝", Imprint.C, (4, 3)))
        action = FormationStep(FormationKind.MOVE, "萝里娜", (3, 3), (2, 3), None, 0)
        expected = {(1, 3), (3, 0), (2, 3), (4, 3)}
        for actual in (expected, {(1, 3), (3, 0), (3, 3), (4, 3)}):
            with self.subTest(post_action_occupied=actual):
                replay = self.replay([ready] + [selected] * 7 + [ready])
                before = self.initialize(replay.task, heroes, panel)
                after_scan = self.scan((BenchHero(entry.name, entry.imprint),), panel)
                with patch.object(replay.task, "scan", return_value=after_scan), \
                        patch.object(replay.task, "_probe", return_value=actual) as probe:
                    if actual == expected:
                        replay.task._execute_step(action)
                        self.assertTrue(replay.task.valid)
                        self.assertEqual(replay.task.roster.revision, 1)
                        self.assertEqual(next(hero.cell for hero in replay.task.roster.heroes
                                              if hero.name == "萝里娜"), (2, 3))
                        self.assertEqual(replay.click_record_clears, [8])
                    else:
                        with self.assertRaises(RuleInputError):
                            replay.task._execute_step(action)
                        self.assertFalse(replay.task.valid)
                        self.assertIs(replay.task.roster, before)
                        self.assertEqual(replay.click_record_clears, [])
                probe.assert_called_once_with(after_scan, frozenset(expected))
                self.assertEqual(replay.actions, [
                    (0, "BrawlFormationOpen", (731, 409, 732, 410)),
                    (1, "BrawlFormationTarget", (794, 353, 795, 354))])

    def test_unrequested_grid_changed_scene_and_changed_context_stop_without_target(self):
        action = FormationStep(FormationKind.PLACE, "格伦", None, (3, 3), None, 0)
        # Repeated preparation may authorize another opening only after fresh
        # context checks. A changed panel or bench must reject that retry;
        # an actual grid still requires its independent target validation.
        cases = (
            [self.state_frame(BrawlScreen.FORMATION_SELECTION)],
            [self.state_frame(), self.state_frame(BrawlScreen.COMBAT)],
            [self.state_frame(), self.state_frame(mode=BrawlMode.CHALLENGE)],
            [self.state_frame()] + [self.state_frame(panel=replace(PANEL, coins=11))] * 30,
            [self.state_frame()] + [self.state_frame(entries=(replace(ENTRY, imprint=Imprint.C),))] * 30,
            [self.state_frame(), self.state_frame(
                BrawlScreen.FORMATION_SELECTION, panel=replace(PANEL, coins=11))],
            [self.state_frame(), self.state_frame(
                BrawlScreen.FORMATION_SELECTION, entries=(replace(ENTRY, imprint=Imprint.C),))],
        )
        for frames in cases:
            with self.subTest(frames=frames):
                replay = self.replay(frames)
                with self.assertRaises(ObservationError):
                    replay.task._grid_action(self.scan(), set(), action=action,
                                             expected_panel=replace(PANEL, deployed=1))
                self.assertTrue(all(item[1] == "BrawlFormationOpen" for item in replay.actions))
                self.assertLessEqual(len(replay.actions), 1)

    def test_pending_target_timeout_and_reopened_grid_cannot_repeat_or_confirm(self):
        action = FormationStep(FormationKind.PLACE, "格伦", None, (3, 3), None, 0)
        grid = FormationGrid((CELL,), (), None, (CELL,), PANEL)
        selected = self.state_frame(BrawlScreen.FORMATION_SELECTION, grid=grid)
        for tail in ([selected] * 30,
                     [self.state_frame(panel=None), selected] * 15):
            with self.subTest(tail=tail[0].observation.state):
                replay = self.replay([self.state_frame()] + [selected] * 7 + tail)
                with self.assertRaises(ObservationError):
                    replay.task._grid_action(self.scan(), set(), action=action,
                                             expected_panel=replace(PANEL, deployed=1))
                self.assertEqual([item[1] for item in replay.actions],
                                 ["BrawlFormationOpen", "BrawlFormationTarget"])

    def test_probe_checks_bench_again_after_cancellation(self):
        panel = replace(PANEL, deployed=1)
        grid = FormationGrid((CELL,), (CELL,), None, (), panel)
        selected = self.state_frame(BrawlScreen.FORMATION_SELECTION, panel=panel, grid=grid)
        replay = self.replay([self.state_frame(panel=panel)] + [selected] * 7 + [
            self.state_frame(panel=panel, entries=(replace(ENTRY, imprint=Imprint.C),))])
        with self.assertRaises(ObservationError):
            replay.task._grid_action(self.scan(panel=panel), {(3, 3)})
        self.assertEqual(len(replay.actions), 2)

    def test_probe_retries_unconfirmed_cancellation_with_fresh_grid_and_shared_interval(self):
        panel = replace(PANEL, deployed=1)
        for empty in (False, True):
            with self.subTest(empty_bench=empty):
                entries = () if empty else (ENTRY,)
                grid = FormationGrid((CELL,), (CELL,), CELL if empty else None, (), panel)
                prep = self.state_frame(panel=panel, entries=entries, empty=empty)
                selected = self.state_frame(BrawlScreen.FORMATION_SELECTION, panel=panel,
                                            entries=entries, empty=empty, grid=grid)
                # The first cancellation is deliberately ineffective. Frames
                # remain independent of clicks, and only a later preparation
                # confirms return; the production three-second timer is active.
                replay = self.replay([prep] + [selected] * 9 + [prep])
                checked = self.scan((), panel) if empty else self.scan(panel=panel)
                self.assertEqual(replay.task._probe(checked, {(3, 3)}), frozenset({(3, 3)}))
                self.assertEqual([item[:2] for item in replay.actions], [
                    (0, "BrawlFormationOpen"), (1, "BrawlFormationCancelRound"),
                    (8, "BrawlFormationCancelRound")])
                self.assertGreaterEqual((8 - 1) * replay.seconds, replay.task.ACTION_INTERVAL)
                self.assertFalse(replay.task.interval_is_reached(
                    "BrawlFormationCancelRound", interval=replay.task.ACTION_INTERVAL))
                self.assertEqual(replay.click_record_clears, [])

    def test_probe_cancellation_retries_keep_original_deadline_and_log_scene(self):
        panel = replace(PANEL, deployed=1)
        grid = FormationGrid((CELL,), (CELL,), None, (), panel)
        selected = self.state_frame(BrawlScreen.FORMATION_SELECTION, panel=panel, grid=grid)
        replay = self.replay([self.state_frame(panel=panel)] + [selected] * 30)
        with patch("tasks.brawl.formation.logger.warning") as warning, \
                self.assertRaisesRegex(ObservationError, "Formation response timed out"):
            replay.task._probe(self.scan(panel=panel), {(3, 3)})
        self.assertEqual([item[:2] for item in replay.actions], [
            (0, "BrawlFormationOpen"), (1, "BrawlFormationCancelRound"),
            (8, "BrawlFormationCancelRound"), (15, "BrawlFormationCancelRound")])
        self.assertEqual(replay.index, 26)
        message = warning.call_args.args[0]
        self.assertIn("state=formation_selection", message)
        self.assertIn("observation_reason=''", message)
        self.assertIn("cancel_attempts=3", message)

    def test_probe_cancellation_retry_rejects_changed_context_source_or_bench(self):
        panel = replace(PANEL, deployed=1)
        other = BoardCell(2, 3, (794, 353))
        grid = FormationGrid((CELL, other), (CELL,), CELL, (other,), panel)
        prep = self.state_frame(panel=panel, entries=(), empty=True)
        selected = self.state_frame(BrawlScreen.FORMATION_SELECTION, panel=panel,
                                    entries=(), empty=True, grid=grid)
        changed = (
            self.state_frame(BrawlScreen.FORMATION_SELECTION, panel=panel, entries=(), empty=True,
                             grid=replace(grid, selected_source=other)),
            self.state_frame(BrawlScreen.FORMATION_SELECTION, panel=panel, entries=(), empty=True,
                             grid=replace(grid, occupied=(other,))),
            self.state_frame(BrawlScreen.FORMATION_SELECTION, panel=panel, entries=(), empty=True,
                             grid=grid, mode=BrawlMode.CHALLENGE),
            self.state_frame(BrawlScreen.FORMATION_SELECTION, panel=panel, grid=grid),
            self.state_frame(BrawlScreen.FORMATION_SELECTION, panel=replace(panel, coins=11),
                             entries=(), empty=True, grid=grid),
            self.state_frame(BrawlScreen.UNKNOWN, panel=None),
        )
        for index, changed_frame in enumerate(changed):
            with self.subTest(changed_evidence=index):
                replay = self.replay([prep] + [selected] * 7 + [changed_frame] * 30)
                with self.assertRaises(ObservationError):
                    replay.task._probe(self.scan((), panel), {(3, 3)})
                self.assertEqual([item[:2] for item in replay.actions], [
                    (0, "BrawlFormationOpen"), (1, "BrawlFormationCancelRound")])

    def test_probe_cancellation_never_retries_after_unread_preparation(self):
        panel = replace(PANEL, deployed=1)
        grid = FormationGrid((CELL,), (CELL,), None, (), panel)
        selected = self.state_frame(BrawlScreen.FORMATION_SELECTION, panel=panel, grid=grid)
        replay = self.replay([self.state_frame(panel=panel), selected,
                              self.state_frame(panel=None), selected])
        with self.assertRaisesRegex(ObservationError, "An unrequested selection grid appeared"):
            replay.task._probe(self.scan(panel=panel), {(3, 3)})
        self.assertEqual([item[:2] for item in replay.actions], [
            (0, "BrawlFormationOpen"), (1, "BrawlFormationCancelRound")])

    def test_verified_return_allows_next_probe_without_cross_action_delay(self):
        panel = replace(PANEL, deployed=1)
        grid = FormationGrid((CELL,), (CELL,), None, (), panel)
        prep = self.state_frame(panel=panel)
        selected = self.state_frame(BrawlScreen.FORMATION_SELECTION, panel=panel, grid=grid)
        replay = self.replay([prep, selected, prep, selected, prep])
        replay.seconds = .1
        for _ in range(2):
            self.assertEqual(replay.task._probe(self.scan(panel=panel), {(3, 3)}), frozenset({(3, 3)}))
        # Each return positively ends its own read-only interaction. A second
        # probe may start there, while each grid still receives just one cancel.
        self.assertEqual([item[:2] for item in replay.actions], [
            (0, "BrawlFormationOpen"), (1, "BrawlFormationCancelRound"),
            (2, "BrawlFormationOpen"), (3, "BrawlFormationCancelRound")])
        self.assertEqual(replay.index, 4)

    def test_unconfirmed_move_swap_and_withdraw_never_repeat_destination(self):
        panel = replace(PANEL, deployed=1)
        target = BoardCell(2, 3, (794, 353))
        actions = (
            FormationStep(FormationKind.MOVE, "卡琳", (3, 3), (2, 3), None, 0),
            FormationStep(FormationKind.SWAP, "格伦", None, (3, 3), "卡琳", 0),
            FormationStep(FormationKind.WITHDRAW, "卡琳", (3, 3), None, None, 0),
        )
        for action in actions:
            with self.subTest(kind=action.kind):
                source = None if action.kind == FormationKind.SWAP else CELL
                grid = FormationGrid((CELL, target), (CELL,), source, (target,), panel)
                control = WithdrawControl(CELL, (681, 372, 711, 399), panel)
                selected = self.state_frame(BrawlScreen.FORMATION_SELECTION, panel=panel,
                                            grid=grid, control=control)
                replay = self.replay([self.state_frame(panel=panel)] + [selected] * 30)
                after = replace(panel, deployed=0) if action.kind == FormationKind.WITHDRAW else panel
                with self.assertRaisesRegex(ObservationError, "Formation response timed out"):
                    replay.task._grid_action(self.scan(panel=panel), {(3, 3)},
                                             action=action, expected_panel=after)
                name = "BrawlFormationWithdraw" if action.kind == FormationKind.WITHDRAW else "BrawlFormationTarget"
                self.assertEqual([item[:2] for item in replay.actions], [
                    (0, "BrawlFormationOpen"), (1, name)])
                self.assertEqual(replay.index, 26)

    def test_begin_requires_a_complete_empty_first_round_and_clears_old_session(self):
        task = self.replay([self.state_frame()]).task
        for scan in (self.scan(outcome=ScanOutcome.TIMEOUT),
                     self.scan(panel=replace(PANEL, round_number=2)),
                     self.scan(panel=replace(PANEL, deployed=1))):
            self.initialize(task, (RosterHero("格伦", Imprint.D),))
            with self.subTest(scan=scan), patch.object(task, "scan", return_value=scan):
                result = task.begin()
                self.assertEqual(result.outcome, FormationOutcome.FAILED)
                self.assertFalse(task.valid)
                self.assertIsNone(task.roster)
        with patch.object(task, "scan", return_value=self.scan()):
            self.assertEqual(task.begin().outcome, FormationOutcome.READY)
            self.assertTrue(task.valid)
            self.assertEqual(task.roster.heroes, (RosterHero("格伦", Imprint.D),))

    def test_execute_unsupported_body_hero_commits_only_after_full_scan_and_probe(self):
        task = self.replay([self.state_frame()]).task
        before = self.initialize(task, (RosterHero("赛兹", Imprint.B),))
        action = FormationStep(FormationKind.PLACE, "赛兹", None, (3, 3), None, 0)
        after = self.scan((), replace(PANEL, deployed=1))
        calls = []

        def action_done(*args, **kwargs):
            self.assertIs(task.roster, before)
            self.assertFalse(task.valid)
            calls.append("action")

        def scan_done(*args, **kwargs):
            self.assertIs(task.roster, before)
            calls.append("scan")
            return after

        def probe_done(scan, expected):
            self.assertIs(task.roster, before)
            self.assertFalse(task.valid)
            self.assertIs(scan, after)
            self.assertEqual(expected, frozenset({(3, 3)}))
            self.assertEqual(task.device.click_record_clears, [])
            calls.append("probe")
            return {(3, 3)}

        selected = BenchSelectionResult(SelectionOutcome.SELECTED,
                                        BenchHero("赛兹", Imprint.B), PANEL, BrawlMode.NORMAL)
        with patch.object(task, "select", return_value=selected), \
                patch.object(task, "_grid_action", side_effect=action_done), \
                patch.object(task, "scan", side_effect=scan_done), \
                patch.object(task, "_probe", side_effect=probe_done):
            task._execute_step(action)
        self.assertEqual(calls, ["action", "scan", "probe"])
        self.assertEqual(task.roster.heroes, (RosterHero("赛兹", Imprint.B, (3, 3)),))
        self.assertEqual(task.roster.revision, 1)
        self.assertTrue(task.valid)

        self.assertEqual(task.device.click_record_clears, [0])

    def test_execute_scan_or_occupation_failure_keeps_last_roster_invalid(self):
        task = self.replay([self.state_frame()]).task
        action = FormationStep(FormationKind.PLACE, "格伦", None, (3, 3), None, 0)
        selected = BenchSelectionResult(SelectionOutcome.SELECTED, HERO, PANEL, BrawlMode.NORMAL)
        cases = ((self.scan(panel=replace(PANEL, deployed=1)), {(3, 3)}, False),
                 (self.scan((), replace(PANEL, deployed=1)), {(2, 3)}, True),
                 (self.scan((), replace(PANEL, deployed=1), ScanOutcome.TIMEOUT), {(3, 3)}, False))
        for after, cells, probes in cases:
            with self.subTest(after=after, cells=cells):
                before = self.initialize(task, (RosterHero("格伦", Imprint.D),))
                with patch.object(task, "select", return_value=selected), \
                        patch.object(task, "_grid_action"), patch.object(task, "scan", return_value=after), \
                        patch.object(task, "_probe", return_value=cells) as probe:
                    with self.assertRaises(RuleInputError):
                        task._execute_step(action)
                    self.assertEqual(probe.called, probes)
                self.assertIs(task.roster, before)
                self.assertFalse(task.valid)
                self.assertEqual(task.device.click_record_clears, [])

    def test_execute_place_runs_action_and_empty_bench_probe_before_commit(self):
        # Explicit simulated observations connect both production grid loops.
        # Scan and selection are separate tested components; their responses
        # here do not advance screenshots or authorize a roster commit alone.
        hero = BenchHero("赛兹", Imprint.B)
        entry = replace(ENTRY, name=hero.name, imprint=hero.imprint)
        after_panel = replace(PANEL, deployed=1)
        placement = FormationGrid((CELL,), (), None, (), PANEL)
        verification = FormationGrid((CELL,), (CELL,), CELL, (), after_panel)
        ready = self.state_frame(entries=(entry,), selected=entry)
        placing = self.state_frame(BrawlScreen.FORMATION_SELECTION, entries=(entry,),
                                   selected=entry, grid=placement)
        after = self.state_frame(panel=after_panel, entries=(), empty=True)
        checking = self.state_frame(BrawlScreen.FORMATION_SELECTION, panel=after_panel,
                                    entries=(), empty=True, grid=verification)
        replay = self.replay([ready] + [placing] * 7 + [after] * 7 + [checking] * 7 + [after])
        before = self.initialize(replay.task, (RosterHero(hero.name, hero.imprint),))
        action = FormationStep(FormationKind.PLACE, hero.name, None, (3, 3), None, 0)
        selected = BenchSelectionResult(SelectionOutcome.SELECTED, hero, PANEL, BrawlMode.NORMAL)

        def scanned(*args, **kwargs):
            self.assertIs(replay.task.roster, before)
            self.assertEqual(replay.index, 8)
            return self.scan((), after_panel)

        with patch.object(replay.task, "select", return_value=selected), \
                patch.object(replay.task, "scan", side_effect=scanned):
            replay.task._execute_step(action)
        # The returned preparation at frame 8 permits the independent probe.
        # Positive grid evidence then permits immediate cancellation, while
        # the roster still cannot commit until preparation returns at frame 22.
        self.assertEqual([item[:2] for item in replay.actions], [
            (0, "BrawlFormationOpen"), (1, "BrawlFormationTarget"),
            (8, "BrawlFormationOpen"), (15, "BrawlFormationCancelRound")])
        self.assertEqual(replay.index, 22)
        self.assertTrue(replay.task.valid)
        self.assertEqual(replay.task.roster.heroes, (RosterHero(hero.name, hero.imprint, (3, 3)),))

    def test_execute_selection_failure_and_device_exception_invalidate_before_clicks(self):
        task = self.replay([self.state_frame()]).task
        action = FormationStep(FormationKind.PLACE, "格伦", None, (3, 3), None, 0)
        rejected = BenchSelectionResult(SelectionOutcome.BENCH_CHANGED, None, PANEL, BrawlMode.NORMAL)
        before = self.initialize(task, (RosterHero("格伦", Imprint.D),))
        with patch.object(task, "select", return_value=rejected), patch.object(task, "_grid_action") as grid:
            with self.assertRaises(ObservationError):
                task._execute_step(action)
            grid.assert_not_called()
        self.assertFalse(task.valid)
        self.assertIs(task.roster, before)
        self.initialize(task, before.heroes)
        with patch.object(task, "select", side_effect=RuntimeError("simulated device failure")), \
                self.assertRaises(RuntimeError):
            task._execute_step(action)
        self.assertFalse(task.valid)

    def test_refresh_requires_fresh_occupation_and_invalidates_escaped_device_errors(self):
        task = self.replay([self.state_frame()]).task
        panel = replace(PANEL, deployed=1)
        before = self.initialize(task, (RosterHero("格伦", Imprint.D, (3, 3)),), panel)
        with patch.object(task, "scan", return_value=self.scan((), panel)), \
                patch.object(task, "_probe", return_value={(2, 3)}):
            self.assertEqual(task.refresh().outcome, FormationOutcome.FAILED)
        self.assertIs(task.roster, before)
        self.assertFalse(task.valid)
        self.initialize(task, before.heroes, panel)
        with patch.object(task, "scan", side_effect=RuntimeError("simulated device failure")), \
                self.assertRaises(RuntimeError):
            task.refresh()
        self.assertFalse(task.valid)

    def test_arrange_recommendation_commits_each_hero_before_next_selection(self):
        task = self.replay([self.state_frame()]).task
        heroes = (RosterHero("格伦", Imprint.D), RosterHero("卡琳", Imprint.C))
        self.initialize(task, heroes)
        initial = task._scan_result
        first = self.scan((HERO,), replace(PANEL, deployed=1))
        last = self.scan((), replace(PANEL, deployed=2))
        recommendation = SimpleNamespace(placements=(
            SimpleNamespace(name="格伦", row=0, column=3),
            SimpleNamespace(name="卡琳", row=0, column=2)))
        revisions = []

        def select(hero, scan):
            revisions.append(task.roster.revision)
            self.assertFalse(task.valid)
            return BenchSelectionResult(SelectionOutcome.SELECTED, hero, scan.panel, scan.game_mode)

        with patch("tasks.brawl.formation.recommend_lineup", return_value=recommendation), \
                patch.object(task, "scan", side_effect=[initial, first, last]), \
                patch.object(task, "_probe", side_effect=[set(), {(2, 3)}, {(2, 3), (3, 3)}]), \
                patch.object(task, "select", side_effect=select), patch.object(task, "_grid_action") as grid:
            result = task.arrange({})
        self.assertEqual(result.outcome, FormationOutcome.COMPLETE)
        self.assertEqual([item.hero for item in result.steps], ["卡琳", "格伦"])
        self.assertEqual(revisions, [0, 1])
        self.assertEqual(grid.call_count, 2)
        self.assertEqual({hero.name: hero.cell for hero in task.roster.heroes},
                         {"格伦": (3, 3), "卡琳": (2, 3)})
        self.assertTrue(task.valid)

    def test_arrange_failure_retains_only_confirmed_steps_and_requires_explicit_bonuses(self):
        task = self.replay([self.state_frame()]).task
        heroes = (RosterHero("格伦", Imprint.D), RosterHero("卡琳", Imprint.C))
        self.initialize(task, heroes)
        with patch.object(task, "refresh") as refresh, self.assertRaises(RuleInputError):
            task.arrange(None)
        refresh.assert_not_called()
        initial = task._scan_result
        first = self.scan((HERO,), replace(PANEL, deployed=1))
        recommendation = SimpleNamespace(placements=(
            SimpleNamespace(name="格伦", row=0, column=3),
            SimpleNamespace(name="卡琳", row=0, column=2)))

        def select(hero, scan):
            if hero.name == "格伦":
                raise ObservationError("simulated changed bench")
            return BenchSelectionResult(SelectionOutcome.SELECTED, hero, scan.panel, scan.game_mode)

        with patch("tasks.brawl.formation.recommend_lineup", return_value=recommendation), \
                patch.object(task, "scan", side_effect=[initial, first]), \
                patch.object(task, "_probe", side_effect=[set(), {(2, 3)}]), \
                patch.object(task, "select", side_effect=select), patch.object(task, "_grid_action"):
            result = task.arrange({})
        self.assertEqual(result.outcome, FormationOutcome.FAILED)
        self.assertEqual([item.hero for item in result.steps], ["卡琳"])
        self.assertEqual(task.roster.revision, 1)
        self.assertFalse(task.valid)

    def test_immediate_opening_reuses_scan_and_last_selection_but_still_commits(self):
        task = self.replay([self.state_frame()]).task
        last = BenchHero("卡琳", Imprint.C)
        first = self.scan((HERO,), replace(PANEL, deployed=1))
        after = self.scan((), replace(PANEL, deployed=2))
        recommendation = SimpleNamespace(placements=(
            SimpleNamespace(name="格伦", row=0, column=3), SimpleNamespace(name="卡琳", row=0, column=2)))
        cursor = Cursor(last, None, True, False, None, PANEL, BrawlMode.NORMAL)
        selected = BenchSelectionResult(SelectionOutcome.SELECTED, HERO, first.panel, BrawlMode.NORMAL)
        with patch.object(task, "scan", side_effect=[self.scan((HERO, last)), first, after]) as scan, \
                patch("tasks.brawl.formation.recommend_lineup", return_value=recommendation), \
                patch("tasks.brawl.formation.read_cursor", return_value=cursor), \
                patch.object(task, "refresh") as refresh, \
                patch.object(task, "select", return_value=selected) as select, \
                patch.object(task, "_grid_action") as grid, \
                patch.object(task, "_probe", side_effect=[{(2, 3)}, {(2, 3), (3, 3)}]) as probe:
            self.assertEqual(task.begin().outcome, FormationOutcome.READY)
            result = task.arrange({})
        self.assertEqual(result.outcome, FormationOutcome.COMPLETE)
        self.assertEqual(scan.call_count, 3)  # Initial scan and one after each placement.
        refresh.assert_not_called()
        select.assert_called_once_with(HERO, first)  # Later steps cannot reuse opening proof.
        self.assertEqual(grid.call_count, 2)
        self.assertEqual(probe.call_count, 2)
        self.assertEqual(result.roster.heroes, (RosterHero("格伦", Imprint.D, (3, 3)),
                                              RosterHero("卡琳", Imprint.C, (2, 3))))
        self.assertEqual(len(result.steps), 2)
        self.assertIsNone(task._opening_evidence)

    def test_opening_reuse_rejects_new_mutated_expired_and_untracked_frames(self):
        for change in ("new_frame", "mutated", "expired", "fresh_requested", "invalidated", "results"):
            with self.subTest(change=change):
                task = self.replay([self.state_frame()]).task
                with patch.object(task, "scan", return_value=self.scan()):
                    task.begin()
                if change == "new_frame":
                    task.device.image = task.device.image.copy()
                elif change == "mutated":
                    task.device.image[1, 1, 0] = 1
                elif change == "expired":
                    self.clock.advance(task.ACTION_INTERVAL + .1)
                elif change == "invalidated":
                    task.invalidate()
                failed = SimpleNamespace(outcome=FormationOutcome.FAILED)
                with patch.object(task, "refresh", return_value=failed) as refresh:
                    self.assertIs(task.arrange({}, hero_results=(HERO,) if change == "results" else (),
                                               skip_first_screenshot=change != "fresh_requested"), failed)
                refresh.assert_called_once()
                self.assertIsNone(task._opening_evidence)

    def test_opening_last_selection_requires_matching_cursor_and_unexpired_proof(self):
        good = Cursor(HERO, True, True, False, None, PANEL, BrawlMode.NORMAL)
        bad = (replace(good, selected=BenchHero("格伦", Imprint.C)),
               replace(good, bottom=None), replace(good, next_area=(1030, 335, 1265, 375)),
               replace(good, panel=replace(PANEL, coins=11)),
               replace(good, game_mode=BrawlMode.CHALLENGE), replace(good, empty=None), "expired", "not_last")
        for cursor in bad:
            with self.subTest(cursor=cursor):
                task = self.replay([self.state_frame()]).task
                opening = self.scan((HERO, BenchHero("卡琳", Imprint.C))) if cursor == "not_last" else self.scan()
                with patch.object(task, "scan", return_value=opening):
                    task.begin()
                evidence = task._opening_evidence
                if cursor == "expired":
                    self.clock.advance(task.ACTION_INTERVAL + .1)
                    cursor = good
                elif cursor == "not_last":
                    cursor = good
                rejected = BenchSelectionResult(SelectionOutcome.BENCH_CHANGED, None, PANEL, BrawlMode.NORMAL)
                action = FormationStep(FormationKind.PLACE, "格伦", None, (3, 3), None, 0)
                with patch("tasks.brawl.formation.read_cursor", return_value=cursor), \
                        patch.object(task, "select", return_value=rejected) as select, \
                        patch.object(task, "_grid_action") as grid, self.assertRaises(ObservationError):
                    task._execute_step(action, opening_evidence=evidence)
                select.assert_called_once()
                grid.assert_not_called()
                self.assertFalse(task.valid)

    def test_opening_evidence_is_consumed_on_failure_and_never_reused_for_later_steps(self):
        task = self.replay([self.state_frame()]).task
        with patch.object(task, "scan", return_value=self.scan()):
            task.begin()
        with patch("tasks.brawl.formation.recommend_lineup", side_effect=RuntimeError("simulated failure")), \
                self.assertRaises(RuntimeError):
            task.arrange({})
        self.assertIsNone(task._opening_evidence)
        failed = SimpleNamespace(outcome=FormationOutcome.FAILED)
        with patch.object(task, "refresh", return_value=failed) as refresh:
            self.assertIs(task.arrange({}), failed)
        refresh.assert_called_once()

    def test_grid_readers_share_only_current_frame_panel_and_viewport(self):
        action = FormationStep(FormationKind.PLACE, "格伦", None, (3, 3), None, 0)
        grid = FormationGrid((CELL,), (), None, (CELL,), PANEL)
        selected = self.state_frame(BrawlScreen.FORMATION_SELECTION, grid=grid)
        after = replace(PANEL, deployed=1)
        replay = self.replay([self.state_frame()] + [selected] * 7 + [self.state_frame(panel=after)])
        viewports = []

        def read(image, *, observation):
            self.assertEqual(observation.panel, PANEL)
            viewport = BenchViewport((ENTRY,), PANEL)
            viewports.append((id(image), viewport))
            return viewport

        def selected_entry(image, observation, *, viewport):
            self.assertEqual(viewports[-1], (id(image), viewport))
            return ENTRY

        with patch("tasks.brawl.formation.read_bench", side_effect=read) as reader, \
                patch("tasks.brawl.formation._selected_entry", side_effect=selected_entry):
            replay.task._grid_action(self.scan(), set(), action=action, expected_panel=after)
        self.assertEqual(reader.call_count, 2)
        self.assertEqual(len(replay.actions), 2)
