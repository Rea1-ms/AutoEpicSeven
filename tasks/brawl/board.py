"""Locate the visible formation grid without guessing hero identities or axes."""

from dataclasses import dataclass

import cv2
import numpy as np

from tasks.brawl.assets.assets_brawl import (
    FORMATION_GRID_CHECK,
    FORMATION_MOVE_SOURCE,
    FORMATION_SOURCE_SELECTED,
)
from tasks.brawl.assets.assets_brawl_actions import SWAP_GRID_ANCHOR
from tasks.brawl.assets.assets_brawl_withdraw import FIELD_WITHDRAW
from tasks.brawl.rules import team_capacity
from tasks.brawl.vision import (
    BrawlScreen,
    BrawlVision,
    ObservationError,
    PreparationPanel,
)


@dataclass(frozen=True)
class BoardCell:
    """A screen row is horizontal; these are not strategy front/back coordinates."""

    screen_row: int
    screen_column: int
    center: tuple[int, int]


@dataclass(frozen=True)
class BoardObservation:
    """Grid geometry and explicit controls; unmarked cells are not proven empty."""

    cells: tuple[BoardCell, ...]
    selected_source: BoardCell | None = None
    occupied_swap_targets: tuple[BoardCell, ...] = ()
    yellow_destinations: tuple[BoardCell, ...] = ()


@dataclass(frozen=True)
class SwapTargets:
    """Only occupied controls are verified; there is no claim of free cells."""

    occupied: tuple[BoardCell, ...]
    panel: PreparationPanel


@dataclass(frozen=True)
class FormationGrid:
    """Anchored coordinates and complete occupation; yellow edges are auxiliary."""

    cells: tuple[BoardCell, ...]
    occupied: tuple[BoardCell, ...]
    selected_source: BoardCell | None
    yellow_destinations: tuple[BoardCell, ...]
    panel: PreparationPanel

    @property
    def empty_destinations(self):
        # The reader has independently matched every occupied control and
        # checked their count against deployed. Its complement on the anchored
        # board therefore supplies empty coordinates even when a body or icon
        # covers a yellow edge. A full field opened from the bench permits only
        # swaps; a selected field source may still move without adding a hero.
        # Yellow pixels are observations, never another permission requirement.
        if self.selected_source is None and self.panel.deployed >= self.panel.capacity:
            return ()
        return tuple(cell for cell in self.cells if cell not in self.occupied)


def logical_to_screen(row, column):
    """Convert strategy front/back rows into screen indices, never pixels.

    Manual calibration uses the real formation-place and challenge-combat
    captures: the placement half lies left of the central arena divider, and
    allied green bars face the purple-bar enemies on their right. Therefore
    logical row zero is the rightmost screen column. Logical columns run from
    top to bottom. The alternating horizontal offset belongs to _cells, not
    this index conversion; a fresh grid still has to authorize each click.
    """
    if (type(row) is not int or not 0 <= row < 4
            or type(column) is not int or not 0 <= column < 7):
        raise ValueError("A logical row 0..3 and column 0..6 are required")
    return column, 3 - row


def screen_to_logical(screen_row, screen_column):
    """Inverse of the calibrated strategy-to-screen index conversion."""
    _validate_screen_cell((screen_row, screen_column))
    return 3 - screen_column, screen_row


def _validate_screen_cell(value):
    if (not isinstance(value, tuple) or len(value) != 2
            or type(value[0]) is not int or not 0 <= value[0] < 7
            or type(value[1]) is not int or not 0 <= value[1] < 4):
        raise ValueError("A screen cell tuple with row 0..6 and column 0..3 is required")


def _cells(dx=0, dy=0):
    return tuple(
        BoardCell(row, column, (int(420 + column * 124.5 - (row % 2) * 62.25 + dx + 0.5),
                                241 + row * 56 + dy))
        for row in range(7) for column in range(4)
    )


