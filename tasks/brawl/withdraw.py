"""Move one explicitly selected field cell to a bench with verified free space."""

from dataclasses import dataclass, replace
from enum import Enum

import cv2
import numpy as np

from module.base.base import ModuleBase
from module.base.button import ClickButton
from module.base.timer import Timer
from tasks.brawl.assets.assets_brawl import (
    FORMATION_GRID_CHECK,
    FORMATION_MOVE_SOURCE,
    QUEUE_EMPTY_CHECK,
)
from tasks.brawl.assets.assets_brawl_withdraw import FIELD_WITHDRAW
from tasks.brawl.bench import BenchHero, read_bench
from tasks.brawl.board import BoardCell, _cells, _outline_coverage
from tasks.brawl.rules import BENCH_CAPACITY, RuleInputError, get_hero, validate_imprint
from tasks.brawl.scanner import BenchScanResult
from tasks.brawl.vision import (
    BrawlMode,
    BrawlScreen,
    BrawlVision,
    ObservationError,
    PreparationPanel,
)


@dataclass(frozen=True)
class WithdrawControl:
    source: BoardCell
    button_area: tuple[int, int, int, int]
    panel: PreparationPanel


@dataclass(frozen=True)
class WithdrawRequest:
    source: BoardCell
    panel: PreparationPanel
    game_mode: BrawlMode
    bench_before: BenchScanResult
    selected: bool = False


class WithdrawOutcome(str, Enum):
    CONFIRMED = "confirmed"
    TIMEOUT = "timeout"
    SCENE_CHANGED = "scene_changed"
    CONTEXT_CHANGED = "context_changed"
    SOURCE_CHANGED = "source_changed"
    BENCH_CHANGED = "bench_changed"


@dataclass(frozen=True)
class WithdrawResult:
    outcome: WithdrawOutcome
    hero: BenchHero | None = None


def read_withdraw_control(image, *, observation=None):
    """Pair the current green source and red minus without claiming a full grid.

    Several occupied outlines can be obscured by bodies at ten deployed heroes.
    A removal needs only the grid anchor, one positively green source, and its
    uniquely adjacent red minus. It never infers empty destinations from missing
    controls, nor converts an overhead symbol's location into a board cell.
    """
    vision = BrawlVision(image)
    current = observation if observation is not None else vision.observe()
    if current.state != BrawlScreen.FORMATION_SELECTION or current.game_mode is None:
        return None
    try:
        panel = vision.preparation_panel()
    except ObservationError:
        return None
    if panel.deployed == 0:
        return None
    anchors = FORMATION_GRID_CHECK.match_multi_template(image, similarity=.9)
    sources = FORMATION_MOVE_SOURCE.match_multi_template(image, similarity=.9)
    minus = FIELD_WITHDRAW.match_multi_template(image, similarity=.92)
    if len(anchors) != 1 or len(sources) != 1 or len(minus) != 1:
        return None
    dx = int(anchors[0].area[0] - FORMATION_GRID_CHECK.area[0])
    dy = int(anchors[0].area[1] - FORMATION_GRID_CHECK.area[1])
    left, top, right, bottom = sources[0].area
    mx, my = (left + right) / 2, (top + bottom) / 2
    candidates = [cell for cell in _cells(dx, dy)
                  if abs(cell.center[0] - mx + 33.5) <= 8
                  and abs(cell.center[1] - my - 23) <= 8]
    if len(candidates) != 1:
        return None
    source = candidates[0]
    x, y = source.center
    if not (285 <= x <= 870 and 190 <= y <= 615):
        return None
    r, g, b = (image[:, :, channel].astype(np.int16) for channel in range(3))
    green = (g > 130) & (g - r > 20) & (g - b > 20)
    outline = cv2.dilate(green.astype(np.uint8), np.ones((7, 7), dtype=np.uint8))
    if _outline_coverage(outline, source.center) < .8:
        return None
    left, top, right, bottom = minus[0].area
    if abs((left + right) / 2 - x + 35) > 7 or abs((top + bottom) / 2 - y + 23) > 7:
        return None
    region = image[top:bottom, left:right]
    if (np.mean(np.all(region == 0, axis=2)) >= .01
            or np.max(np.abs(region.mean(axis=(0, 1)) - np.array(FIELD_WITHDRAW.color))) > 20):
        return None
    return WithdrawControl(source, tuple(map(int, minus[0].button)), panel)


