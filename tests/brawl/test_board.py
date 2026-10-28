import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from module.config import server

server.set_lang("global_cn")

from tasks.brawl.assets.assets_brawl import (  # noqa: E402
    FORMATION_GRID_CHECK,
    FORMATION_MOVE_SOURCE,
    FORMATION_SOURCE_SELECTED,
)
from tasks.brawl.assets.assets_brawl_actions import SWAP_GRID_ANCHOR  # noqa: E402
from tasks.brawl.board import (  # noqa: E402
    logical_to_screen,
    read_board,
    read_formation_grid,
    screen_to_logical,
)
from tasks.brawl.vision import (  # noqa: E402
    BrawlScreen,
    BrawlVision,
    ObservationError,
    PreparationPanel,
)
from tasks.brawl.withdraw import read_withdraw_control  # noqa: E402
from tests.support.brawl import frame  # noqa: E402


class BoardRecognitionTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")

    def test_placement_grid_preserves_seven_staggered_screen_rows(self):
        self.frame = frame("formation-place")
        board = read_board(self.frame)
        self.assertIsNotNone(board)
        self.assertEqual(len(board.cells), 28)
        self.assertIsNone(board.selected_source)
        self.assertEqual(board.occupied_swap_targets, ())
        self.assertEqual(board.yellow_destinations, board.cells)
        self.assertEqual([(cell.screen_row, cell.screen_column) for cell in board.cells],
                         [(row, column) for row in range(7) for column in range(4)])
        self.assertEqual([board.cells[index].center for index in (0, 3, 4, 7, 24, 27)],
                         [(420, 241), (794, 241), (358, 297), (731, 297), (420, 577), (794, 577)])
        self.assertEqual(len({cell.center for cell in board.cells}), 28)

    def test_move_grid_locates_the_selected_source_cell(self):
        # Retain the registered case ID, but correct its former interpretation:
        # the lone blue control is an occupied destination, not a selected source.
        self.frame = frame("formation-move")
        board = read_board(self.frame)
        self.assertIsNotNone(board)
        self.assertEqual(len(board.cells), 28)
        self.assertIsNone(board.selected_source)
        self.assertEqual(board.occupied_swap_targets, (board.cells[15],))
        self.assertEqual(board.occupied_swap_targets[0].center, (731, 409))

    def test_multiple_blue_controls_are_occupied_swap_targets(self):
        for name, indices in (("swap-two", (15, 19)), ("swap-four", (11, 15, 19, 23))):
            with self.subTest(sample=name):
                self.frame = frame(name)
                board = read_board(self.frame)
                self.assertIsNotNone(board)
                self.assertEqual(len(board.cells), 28)
                self.assertIsNone(board.selected_source)
                self.assertEqual(board.occupied_swap_targets,
                                 tuple(board.cells[index] for index in indices))
                self.assertTrue(all(cell not in board.yellow_destinations
                                    for cell in board.occupied_swap_targets))

    def test_green_control_identifies_source_separately_from_swap_target(self):
        self.frame = frame("move-source")
        board = read_board(self.frame)
        self.assertIsNotNone(board)
        self.assertEqual(board.selected_source, board.cells[15])
        self.assertEqual(board.occupied_swap_targets, (board.cells[11],))
        self.assertNotIn(board.selected_source, board.occupied_swap_targets)
        self.assertNotIn(board.selected_source, board.yellow_destinations)

    def test_occluded_three_hero_grid_is_not_completed_from_neighbors(self):
        # This is a real partially occluded capture: the upper occupied hero
        # covers adjoining outlines. More markers do not establish all 28 cells.
        self.frame = frame("swap-three")
        self.assertIsNone(read_board(self.frame))

    def test_non_grid_scenes_and_deployment_animation_have_no_targets(self):
        for name in ("normal-deployed", "formation-deploy-animation", "formation", "income-tooltip",
                     "normal-opening", "normal-combat", "normal-victory15", "transmit"):
            with self.subTest(sample=name):
                self.frame = frame(name)
                self.assertIsNone(read_board(self.frame))

    def test_occluded_grid_cannot_publish_unverified_destinations(self):
        original = frame("formation-place")
        for area in ((350, 196, 485, 250), (290, 485, 865, 615), (290, 390, 425, 445)):
            self.frame = original.copy()
            x1, y1, x2, y2 = area
            self.frame[y1:y2, x1:x2] = 0
            with self.subTest(area=area):
                self.assertIsNone(read_board(self.frame))

    def test_missing_or_duplicate_move_markers_do_not_invent_a_source(self):
        original = frame("formation-move")
        left, top, right, bottom = FORMATION_SOURCE_SELECTED.area
        self.frame = original.copy()
        self.frame[top:bottom, left:right] = 0
        board = read_board(self.frame)
        self.assertIsNotNone(board)
        self.assertIsNone(board.selected_source)
        self.assertEqual(board.occupied_swap_targets, ())
        self.assertNotIn(board.cells[15], board.yellow_destinations)
        # An erased control does not establish that its cell is empty. Conversely
        # a pasted arrow over a fully yellow cell must not invent an occupied one.
        self.frame = original.copy()
        self.frame[top:bottom, left - 249:right - 249] = original[top:bottom, left:right]
        self.assertIsNone(read_board(self.frame))

    def test_prior_asset_offsets_do_not_move_this_frames_grid(self):
        self.frame = frame("formation-move")
        expected = read_board(self.frame)
        self.assertIsNotNone(expected)
        try:
            FORMATION_GRID_CHECK.load_offset((12, -8))
            FORMATION_SOURCE_SELECTED.load_offset((-10, 9))
            FORMATION_MOVE_SOURCE.load_offset((10, -9))
            self.assertEqual(read_board(self.frame), expected)
        finally:
            FORMATION_GRID_CHECK.load_offset((0, 0))
            FORMATION_SOURCE_SELECTED.load_offset((0, 0))
            FORMATION_MOVE_SOURCE.load_offset((0, 0))
        # A translated capture must move both the grid and occupied destination.
        # Poisoning cached offsets alone would still pass for fixed coordinates.
        original = self.frame
        self.frame = np.zeros_like(original)
        self.frame[:-8, 12:] = original[8:, :-12]
        shifted = read_board(self.frame)
        self.assertIsNotNone(shifted)
        self.assertEqual([cell.center for cell in shifted.cells],
                         [(cell.center[0] + 12, cell.center[1] - 8) for cell in expected.cells])
        self.assertIsNone(shifted.selected_source)
        self.assertEqual(shifted.occupied_swap_targets[0].center, (743, 401))

    def test_invalid_image_and_unsupported_server_are_rejected(self):
        for invalid in (None, np.zeros((360, 640, 3), dtype=np.uint8),
                        np.zeros((720, 1280, 3), dtype=np.float32)):
            with self.subTest(shape=getattr(invalid, "shape", None)), self.assertRaises(ValueError):
                read_board(invalid)
        self.frame = frame("formation-place")
        try:
            server.set_lang("global_en")
            with self.assertRaises(ValueError):
                read_board(self.frame)
        finally:
            server.set_lang("global_cn")


