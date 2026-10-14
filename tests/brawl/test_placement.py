import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from module.config import server

server.set_lang("global_cn")

from tasks.brawl.bench import BenchEntry, BenchHero  # noqa: E402
from tasks.brawl.board import BoardCell, BoardObservation  # noqa: E402
from tasks.brawl.field import FieldHero, FieldObservation  # noqa: E402
from tasks.brawl.placement import (  # noqa: E402
    BrawlPlacement, PlacementOutcome, PlacementRequest, capture_placement,
)
from tasks.brawl.rules import Imprint  # noqa: E402
from tasks.brawl.vision import (  # noqa: E402
    BrawlMode, BrawlObservation, BrawlScreen, ObservationError, PreparationPanel,
)
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
            (0, "BrawlPlacementOpen"), (7, "BrawlPlacementOpen"), (14, "BrawlPlacementCell")])

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
                replay = self.replay([step()] + [step(BrawlScreen.FORMATION_SELECTION)] * grid_frames
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