def read_formation_grid(image, *, observation=None, expected_source=None, expected_target=None):
    """Read complete occupation and derive destinations on the anchored grid.

    A selected field hero contributes one green source; every other occupied
    cell needs its blue swap control. Their total must agree with deployed.
    Yellow outlines are auxiliary observations, not per-cell click gates.
    Missing arrows invalidate the occupied set instead of proving emptiness.
    An expected source or target is a screen-index tuple, never a remembered
    pixel coordinate. The source cannot be used as a destination for itself.
    """
    for requested in (expected_source, expected_target):
        if requested is not None:
            _validate_screen_cell(requested)
    vision = BrawlVision(image)
    current = observation if observation is not None else vision.observe()
    if current.state != BrawlScreen.FORMATION_SELECTION or current.game_mode is None:
        return None
    # The caller may have just read this frame's panel while validating its
    # action context. Reuse only that explicitly supplied observation; never
    # retain a panel across screenshots, grid transitions or resource changes.
    panel = current.panel
    if panel is None:
        try:
            panel = vision.preparation_panel()
        except ObservationError:
            return None

    # Both independently captured anchors describe the same grid. When both
    # survive they must agree; no cached asset offset or majority guess may
    # relocate the caller's requested source to a different occupied hero.
    offsets = []
    for asset in (FORMATION_GRID_CHECK, SWAP_GRID_ANCHOR):
        anchors = asset.match_multi_template(image, similarity=0.9)
        if len(anchors) > 1:
            return None
        if anchors:
            area = anchors[0].area
            offsets.append((int(area[0] - asset.area[0]), int(area[1] - asset.area[1])))
    if len(set(offsets)) > 1:
        return None
    markers = FORMATION_SOURCE_SELECTED.match_multi_template(image, similarity=0.9)
    sources = FORMATION_MOVE_SOURCE.match_multi_template(image, similarity=0.9)
    if len(sources) > 1:
        return None
    if not offsets:
        # Fixed edges can be obscured at any occupancy. Requiring a full team
        # here strands recovery after withdrawing a hero: its remaining seven
        # controls still prove the same grid, but its count is now 7/8. Require
        # a nonempty, complete set instead, retaining the panel parser's level
        # and capacity invariant for explicit same-frame observations as well.
        # Count a green source alongside the blue controls, never as a missing
        # hero. Every control must agree on one small translation of unique
        # calibrated cells; a source additionally needs its red minus below.
        # Neither an absent marker nor the expected destination proves geometry,
        # and no control may drift a whole cell to another hero's position.
        if (panel.capacity != team_capacity(panel.level) or not 0 < panel.deployed <= panel.capacity
                or len(markers) + len(sources) != panel.deployed):
            return None
        controls = []
        for marker, source_offset in [(marker, 0) for marker in markers] + [(marker, 33.5) for marker in sources]:
            left, top, right, bottom = marker.area
            x, y = (left + right) / 2 - source_offset, (top + bottom) / 2 + 23
            candidates = [cell for cell in _cells()
                          if abs(cell.center[0] - x) <= 8 and abs(cell.center[1] - y) <= 8]
            if len(candidates) != 1 or candidates[0] in [c for c, _, _ in controls]:
                return None
            cell = candidates[0]
            controls.append((cell, x - cell.center[0], y - cell.center[1]))
        dx, dy = (round(float(np.median([control[axis] for control in controls])))
                  for axis in (1, 2))
        if any(abs(x - dx) > 2 or abs(y - dy) > 2 for _, x, y in controls):
            return None
        offsets.append((dx, dy))
    cells = _cells(*offsets[0])
    if any(not (61 <= cell.center[0] < image.shape[1] - 61
                and 35 <= cell.center[1] < image.shape[0] - 35) for cell in cells):
        return None
    r, g, b = (image[:, :, channel].astype(np.int16) for channel in range(3))
    yellow = (r > 180) & (g > 150) & (b < 225) & (r - b > 30) & (g - b > 15)
    kernel = np.ones((7, 7), dtype=np.uint8)
    yellow_outline = cv2.dilate(yellow.astype(np.uint8), kernel)
    targets = []
    for marker in markers:
        left, top, right, bottom = marker.area
        x, y = (left + right) / 2, (top + bottom) / 2 + 23
        candidates = [cell for cell in cells
                      if abs(cell.center[0] - x) <= 8 and abs(cell.center[1] - y) <= 8]
        if len(candidates) != 1:
            return None
        target = candidates[0]
        if target in targets:
            return None
        targets.append(target)

    source = None
    if sources:
        left, top, right, bottom = sources[0].area
        x, y = (left + right) / 2 - 33.5, (top + bottom) / 2 + 23
        candidates = [cell for cell in cells
                      if abs(cell.center[0] - x) <= 8 and abs(cell.center[1] - y) <= 8]
        if len(candidates) != 1:
            return None
        source = candidates[0]
        if source in targets or _source_withdraw_area(image, source.center, sources[0].area) is None:
            return None
    occupied = tuple(sorted(targets + ([source] if source is not None else []), key=cells.index))
    if len(occupied) != panel.deployed:
        return None
    if expected_source is not None and (source is None or
            (source.screen_row, source.screen_column) != expected_source):
        return None
    destinations = tuple(cell for cell in cells if cell not in occupied
                         and _outline_coverage(yellow_outline, cell.center) >= 0.8)
    grid = FormationGrid(cells, occupied, source, destinations, panel)
    if expected_target is not None and not any(
            (cell.screen_row, cell.screen_column) == expected_target
            for cell in tuple(targets) + grid.empty_destinations):
        return None
    return grid


