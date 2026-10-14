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


def _cells(dx=0, dy=0):
    return tuple(
        BoardCell(row, column, (int(420 + column * 124.5 - (row % 2) * 62.25 + dx + 0.5),
                                241 + row * 56 + dy))
        for row in range(7) for column in range(4)
    )


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


def read_board(image, *, observation=None):
    """Read all 28 cells only while a complete placement grid is visible.

    The captured board has seven staggered horizontal screen rows, with four
    cells each. The strategy's four front/back rows use a different convention;
    no mapping between the two is established here. Coordinates are derived
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