def _valid_bench(scan):
    if (not isinstance(scan, BenchScanResult) or not scan.coverage_complete
            or scan.panel is None or scan.game_mode is None
            or not 0 <= len(scan.heroes) < BENCH_CAPACITY):
        return False
    names = set()
    for hero in scan.heroes:
        if not isinstance(hero, BenchHero) or hero.imprint is None:
            return False
        try:
            if (get_hero(hero.name).name != hero.name
                    or validate_imprint(hero.name, hero.imprint) != hero.imprint):
                return False
        except RuleInputError:
            return False
        if hero.name in names:
            return False
        names.add(hero.name)
    return True


def _bench_agrees(image, current, scan):
    visible = read_bench(image, observation=current)
    if visible.panel != scan.panel:
        return False
    known = {hero.name: hero.imprint for hero in scan.heroes}
    if not scan.heroes:
        # The queue remains visible in the selection overlay, but the ordinary
        # cursor reader intentionally accepts preparation only. Check the same
        # positive empty-queue asset here without changing that reader's scope.
        return not visible.entries and BrawlVision(image).matches(QUEUE_EMPTY_CHECK, color=True)
    return bool(visible.entries) and all(
        entry.name in known and entry.imprint == known[entry.name] for entry in visible.entries)


def capture_withdraw(image, screen_row, screen_column, before_scan):
    """Capture an explicit cell selection, not an inferred sprite identity.

    Selecting a requested cell is reversible and does not remove anyone. Only
    its subsequent green source and paired minus authorize removal. This lets
    unknown idle poses be read on the bench without guessing from moving bodies.
    The complete scan must be supplied by the caller before entering the grid.
    """
    if (type(screen_row) is not int or not 0 <= screen_row < 7
            or type(screen_column) is not int or not 0 <= screen_column < 4):
        raise ValueError("A screen row 0..6 and column 0..3 are required")
    if not _valid_bench(before_scan):
        raise ObservationError("A complete bench scan with at least one free seat is required")
    vision = BrawlVision(image)
    current = vision.observe()
    selected = current.state == BrawlScreen.FORMATION_SELECTION
    if current.state not in (BrawlScreen.PREPARATION, BrawlScreen.FORMATION_SELECTION):
        raise ObservationError("A stable preparation or explicitly selected field cell is required")
    panel = vision.preparation_panel() if selected else current.panel
    if (panel is None or panel.deployed == 0 or panel != before_scan.panel
            or current.game_mode != before_scan.game_mode
            or not _bench_agrees(image, current, before_scan)):
        raise ObservationError("The current preparation or visible bench differs from the scan")
    source = next(cell for cell in _cells()
                  if (cell.screen_row, cell.screen_column) == (screen_row, screen_column))
    if selected:
        control = read_withdraw_control(image, observation=current)
        if control is None or control.source != source:
            raise ObservationError("The requested field cell has no verified removal control")
    return WithdrawRequest(source, panel, current.game_mode, before_scan, selected)