def read_swap_targets(image, *, observation=None):
    """Read positive occupied destinations, including the full-field swap UI.

    At 10/10 this UI hides all free-cell outlines, so it cannot pass the full
    placement-grid reader. A separately captured occupied hex corner anchors
    the supported layout. An unread anchor, source control or missing occupied
    marker leaves the whole set unknown. This never authorizes empty placement.
    """
    vision = BrawlVision(image)
    current = observation if observation is not None else vision.observe()
    if current.state != BrawlScreen.FORMATION_SELECTION or current.game_mode is None:
        return None
    try:
        panel = vision.preparation_panel()
    except ObservationError:
        return None
    if panel.deployed == 0 or FORMATION_MOVE_SOURCE.match_multi_template(image, similarity=0.9):
        return None
    board = read_board(image, observation=current)
    if board is not None:
        if board.selected_source is not None or len(board.occupied_swap_targets) != panel.deployed:
            return None
        return SwapTargets(board.occupied_swap_targets, panel)
    anchors = SWAP_GRID_ANCHOR.match_multi_template(image, similarity=0.9)
    if len(anchors) != 1:
        return None
    area = anchors[0].area
    cells = _cells(int(area[0] - SWAP_GRID_ANCHOR.area[0]),
                   int(area[1] - SWAP_GRID_ANCHOR.area[1]))
    markers = FORMATION_SOURCE_SELECTED.match_multi_template(image, similarity=0.9)
    if len(markers) != panel.deployed:
        return None
    targets = []
    for marker in markers:
        left, top, right, bottom = marker.area
        x, y = (left + right) / 2, (top + bottom) / 2 + 23
        candidates = [cell for cell in cells
                      if abs(cell.center[0] - x) <= 8 and abs(cell.center[1] - y) <= 8]
        if len(candidates) != 1 or candidates[0] in targets:
            return None
        targets.append(candidates[0])
    return SwapTargets(tuple(sorted(targets, key=cells.index)), panel)


def _outline_coverage(mask, center):
    x, y = center
    vertices = np.array(((x, y - 35), (x + 61, y - 17), (x + 61, y + 17),
                         (x, y + 35), (x - 61, y + 17), (x - 61, y - 17)))
    samples = []
    for start, end in zip(vertices, np.roll(vertices, -1, axis=0)):
        points = np.rint(start + np.linspace(0.15, 0.85, 15)[:, None] * (end - start)).astype(int)
        samples.extend(mask[points[:, 1], points[:, 0]])
    return float(np.mean(samples))


def _source_withdraw_area(image, center, magnifier_area):
    """Require two unique, colored controls at the same anchored source.

    Bodies can hide either side of a selected hexagon. Picking a different
    edge per capture keeps moving that failure. Instead, the uniquely located
    magnifier and minus must retain their captured colors and their independent
    offsets to this exact cell. A missing/misplaced control remains unknown;
    no hidden edge becomes an empty destination. Callers still prove the grid
    anchor and complete occupied count separately before a formation action.
    """
    x, y = center
    minus = FIELD_WITHDRAW.match_multi_template(image, similarity=.92)
    if len(minus) != 1:
        return None
    left, top, right, bottom = minus[0].area
    if abs((left + right) / 2 - x + 35) > 7 or abs((top + bottom) / 2 - y + 23) > 7:
        return None
    for area, asset in ((magnifier_area, FORMATION_MOVE_SOURCE), (minus[0].area, FIELD_WITHDRAW)):
        left, top, right, bottom = area
        if not (0 <= left < right <= image.shape[1] and 0 <= top < bottom <= image.shape[0]):
            return None
        region = image[top:bottom, left:right]
        if (np.mean(np.all(region == 0, axis=2)) >= .01
                or np.max(np.abs(region.mean(axis=(0, 1)) - np.array(asset.color))) > 20):
            return None
    return tuple(map(int, minus[0].button))


