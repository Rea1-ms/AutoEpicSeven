"""Place one explicitly selected bench hero and verify its visible destination."""

from dataclasses import dataclass
from enum import Enum

from module.base.base import ModuleBase
from module.base.button import ClickButton
from module.base.timer import Timer
from tasks.brawl.bench import BenchHero, read_bench
from tasks.brawl.board import read_board
from tasks.brawl.cursor import read_cursor
from tasks.brawl.field import SUPPORTED_FIELD_HEROES, read_field
from tasks.brawl.vision import (
    BrawlMode,
    BrawlScreen,
    BrawlVision,
    ObservationError,
    PreparationPanel,
)


@dataclass(frozen=True)
class PlacementRequest:
    hero: BenchHero
    panel: PreparationPanel
    game_mode: BrawlMode
    screen_row: int
    screen_column: int


class PlacementOutcome(str, Enum):
    CONFIRMED = "confirmed"
    TIMEOUT = "timeout"
    SCENE_CHANGED = "scene_changed"
    CONTEXT_CHANGED = "context_changed"
    SELECTION_CHANGED = "selection_changed"
    TARGET_UNAVAILABLE = "target_unavailable"


def capture_placement(image, screen_row, screen_column):
    """Capture a fresh selected card; the caller supplies a screen-grid cell.

    This is not a strategy-coordinate conversion or permission to swap. An
    unsupported field identity cannot be verified after placement, so do not
    start that action. A full bench does not prevent placing a hero on an empty
    field slot: those two capacities are independent.
    """
    if (type(screen_row) is not int or not 0 <= screen_row < 7
            or type(screen_column) is not int or not 0 <= screen_column < 4):
        raise ValueError("A verified screen row 0..6 and column 0..3 are required")
    cursor = read_cursor(image)
    if (cursor.panel is None or cursor.game_mode is None or cursor.selected is None
            or cursor.selected.imprint is None or cursor.empty is not False):
        raise ObservationError("A known selected bench card is required")
    if cursor.selected.name not in SUPPORTED_FIELD_HEROES:
        raise ObservationError("This hero's field identity cannot yet be verified")
    if cursor.panel.deployed >= cursor.panel.capacity:
        raise ObservationError("The field has no remaining deployment slot")
    return PlacementRequest(cursor.selected, cursor.panel, cursor.game_mode, screen_row, screen_column)


def _selected_entry(image, observation, *, viewport=None):
    """Select a card from a viewport belonging to this exact screenshot only."""
    if viewport is not None:
        BrawlVision(image)
        if observation.state not in (BrawlScreen.PREPARATION, BrawlScreen.FORMATION_SELECTION,
                                     BrawlScreen.TRANSMIT_SELECTION):
            return None
    visible = viewport if viewport is not None else read_bench(image, observation=observation)
    if observation.panel is not None and visible.panel != observation.panel:
        return None
    entries = visible.entries
    selected = [entry for entry in entries if 996 <= entry.name_area[0] <= 1010
                and 247 <= entry.name_area[1] < entry.name_area[3] <= 284
                and entry.plus_area is not None]
    return selected[0] if len(selected) == 1 else None


def _same_resources(first, second):
    return (first.round_number, first.level, first.capacity, first.coins, first.synergy_tokens) == (
        second.round_number, second.level, second.capacity, second.coins, second.synergy_tokens)