class FormationGridTests(unittest.TestCase):
    def test_reported_occluded_yellow_target_uses_the_complete_occupied_set(self):
        self.frame = frame("move-target-yellow-occluded")
        vision = BrawlVision(self.frame)
        observation = replace(vision.observe(), panel=vision.preparation_panel())
        grid = read_formation_grid(self.frame, observation=observation,
                                   expected_source=(3, 3), expected_target=(2, 3))
        self.assertIsNotNone(grid)
        self.assertEqual(grid.selected_source, grid.cells[15])
        self.assertEqual(grid.occupied, tuple(grid.cells[index] for index in (7, 12, 15, 19)))
        self.assertNotIn(grid.cells[11], grid.yellow_destinations)
        self.assertIn(grid.cells[11], grid.empty_destinations)
        self.assertEqual(len(grid.empty_destinations), 24)
        self.assertIsNone(read_formation_grid(self.frame, observation=observation,
                                             expected_source=(3, 3), expected_target=(3, 3)))
        # Losing an occupied marker must still invalidate the complete set;
        # this change does not infer an empty cell from one missing arrow.
        hidden = self.frame.copy()
        hidden[250:298, 706:756] = 0
        self.assertIsNone(read_formation_grid(hidden, observation=observation,
                                             expected_source=(3, 3), expected_target=(2, 3)))

    def setUp(self):
        server.set_lang("global_cn")

    def test_grid_and_withdraw_reuse_only_the_explicit_same_frame_panel(self):
        for sample, reader in (("formation-move", read_formation_grid),
                               ("withdraw-grid", read_withdraw_control)):
            with self.subTest(sample=sample):
                self.frame = frame(sample)
                vision = BrawlVision(self.frame)
                observation = vision.observe()
                panel = vision.preparation_panel()
                expected = reader(self.frame, observation=observation)
                self.assertIsNotNone(expected)
                supplied = replace(observation, panel=panel)
                with patch.object(BrawlVision, "preparation_panel",
                                  side_effect=AssertionError("repeated panel read")):
                    self.assertEqual(reader(self.frame, observation=supplied), expected)
                    self.assertIsNone(reader(self.frame, observation=replace(
                        supplied, panel=replace(panel, deployed=0))))
                    self.assertIsNone(reader(self.frame, observation=replace(
                        supplied, state=BrawlScreen.PREPARATION)))
                    self.assertIsNone(reader(self.frame, observation=replace(supplied, game_mode=None)))
                    with self.assertRaises(ValueError):
                        reader(np.zeros((360, 640, 3), np.uint8), observation=supplied)
                # Each independent call without a panel must still perform its
                # own OCR. There is no implicit cache from the successful read.
                with patch.object(BrawlVision, "preparation_panel", return_value=panel) as numeric:
                    self.assertEqual(reader(self.frame, observation=observation), expected)
                    numeric.assert_called_once_with()
                with patch.object(BrawlVision, "preparation_panel",
                                  side_effect=ObservationError("unread")):
                    self.assertIsNone(reader(self.frame, observation=observation))

    def test_logical_front_faces_right_and_mapping_is_bijective(self):
        self.assertEqual([logical_to_screen(row, 3) for row in range(4)],
                         [(3, 3), (3, 2), (3, 1), (3, 0)])
        self.assertEqual([logical_to_screen(0, column) for column in range(7)],
                         [(row, 3) for row in range(7)])
        mapped = {logical_to_screen(row, column) for row in range(4) for column in range(7)}
        self.assertEqual(mapped, {(row, column) for row in range(7) for column in range(4)})
        for row in range(4):
            for column in range(7):
                self.assertEqual(screen_to_logical(*logical_to_screen(row, column)), (row, column))

    def test_invalid_logical_screen_and_requested_cells_are_rejected(self):
        for row, column in ((-1, 0), (4, 0), (0, 7), (True, 0), (0, 1.0)):
            with self.subTest(logical=(row, column)), self.assertRaises(ValueError):
                logical_to_screen(row, column)
        for row, column in ((-1, 0), (7, 0), (0, 4), (0, False), (1.0, 0)):
            with self.subTest(screen=(row, column)), self.assertRaises(ValueError):
                screen_to_logical(row, column)
        for requested in ((7, 0), (0, 4), (True, 0), [0, 0], (0,)):
            for keyword in ("expected_source", "expected_target"):
                with self.subTest(requested=requested, keyword=keyword), self.assertRaises(ValueError):
                    read_formation_grid(None, **{keyword: requested})

    def test_captured_empty_and_occupied_grids_have_complete_occupation(self):
        for name, indices in (("formation-place", ()), ("formation-move", (15,)),
                              ("swap-two", (15, 19)), ("swap-four", (11, 15, 19, 23))):
            with self.subTest(sample=name):
                self.frame = frame(name)
                grid = read_formation_grid(self.frame)
                self.assertIsNotNone(grid)
                self.assertEqual(len(grid.cells), 28)
                self.assertEqual(grid.occupied, tuple(grid.cells[index] for index in indices))
                self.assertEqual(grid.panel.deployed, len(indices))
                self.assertIsNone(grid.selected_source)
                self.assertTrue(set(grid.occupied).isdisjoint(grid.yellow_destinations))
                if not indices:
                    self.assertEqual(grid.yellow_destinations, grid.cells)

    def test_captured_occlusion_preserves_occupied_and_only_proven_destinations(self):
        self.frame = frame("swap-three")
        self.assertIsNone(read_board(self.frame))
        grid = read_formation_grid(self.frame, expected_target=(3, 3))
        self.assertIsNotNone(grid)
        self.assertEqual(grid.occupied, tuple(grid.cells[index] for index in (11, 15, 19)))
        self.assertEqual(grid.panel.deployed, 3)
        self.assertNotIn(grid.cells[7], grid.yellow_destinations)
        self.assertIn(grid.cells[0], grid.yellow_destinations)
        self.assertIn(grid.cells[7], grid.empty_destinations)
        self.assertIsNotNone(read_formation_grid(self.frame, expected_target=(1, 3)))

    def test_captured_full_grid_exposes_ten_swap_targets_and_no_empty_cells(self):
        self.frame = frame("full-swap-grid")
        grid = read_formation_grid(self.frame, expected_target=(6, 3))
        self.assertIsNotNone(grid)
        self.assertEqual(grid.panel.deployed, 10)
        self.assertEqual(grid.occupied,
                         tuple(grid.cells[index] for index in (3, 7, 11, 14, 15, 17, 19, 22, 23, 27)))
        self.assertEqual(grid.yellow_destinations, ())
        self.assertEqual(grid.empty_destinations, ())
        self.assertIsNone(read_formation_grid(self.frame, expected_target=(0, 0)))

    def test_full_five_swap_uses_controls_when_both_edge_anchors_are_absent(self):
        # Explicitly simulate five occupied cells from the captured full grid.
        # Unlike the ten-hero capture, neither static edge anchor survives.
        self.frame = frame("full-swap-grid").copy()
        kept = {(1, 3), (2, 3), (3, 2), (3, 3), (4, 3)}
        for marker in FORMATION_SOURCE_SELECTED.match_multi_template(self.frame, similarity=.9):
            left, top, right, bottom = marker.area
            center = ((left + right) / 2, (top + bottom) / 2 + 23)
            cell = min(((r, c) for r in range(7) for c in range(4)),
                       key=lambda rc: abs(420 + rc[1] * 124.5 - (rc[0] % 2) * 62.25 - center[0])
                       + abs(241 + rc[0] * 56 - center[1]))
            if cell not in kept:
                self.frame[top - 1:bottom + 1, left - 1:right + 1] = 0
        for asset in (FORMATION_GRID_CHECK, SWAP_GRID_ANCHOR):
            left, top, right, bottom = asset.search
            self.frame[top:bottom, left:right] = 0
        observation = replace(BrawlVision(self.frame).observe(read_panel=False),
                              panel=PreparationPanel(5, 4, 5, 5, 60, 0))
        self.assertEqual(observation.state, BrawlScreen.FORMATION_SELECTION)
        grid = read_formation_grid(self.frame, observation=observation, expected_target=(1, 3))
        self.assertIsNotNone(grid)
        self.assertEqual({(c.screen_row, c.screen_column) for c in grid.occupied}, kept)
        self.assertEqual(grid.empty_destinations, ())
        self.assertIsNone(read_formation_grid(self.frame, observation=observation, expected_target=(0, 0)))
        self.assertIsNone(read_formation_grid(self.frame, observation=replace(
            observation, panel=replace(observation.panel, deployed=6))))
        shifted = np.roll(self.frame, shift=(3, 4), axis=(0, 1))
        translated = read_formation_grid(shifted, observation=observation)
        self.assertIsNotNone(translated)
        self.assertEqual(translated.cells[0].center,
                         (grid.cells[0].center[0] + 4, grid.cells[0].center[1] + 3))
        marker = FORMATION_SOURCE_SELECTED.match_multi_template(self.frame, similarity=.9)[0]
        left, top, right, bottom = marker.area
        moved = self.frame[top:bottom, left:right].copy()
        self.frame[top:bottom, left:right + 6] = 0
        self.frame[top:bottom, left + 6:right + 6] = moved
        self.assertIsNone(read_formation_grid(self.frame, observation=observation))

    def test_reported_recovery_restore_locates_seven_heroes_without_fixed_anchors(self):
        self.frame = frame("resume-round13-restore")
        vision = BrawlVision(self.frame)
        observation = replace(vision.observe(read_panel=False), panel=vision.preparation_panel())
        self.assertEqual(observation.state, BrawlScreen.FORMATION_SELECTION)
        self.assertEqual(observation.panel, PreparationPanel(13, 7, 7, 8, 160, 0))
        for asset in (FORMATION_GRID_CHECK, SWAP_GRID_ANCHOR, FORMATION_MOVE_SOURCE):
            self.assertEqual(asset.match_multi_template(self.frame, similarity=.9), [])
        self.assertEqual(len(FORMATION_SOURCE_SELECTED.match_multi_template(self.frame, similarity=.9)), 7)
        grid = read_formation_grid(self.frame, observation=observation, expected_target=(0, 3))
        self.assertIsNotNone(grid)
        self.assertIsNone(grid.selected_source)
        self.assertEqual({(cell.screen_row, cell.screen_column) for cell in grid.occupied},
                         {(1, 3), (2, 0), (2, 3), (3, 0), (3, 3), (4, 3), (5, 3)})
        self.assertEqual(grid.cells[3].center, (794, 241))
        self.assertIn(grid.cells[3], grid.empty_destinations)
        self.assertEqual(len(grid.empty_destinations), 21)

    def test_partial_anchorless_grid_requires_complete_consistent_current_controls(self):
        original = frame("resume-round13-restore")
        vision = BrawlVision(original)
        observation = replace(vision.observe(read_panel=False), panel=vision.preparation_panel())
        marker = FORMATION_SOURCE_SELECTED.match_multi_template(original, similarity=.9)[0]
        left, top, right, bottom = marker.area
        # These are explicit corruptions of the captured seven-hero selection,
        # not additional real game states. Missing or duplicated arrows cannot
        # turn an occupied cell into permission to restore a bench hero there.
        for corrupt in ("missing", "duplicate", "shifted"):
            with self.subTest(corrupt=corrupt):
                self.frame = original.copy()
                if corrupt == "duplicate":
                    self.frame[top:bottom, left - 249:right - 249] = original[top:bottom, left:right]
                else:
                    self.frame[top:bottom, left:right] = 0
                    if corrupt == "shifted":
                        self.frame[top:bottom, left + 6:right + 6] = original[top:bottom, left:right]
                self.assertIsNone(read_formation_grid(self.frame, observation=observation,
                                                     expected_target=(0, 3)))
        for panel in (replace(observation.panel, deployed=6), replace(observation.panel, capacity=9)):
            with self.subTest(panel=panel):
                self.assertIsNone(read_formation_grid(original, observation=replace(observation, panel=panel)))
        self.frame = original.copy()
        for control in FORMATION_SOURCE_SELECTED.match_multi_template(original, similarity=.9):
            x1, y1, x2, y2 = control.area
            self.frame[y1:y2, x1:x2] = 0
        self.assertIsNone(read_formation_grid(self.frame, observation=replace(
            observation, panel=replace(observation.panel, deployed=0))))
        # A shared small translation is explicitly simulated. The current
        # controls must relocate the grid rather than reuse old pixel offsets.
        self.frame = np.roll(original, shift=(3, 4), axis=(0, 1))
        translated = read_formation_grid(self.frame, observation=observation, expected_target=(0, 3))
        self.assertIsNotNone(translated)
        self.assertEqual(translated.cells[3].center, (798, 244))

    def test_reported_first_cell_selection_keeps_the_green_source_and_all_five_heroes(self):
        self.frame = frame("selected-first-cell")
        with patch.object(BrawlVision, "preparation_panel", side_effect=AssertionError("scene needs no OCR")):
            observation = BrawlVision(self.frame).observe(read_panel=False)
        self.assertEqual(observation.state, BrawlScreen.FORMATION_SELECTION)
        panel = BrawlVision(self.frame).preparation_panel()
        self.assertEqual(panel, PreparationPanel(5, 4, 5, 5, 60, 0))
        grid = read_formation_grid(self.frame, observation=replace(observation, panel=panel),
                                   expected_source=(0, 0), expected_target=(2, 3))
        self.assertIsNotNone(grid)
        self.assertEqual(grid.selected_source, grid.cells[0])
        self.assertEqual(grid.occupied, tuple(grid.cells[index] for index in (0, 7, 12, 15, 19)))
        self.assertIn(grid.cells[11], grid.empty_destinations)
        self.assertIsNone(read_formation_grid(self.frame, observation=replace(observation, panel=panel),
                                             expected_target=(0, 0)))

    def test_first_cell_control_calibration_rejects_missing_misaligned_and_recolored_controls(self):
        original = frame("selected-first-cell")
        observation = replace(BrawlVision(original).observe(read_panel=False),
                              panel=PreparationPanel(5, 4, 5, 5, 60, 0))
        # No static edge anchor survives this capture. The complete blue set,
        # green magnifier and correctly positioned red minus are all required.
        for asset in (FORMATION_SOURCE_SELECTED, FORMATION_MOVE_SOURCE):
            marker = asset.match_multi_template(original, similarity=.9)[0]
            left, top, right, bottom = marker.area
            for corrupt in ("missing", "shifted", "duplicate", "recolored"):
                with self.subTest(asset=asset.name, corrupt=corrupt):
                    self.frame = original.copy()
                    if corrupt == "duplicate":
                        shift = 124 if asset is FORMATION_MOVE_SOURCE else -249
                        self.frame[top + 112:bottom + 112, left + shift:right + shift] = original[top:bottom, left:right]
                    else:
                        self.frame[top:bottom, left:right] = 0
                        if corrupt == "shifted":
                            self.frame[top:bottom, left + 6:right + 6] = original[top:bottom, left:right]
                        elif corrupt == "recolored":
                            self.frame[top:bottom, left:right] = original[top:bottom, left:right].mean(axis=2, keepdims=True)
                    self.assertIsNone(read_formation_grid(self.frame, observation=observation))
        self.frame = original.copy()
        self.frame[200:237, 368:406] = 0
        self.assertIsNone(read_formation_grid(self.frame, observation=observation))

    def test_captured_green_source_is_occupied_but_never_its_own_destination(self):
        self.frame = frame("move-source")
        grid = read_formation_grid(self.frame, expected_source=(3, 3), expected_target=(0, 0))
        self.assertIsNotNone(grid)
        self.assertEqual(grid.selected_source, grid.cells[15])
        self.assertEqual(grid.occupied, (grid.cells[11], grid.cells[15]))
        self.assertIsNone(read_formation_grid(self.frame, expected_source=(2, 3)))
        self.assertIsNone(read_formation_grid(self.frame, expected_target=(3, 3)))
        self.frame = frame("withdraw-grid")
        full = read_formation_grid(self.frame, expected_source=(6, 3), expected_target=(0, 0))
        self.assertIsNotNone(full)
        self.assertEqual(full.selected_source, full.cells[27])
        self.assertEqual(full.occupied,
                         tuple(full.cells[index] for index in (3, 7, 11, 14, 15, 17, 19, 22, 23, 27)))
        self.assertEqual(full.panel.deployed, 10)

    def test_resumed_source_with_right_outline_covered_uses_its_paired_controls(self):
        self.frame = frame("resume-source-right-occluded")
        vision = BrawlVision(self.frame)
        observation = replace(vision.observe(), panel=vision.preparation_panel())
        grid = read_formation_grid(self.frame, observation=observation, expected_source=(3, 3))
        self.assertIsNotNone(grid)
        self.assertEqual(grid.selected_source, grid.cells[15])
        self.assertEqual(grid.occupied, tuple(grid.cells[index] for index in (9, 13, 15, 19)))
        control = read_withdraw_control(self.frame, observation=observation)
        self.assertIsNotNone(control)
        self.assertEqual(control.source, grid.selected_source)
        self.assertIsNone(read_formation_grid(self.frame, observation=observation, expected_source=(4, 3)))
        self.assertIsNone(read_formation_grid(self.frame, observation=observation, expected_target=(3, 3)))

    def test_right_occluded_source_rejects_missing_duplicate_and_recolored_controls(self):
        original = frame("resume-source-right-occluded")
        vision = BrawlVision(original)
        observation = replace(vision.observe(), panel=vision.preparation_panel())
        controls = ((678, 368, 716, 405), (740, 361, 789, 411))
        for area in controls:
            for corrupt in ("missing", "duplicate", "recolored"):
                with self.subTest(area=area, corrupt=corrupt):
                    self.frame = original.copy()
                    left, top, right, bottom = area
                    if corrupt == "missing":
                        self.frame[top:bottom, left:right] = 0
                    elif corrupt == "duplicate":
                        self.frame[top:bottom, left - 249:right - 249] = original[top:bottom, left:right]
                    else:
                        # Preserve the control's shape while replacing its
                        # positive red/green color with a neutral channel.
                        region = original[top:bottom, left:right]
                        self.frame[top:bottom, left:right] = region.mean(axis=2, keepdims=True)
                    self.assertIsNone(read_formation_grid(self.frame, observation=observation))
                    self.assertIsNone(read_withdraw_control(self.frame, observation=observation))

    def test_resumed_source_with_left_outline_covered_has_complete_occupation_and_minus(self):
        self.frame = frame("resume-discovery-occluded")
        vision = BrawlVision(self.frame)
        observation = replace(vision.observe(), panel=vision.preparation_panel())
        self.assertEqual(observation.state, BrawlScreen.FORMATION_SELECTION)
        grid = read_formation_grid(self.frame, observation=observation, expected_source=(2, 1))
        self.assertIsNotNone(grid)
        self.assertEqual(grid.panel.deployed, 4)
        self.assertEqual(grid.selected_source, grid.cells[9])
        self.assertEqual(grid.occupied, tuple(grid.cells[index] for index in (9, 13, 15, 19)))
        self.assertIsNone(read_formation_grid(self.frame, observation=observation, expected_source=(3, 1)))
        self.assertIsNone(read_formation_grid(self.frame, observation=observation, expected_target=(2, 1)))
        control = read_withdraw_control(self.frame, observation=observation)
        self.assertIsNotNone(control)
        self.assertEqual(control.source, grid.selected_source)
        self.assertEqual(control.panel, grid.panel)
        self.assertEqual(control.button_area, (502, 321, 519, 338))

    def test_occluded_source_still_requires_both_exposed_edges_and_its_magnifier(self):
        original = frame("resume-discovery-occluded")
        vision = BrawlVision(original)
        observation = replace(vision.observe(), panel=vision.preparation_panel())
        # Exposed edges may also disappear behind a body. The independently
        # paired, colored controls must survive regardless of those outlines.
        for area in ((604, 336, 613, 371), (550, 370, 606, 391)):
            self.frame = original.copy()
            left, top, right, bottom = area
            self.frame[top:bottom, left:right] = 0
            self.assertIsNotNone(read_formation_grid(self.frame, observation=observation,
                                                    expected_source=(2, 1)))
            self.assertIsNotNone(read_withdraw_control(self.frame, observation=observation))
        for area in ((492, 312, 530, 349), (554, 305, 603, 355)):
            with self.subTest(area=area):
                self.frame = original.copy()
                left, top, right, bottom = area
                self.frame[top:bottom, left:right] = 0
                self.assertIsNone(read_formation_grid(self.frame, observation=observation,
                                                     expected_source=(2, 1)))
                self.assertIsNone(read_withdraw_control(self.frame, observation=observation))

    def test_occluded_source_keeps_complete_count_and_minus_pairing_checks(self):
        original = frame("resume-discovery-occluded")
        vision = BrawlVision(original)
        observation = replace(vision.observe(), panel=vision.preparation_panel())
        self.frame = original.copy()
        self.frame[362:410, 458:508] = 0
        self.assertIsNone(read_formation_grid(self.frame, observation=observation))
        self.assertIsNotNone(read_withdraw_control(self.frame, observation=observation))
        self.assertIsNone(read_formation_grid(original, observation=replace(
            observation, panel=replace(observation.panel, deployed=3))))
        for shift in (None, -124):
            with self.subTest(minus_shift=shift):
                self.frame = original.copy()
                self.frame[316:343, 495:525] = 0
                if shift is not None:
                    self.frame[316:343, 495 + shift:525 + shift] = original[316:343, 495:525]
                # A source needs both controls at its anchored location even
                # if the surrounding green outline remains fully visible.
                self.assertIsNone(read_formation_grid(self.frame, observation=observation))
                self.assertIsNone(read_withdraw_control(self.frame, observation=observation))

    def test_missing_or_extra_occupied_controls_never_prove_free_cells(self):
        original = frame("formation-move")
        observation = BrawlVision(original).observe()
        panel = BrawlVision(original).preparation_panel()
        left, top, right, bottom = FORMATION_SOURCE_SELECTED.area
        self.frame = original.copy()
        self.frame[top:bottom, left:right] = 0
        # Keep the independently read real context while corrupting only the
        # control under test; a lost arrow must invalidate total occupation.
        with patch.object(BrawlVision, "preparation_panel", return_value=panel):
            self.assertIsNone(read_formation_grid(self.frame, observation=observation))
        self.frame = original.copy()
        self.frame[top:bottom, left - 249:right - 249] = original[top:bottom, left:right]
        with patch.object(BrawlVision, "preparation_panel", return_value=panel):
            self.assertIsNone(read_formation_grid(self.frame, observation=observation))
        self.frame = original
        with patch.object(BrawlVision, "preparation_panel", return_value=replace(panel, deployed=2)):
            self.assertIsNone(read_formation_grid(self.frame, observation=observation))

    def test_local_occlusion_does_not_authorize_the_hidden_target(self):
        original = frame("formation-place")
        observation = BrawlVision(original).observe()
        panel = BrawlVision(original).preparation_panel()
        self.frame = original.copy()
        self.frame[485:615, 290:865] = 0
        with patch.object(BrawlVision, "preparation_panel", return_value=panel):
            grid = read_formation_grid(self.frame, observation=observation, expected_target=(0, 0))
            self.assertIsNotNone(grid)
            self.assertEqual(grid.occupied, ())
            self.assertIn(grid.cells[0], grid.yellow_destinations)
            self.assertNotIn(grid.cells[24], grid.yellow_destinations)
            self.assertIn(grid.cells[24], grid.empty_destinations)
            self.assertIsNotNone(read_formation_grid(self.frame, observation=observation,
                                                    expected_target=(6, 0)))

    def test_missing_conflicting_or_duplicate_anchors_are_rejected(self):
        self.frame = frame("formation-place")
        observation = BrawlVision(self.frame).observe()
        panel = BrawlVision(self.frame).preparation_panel()
        a = FORMATION_GRID_CHECK.area
        b = SWAP_GRID_ANCHOR.area
        first = SimpleNamespace(area=a)
        shifted = SimpleNamespace(area=(b[0] + 12, b[1], b[2] + 12, b[3]))
        for anchors, swap in (([], []), ([first, first], []), ([first], [shifted])):
            with self.subTest(anchors=len(anchors), swap=len(swap)), \
                    patch.object(BrawlVision, "preparation_panel", return_value=panel), \
                    patch.object(FORMATION_GRID_CHECK, "match_multi_template", return_value=anchors), \
                    patch.object(SWAP_GRID_ANCHOR, "match_multi_template", return_value=swap):
                self.assertIsNone(read_formation_grid(self.frame, observation=observation))

    def test_fresh_anchor_moves_grid_without_reusing_cached_offsets(self):
        self.frame = frame("move-source")
        observation = BrawlVision(self.frame).observe()
        expected = read_formation_grid(self.frame, observation=observation)
        self.assertIsNotNone(expected)
        assets = (FORMATION_GRID_CHECK, SWAP_GRID_ANCHOR,
                  FORMATION_SOURCE_SELECTED, FORMATION_MOVE_SOURCE)
        with patch.object(BrawlVision, "preparation_panel", return_value=expected.panel):
            try:
                for asset in assets:
                    asset.load_offset((12, -8))
                self.assertEqual(read_formation_grid(self.frame, observation=observation), expected)
            finally:
                for asset in assets:
                    asset.load_offset((0, 0))
            # Translation and retained context are explicit synthetic inputs;
            # fixed-position OCR is not claimed to support shifted whole screens.
            original = self.frame
            self.frame = np.zeros_like(original)
            self.frame[:-8, 12:] = original[8:, :-12]
            shifted = read_formation_grid(self.frame, observation=observation,
                                          expected_source=(3, 3), expected_target=(0, 0))
            self.assertIsNotNone(shifted)
            self.assertEqual([cell.center for cell in shifted.cells],
                             [(cell.center[0] + 12, cell.center[1] - 8) for cell in expected.cells])
            self.assertEqual(shifted.selected_source.center, (743, 401))
            self.assertEqual(len(shifted.occupied), 2)

    def test_non_grid_scenes_and_unknown_mode_never_publish_cells(self):
        for name in ("normal-deployed", "formation-deploy-animation", "normal-opening", "income-tooltip"):
            with self.subTest(sample=name):
                self.frame = frame(name)
                self.assertIsNone(read_formation_grid(self.frame))
        self.frame = frame("formation-place")
        observation = BrawlVision(self.frame).observe()
        self.assertEqual(observation.state, BrawlScreen.FORMATION_SELECTION)
        self.assertIsNone(read_formation_grid(self.frame, observation=replace(observation, game_mode=None)))