def read_board(image, *, observation=None):
    """Read all 28 cells only while a complete placement grid is visible.

    The captured board has seven staggered horizontal screen rows, with four
    cells each. The strategy's four front/back rows use a different convention;
    callers can convert indices with logical_to_screen. Coordinates are derived
    from this frame's grid anchor, never a previously matched click rectangle.
    A placement animation or ordinary preparation frame has no usable grid.
    """
    vision = BrawlVision(image)
    if observation is None:
        observation = vision.observe()
    if observation.state != BrawlScreen.FORMATION_SELECTION:
        return None
    anchors = FORMATION_GRID_CHECK.match_multi_template(image, similarity=0.9)
    if len(anchors) != 1:
        return None
    area = anchors[0].area
    dx = int(area[0] - FORMATION_GRID_CHECK.area[0])
    dy = int(area[1] - FORMATION_GRID_CHECK.area[1])
    cells = _cells(dx, dy)

    # A single top-left anchor can survive a tooltip over the lower board.
    # All cells therefore need visible outline evidence. Adjacent hexagons share
    # edges, and yellow, cyan and green controls can illuminate the same edge.
    # Combining these masks validates geometry, not occupation. A missing marker
    # never proves that a cell is empty, nor that it is the selected hero's cell.
    r, g, b = (image[:, :, channel].astype(np.int16) for channel in range(3))
    yellow = (r > 180) & (g > 150) & (b < 225) & (r - b > 30) & (g - b > 15)
    blue = (b > 140) & (g > 130) & (b - r > 5) & (g - r > 5)
    green = (g > 130) & (g - r > 20) & (g - b > 20)
    # Captured outlines have a three-pixel glow on either side of the hex edge.
    kernel = np.ones((7, 7), dtype=np.uint8)
    outline = cv2.dilate((yellow | blue | green).astype(np.uint8), kernel)
    coverage = tuple(_outline_coverage(outline, cell.center) for cell in cells)
    if any(score < 0.8 for score in coverage):
        return None

    yellow_outline = cv2.dilate(yellow.astype(np.uint8), kernel)
    green_outline = cv2.dilate(green.astype(np.uint8), kernel)
    # The historical asset name is retained for compatibility. Its blue double
    # arrow means "swap with this occupied cell", not "currently selected hero".
    # Several such controls are legitimate and must each map to a unique cell.
    markers = FORMATION_SOURCE_SELECTED.match_multi_template(image, similarity=0.9)
    targets = []
    for marker in markers:
        left, top, right, bottom = marker.area
        marker_x, marker_y = (left + right) / 2, (top + bottom) / 2
        candidates = [cell for cell in cells
                      if abs(cell.center[0] - marker_x) <= 8
                      and abs(cell.center[1] - marker_y - 23) <= 8]
        if len(candidates) != 1:
            return None
        target = candidates[0]
        if target in targets or _outline_coverage(yellow_outline, target.center) >= 0.8:
            return None
        targets.append(target)

    # Only the green magnifier beside the red remove control identifies the
    # selected source. It sits right of the source; use both axes to avoid
    # assigning it to the next staggered screen row.
    sources = FORMATION_MOVE_SOURCE.match_multi_template(image, similarity=0.9)
    if len(sources) > 1:
        return None
    source = None
    if sources:
        left, top, right, bottom = sources[0].area
        marker_x, marker_y = (left + right) / 2, (top + bottom) / 2
        candidates = [cell for cell in cells
                      if abs(cell.center[0] - marker_x + 33.5) <= 8
                      and abs(cell.center[1] - marker_y - 23) <= 8]
        if len(candidates) != 1:
            return None
        source = candidates[0]
        if source in targets or _outline_coverage(green_outline, source.center) < 0.8:
            return None
    # Yellow outlines are positive placement controls. A caller still needs
    # the observed deployment count to agree with all occupied controls before
    # using them as empty destinations; an unread occupied control must never
    # silently become a free cell merely because it was absent from targets.
    destinations = tuple(cell for cell in cells if cell != source and cell not in targets
                         and _outline_coverage(yellow_outline, cell.center) >= 0.8)
    return BoardObservation(cells, source, tuple(sorted(targets, key=cells.index)), destinations)