class BrawlPlacement(ModuleBase):
    ACTION_INTERVAL = 3
    STEP_TIMEOUT = 12
    ACTION_TIMER = "BrawlPlacement"

    def place_selected(self, request, skip_first_screenshot=True):
        """Deploy once; count and the intended sprite must agree at the target.

        Pages:
            in: stable preparation with the caller's selected bench card
            out: verified preparation, an interruption or the unconfirmed frame

        Reopening the plus is allowed only until the grid has first appeared.
        The destination is clicked once. Repeating it after an unobserved return
        could select or move the newly deployed hero, so never retry that click.
        Preparation after the destination click cannot authorize another action,
        even when its deployment count or sprite is still animating.
        """
        if (not isinstance(request, PlacementRequest)
                or request.hero.name not in SUPPORTED_FIELD_HEROES
                or request.hero.imprint is None
                or request.panel.deployed >= request.panel.capacity
                or type(request.screen_row) is not int or not 0 <= request.screen_row < 7
                or type(request.screen_column) is not int or not 0 <= request.screen_column < 4):
            raise ValueError("A captured, supported placement request is required")
        # A slow screenshot must not authorize a fresh click after this step's
        # deadline. Confirmed placement below is still checked before timeout.
        timeout = Timer(self.STEP_TIMEOUT).start()
        plus_clicked = grid_seen = returned = False
        target = None
        while True:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()
            image = self.device.image
            vision = BrawlVision(image)
            observed = vision.observe()
            if observed.state not in (BrawlScreen.PREPARATION, BrawlScreen.FORMATION_SELECTION,
                                       BrawlScreen.UNKNOWN):
                return PlacementOutcome.SCENE_CHANGED
            if observed.game_mode is not None and observed.game_mode != request.game_mode:
                return PlacementOutcome.CONTEXT_CHANGED
            if observed.state == BrawlScreen.UNKNOWN or observed.game_mode is None:
                if timeout.reached():
                    return PlacementOutcome.TIMEOUT
                continue

            # Remember leaving the grid even when numeric OCR is temporarily
            # unread; a later grid must not be mistaken for the pending click.
            if target is not None and observed.state == BrawlScreen.PREPARATION:
                returned = True
            if returned and observed.state == BrawlScreen.FORMATION_SELECTION:
                return PlacementOutcome.SCENE_CHANGED
            if target is None and grid_seen and observed.state == BrawlScreen.PREPARATION:
                return PlacementOutcome.SCENE_CHANGED
            if observed.state == BrawlScreen.FORMATION_SELECTION:
                if not plus_clicked:
                    return PlacementOutcome.SCENE_CHANGED
                grid_seen = True
            panel = observed.panel
            if panel is None and observed.state == BrawlScreen.FORMATION_SELECTION:
                try:
                    panel = vision.preparation_panel()
                except ObservationError:
                    pass
            if panel is None:
                if timeout.reached():
                    return PlacementOutcome.TIMEOUT
                continue
            if not _same_resources(panel, request.panel):
                return PlacementOutcome.CONTEXT_CHANGED

            if target is not None:
                if panel.deployed not in (request.panel.deployed, request.panel.deployed + 1):
                    return PlacementOutcome.CONTEXT_CHANGED
                if (observed.state == BrawlScreen.PREPARATION
                        and panel.deployed == request.panel.deployed + 1):
                    field = read_field(image, observation=observed)
                    found = [hero for hero in field.heroes if hero.name == request.hero.name]
                    if (field.panel == panel and len(found) == 1
                            and all(abs(a - b) <= 8 for a, b in zip(found[0].foot, target))):
                        return PlacementOutcome.CONFIRMED
                if timeout.reached():
                    return PlacementOutcome.TIMEOUT
                continue
            if panel != request.panel:
                return PlacementOutcome.CONTEXT_CHANGED
            entry = _selected_entry(image, observed)
            if entry is None or entry.imprint is None:
                if timeout.reached():
                    return PlacementOutcome.TIMEOUT
                continue
            if BenchHero(entry.name, entry.imprint) != request.hero:
                return PlacementOutcome.SELECTION_CHANGED
            if timeout.reached():
                return PlacementOutcome.TIMEOUT
            if observed.state == BrawlScreen.PREPARATION:
                # Retry only this pending plus request. A positively observed
                # grid authorizes its distinct destination immediately; it must
                # not inherit the open button's retry interval.
                if plus_clicked and not self.interval_is_reached(
                        "BrawlPlacementOpen", interval=self.ACTION_INTERVAL):
                    continue
                self.device.click(ClickButton(entry.plus_area, name="BrawlPlacementOpen"))
                self.interval_reset("BrawlPlacementOpen", interval=self.ACTION_INTERVAL)
                plus_clicked = True
                continue
            board = read_board(image, observation=observed)
            if board is None:
                continue
            if (board.selected_source is not None
                    or len(board.occupied_swap_targets) != panel.deployed):
                return PlacementOutcome.TARGET_UNAVAILABLE
            cell = next(cell for cell in board.cells if cell.screen_row == request.screen_row
                        and cell.screen_column == request.screen_column)
            if cell not in board.yellow_destinations:
                return PlacementOutcome.TARGET_UNAVAILABLE
            target = cell.center
            x, y = target
            self.device.click(ClickButton((x, y, x + 1, y + 1), name="BrawlPlacementCell"))
            timeout.reset()
