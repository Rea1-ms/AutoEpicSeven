import unittest

import numpy as np

from module.config import server

server.set_lang("global_cn")

from tasks.brawl.assets.assets_brawl import (  # noqa: E402
    FORMATION_GRID_CHECK,
    FORMATION_MOVE_SOURCE,
    FORMATION_SOURCE_SELECTED,
)
from tasks.brawl.board import read_board  # noqa: E402
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