class BrawlWithdraw(ModuleBase):
    ACTION_INTERVAL = 3
    STEP_TIMEOUT = 12
    ACTION_TIMER = "BrawlWithdraw"

    def withdraw(self, request, skip_first_screenshot=True):
        """Select the requested cell once, remove once, and read its new bench card.

        Pages:
            in: preparation or a verified selected source matching the request
            out: preparation with a named new bench hero, or an unconfirmed frame

        A lower deployed count alone cannot confirm identity. The new visible
        card must have a full name/imprint absent from the complete before scan;
        all visible old entries must remain consistent. An offscreen or obscured
        new card times out without clicking the minus again or guessing focus.
        This returns one observed hero, never a reconstructed complete roster.
        """
        if (not isinstance(request, WithdrawRequest) or not _valid_bench(request.bench_before)
                or request.panel != request.bench_before.panel
                or request.game_mode != request.bench_before.game_mode
                or request.source not in _cells() or request.panel.deployed <= 0):
            raise ValueError("A captured withdrawal request with free bench space is required")
        timeout = Timer(self.STEP_TIMEOUT).start()
        selected = grid_seen = request.selected
        clicked = returned = False
        expected = replace(request.panel, deployed=request.panel.deployed - 1)
        known = {hero.name: hero.imprint for hero in request.bench_before.heroes}
        while True:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()
            image = self.device.image
            vision = BrawlVision(image)
            current = vision.observe()
            if current.state not in (BrawlScreen.PREPARATION, BrawlScreen.FORMATION_SELECTION,
                                     BrawlScreen.UNKNOWN):
                return WithdrawResult(WithdrawOutcome.SCENE_CHANGED)
            if current.game_mode is not None and current.game_mode != request.game_mode:
                return WithdrawResult(WithdrawOutcome.CONTEXT_CHANGED)
            if current.state == BrawlScreen.UNKNOWN or current.game_mode is None:
                if timeout.reached():
                    return WithdrawResult(WithdrawOutcome.TIMEOUT)
                continue
            # Leaving a seen grid closes that selection even when its resource
            # text is unread. A later grid cannot resurrect the old minus click.
            if current.state == BrawlScreen.PREPARATION:
                if grid_seen and not clicked:
                    return WithdrawResult(WithdrawOutcome.SCENE_CHANGED)
                if clicked:
                    returned = True
            elif returned or not selected:
                return WithdrawResult(WithdrawOutcome.SCENE_CHANGED)
            else:
                grid_seen = True
            panel = current.panel
            if current.state == BrawlScreen.FORMATION_SELECTION:
                try:
                    panel = vision.preparation_panel()
                except ObservationError:
                    panel = None
            if panel is None:
                if timeout.reached():
                    return WithdrawResult(WithdrawOutcome.TIMEOUT)
                continue
            if panel != request.panel and not (clicked and panel == expected):
                return WithdrawResult(WithdrawOutcome.CONTEXT_CHANGED)
            if clicked:
                if current.state == BrawlScreen.PREPARATION and panel == expected:
                    visible = read_bench(image, observation=current)
                    added = [BenchHero(entry.name, entry.imprint) for entry in visible.entries
                             if entry.name not in known]
                    if (any(entry.name in known and entry.imprint is not None
                            and entry.imprint != known[entry.name] for entry in visible.entries)
                            or len(added) > 1):
                        return WithdrawResult(WithdrawOutcome.BENCH_CHANGED)
                    if len(added) == 1 and added[0].imprint is not None:
                        return WithdrawResult(WithdrawOutcome.CONFIRMED, added[0])
                if timeout.reached():
                    return WithdrawResult(WithdrawOutcome.TIMEOUT)
                continue
            if not _bench_agrees(image, current, request.bench_before):
                return WithdrawResult(WithdrawOutcome.BENCH_CHANGED)
            control = None
            if current.state == BrawlScreen.FORMATION_SELECTION:
                control = read_withdraw_control(image, observation=current)
                if control is not None and control.source != request.source:
                    return WithdrawResult(WithdrawOutcome.SOURCE_CHANGED)
            if timeout.reached():
                return WithdrawResult(WithdrawOutcome.TIMEOUT)
            if not self.interval_is_reached(self.ACTION_TIMER, interval=self.ACTION_INTERVAL):
                continue
            if current.state == BrawlScreen.PREPARATION and not selected:
                x, y = request.source.center
                self.device.click(ClickButton((x, y, x + 1, y + 1), name="BrawlWithdrawSelect"))
                self.interval_reset(self.ACTION_TIMER, interval=self.ACTION_INTERVAL)
                selected = True
            elif control is not None:
                self.device.click(ClickButton(control.button_area, name="BrawlWithdrawMinus"))
                self.interval_reset(self.ACTION_TIMER, interval=self.ACTION_INTERVAL)
                clicked = True
                timeout.reset()
