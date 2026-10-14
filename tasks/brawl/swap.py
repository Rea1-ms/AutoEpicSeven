"""Swap one selected bench hero into a positively verified occupied position."""

from dataclasses import dataclass
from enum import Enum

from module.base.base import ModuleBase
from module.base.button import ClickButton
from module.base.timer import Timer
from tasks.brawl.bench import BenchHero, read_bench
from tasks.brawl.board import read_swap_targets
from tasks.brawl.cursor import read_cursor
from tasks.brawl.field import SUPPORTED_FIELD_HEROES, read_field
from tasks.brawl.placement import _selected_entry
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
class SwapRequest:
    hero: BenchHero
    outgoing_name: str | None
    target: tuple[int, int]
    screen_row: int
    screen_column: int
    panel: PreparationPanel
    game_mode: BrawlMode
    bench_before: BenchScanResult | None = None


class SwapOutcome(str, Enum):
    CONFIRMED = "confirmed"
    TIMEOUT = "timeout"
    SCENE_CHANGED = "scene_changed"
    CONTEXT_CHANGED = "context_changed"
    SELECTION_CHANGED = "selection_changed"
    TARGET_UNAVAILABLE = "target_unavailable"


def capture_swap(image, screen_row, screen_column):
    """Require a named incoming card and a known outgoing sprite at this cell.

    Full field and bench do not imply an empty placement target. The outgoing
    imprint remains unknown; the verified swap does not reconstruct the bench.
    """
    if (type(screen_row) is not int or not 0 <= screen_row < 7
            or type(screen_column) is not int or not 0 <= screen_column < 4):
        raise ValueError("A screen row 0..6 and column 0..3 are required")
    cursor = read_cursor(image)
    if (cursor.panel is None or cursor.game_mode is None or cursor.selected is None
            or cursor.selected.imprint is None or cursor.selected.name not in SUPPORTED_FIELD_HEROES):
        raise ObservationError("A selected hero with a supported field pose is required")
    target = (int(420 + screen_column * 124.5 - (screen_row % 2) * 62.25 + .5),
              241 + screen_row * 56)
    field = read_field(image)
    found = [hero for hero in field.heroes
             if all(abs(a - b) <= 8 for a, b in zip(hero.foot, target))]
    if (len(found) != 1 or found[0].name == cursor.selected.name
            or any(hero.name == cursor.selected.name for hero in field.heroes)):
        raise ObservationError("The outgoing hero at the target cannot be independently verified")
    return SwapRequest(cursor.selected, found[0].name, target, screen_row, screen_column,
                       cursor.panel, cursor.game_mode)


def _complete_bench(scan):
    """A caller's partial, duplicate or malformed list cannot prove a swap."""
    if (not isinstance(scan, BenchScanResult) or not scan.coverage_complete
            or scan.panel is None or scan.game_mode is None
            or not 1 <= len(scan.heroes) <= BENCH_CAPACITY):
        return False
    names = []
    for hero in scan.heroes:
        if not isinstance(hero, BenchHero) or hero.imprint is None:
            return False
        try:
            canonical = get_hero(hero.name).name
            if validate_imprint(hero.name, hero.imprint) != hero.imprint:
                return False
        except RuleInputError:
            return False
        if canonical != hero.name or hero.name in names:
            return False
        names.append(hero.name)
    return True


def capture_identification_swap(image, screen_row, screen_column, before_scan):
    """Capture an explicit exchange used to read an unknown hero on the bench.

    The caller first completes a fresh bench scan, selects a supported incoming
    card, and supplies the intended screen cell. This request does not assert
    that the cell is occupied: the loop must independently prove that on the
    selection screen before clicking it. Ten occupied bench seats are valid
    because an exchange preserves both deployed and bench counts.
    """
    if (type(screen_row) is not int or not 0 <= screen_row < 7
            or type(screen_column) is not int or not 0 <= screen_column < 4):
        raise ValueError("A screen row 0..6 and column 0..3 are required")
    if not _complete_bench(before_scan):
        raise ObservationError("A complete, unambiguous bench scan is required")
    cursor = read_cursor(image)
    if (cursor.panel is None or cursor.game_mode is None or cursor.selected is None
            or cursor.selected.imprint is None or cursor.selected.name not in SUPPORTED_FIELD_HEROES
            or cursor.panel.deployed == 0):
        raise ObservationError("A supported selected card and a deployed hero are required")
    if (cursor.panel != before_scan.panel or cursor.game_mode != before_scan.game_mode
            or cursor.selected not in before_scan.heroes):
        raise ObservationError("The selected card or preparation context differs from the bench scan")
    known_bench = {hero.name: hero for hero in before_scan.heroes}
    visible = read_bench(image)
    if (visible.panel != before_scan.panel or not visible.entries
            or any(entry.name not in known_bench
                   or entry.imprint != known_bench[entry.name].imprint
                   for entry in visible.entries)):
        raise ObservationError("The visible bench conflicts with the complete scan")
    field = read_field(image)
    if field.panel != cursor.panel or any(hero.name in known_bench for hero in field.heroes):
        raise ObservationError("Known deployed heroes conflict with the complete bench scan")
    target = (int(420 + screen_column * 124.5 - (screen_row % 2) * 62.25 + .5),
              241 + screen_row * 56)
    return SwapRequest(cursor.selected, None, target, screen_row, screen_column,
                       cursor.panel, cursor.game_mode, before_scan)


def identify_swapped_hero(request, after_scan, outcome):
    """Read an outgoing identity only from a confirmed exchange and full scans.

    Focus can automatically advance to an unchanged neighbor, and list order
    can change after a swap. Compare named, imprinted heroes as sets instead.
    Exactly the incoming hero must disappear and one new hero must appear;
    any additional name/imprint mutation invalidates the identification. The
    caller retains the confirmed action outcome and performs the second scan
    separately; this function neither clicks nor guesses a name from focus.
    """
    if (not isinstance(request, SwapRequest) or outcome != SwapOutcome.CONFIRMED
            or request.outgoing_name is not None or not _complete_bench(request.bench_before)
            or not _complete_bench(after_scan)):
        return None
    before = request.bench_before
    if (before.panel != request.panel or after_scan.panel != request.panel
            or before.game_mode != request.game_mode or after_scan.game_mode != request.game_mode
            or len(before.heroes) != len(after_scan.heroes) or request.hero not in before.heroes):
        return None
    removed = set(before.heroes) - set(after_scan.heroes)
    added = set(after_scan.heroes) - set(before.heroes)
    if removed != {request.hero} or len(added) != 1:
        return None
    outgoing = next(iter(added))
    return outgoing if outgoing.name not in {hero.name for hero in before.heroes} else None


class BrawlSwap(ModuleBase):
    ACTION_INTERVAL = 3
    STEP_TIMEOUT = 12
    ACTION_TIMER = "BrawlSwap"

    def swap_selected(self, request, skip_first_screenshot=True):
        """Open the plus, click one occupied target, then verify the new sprite.

        Pages:
            in: preparation matching the captured incoming and outgoing heroes
            out: verified preparation, interruption or last unconfirmed frame

        The target is clicked once. The after-swap bench focus can automatically
        move to a neighbor, so it cannot identify the new deployed hero. Same
        round, mode, full panel and the incoming pose at the requested position
        are required; current imprints and the complete bench remain unknown.
        """
        if (not isinstance(request, SwapRequest) or request.hero.imprint is None
                or request.hero.name not in SUPPORTED_FIELD_HEROES
                or (request.outgoing_name not in SUPPORTED_FIELD_HEROES
                    and not (request.outgoing_name is None and _complete_bench(request.bench_before)))):
            raise ValueError("A supported captured swap request is required")
        timeout = Timer(self.STEP_TIMEOUT).start()
        opened = grid_seen = clicked = returned = False
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
                return SwapOutcome.SCENE_CHANGED
            if current.game_mode is not None and current.game_mode != request.game_mode:
                return SwapOutcome.CONTEXT_CHANGED
            if current.state == BrawlScreen.UNKNOWN or current.game_mode is None:
                if timeout.reached():
                    return SwapOutcome.TIMEOUT
                continue
            if clicked and current.state == BrawlScreen.PREPARATION:
                returned = True
            if returned and current.state == BrawlScreen.FORMATION_SELECTION:
                return SwapOutcome.SCENE_CHANGED
            if not clicked and grid_seen and current.state == BrawlScreen.PREPARATION:
                return SwapOutcome.SCENE_CHANGED
            if current.state == BrawlScreen.FORMATION_SELECTION:
                if not opened:
                    return SwapOutcome.SCENE_CHANGED
                grid_seen = True
            panel = current.panel
            if current.state == BrawlScreen.FORMATION_SELECTION:
                try:
                    panel = vision.preparation_panel()
                except ObservationError:
                    panel = None
            if panel is None:
                if timeout.reached():
                    return SwapOutcome.TIMEOUT
                continue
            if panel != request.panel:
                return SwapOutcome.CONTEXT_CHANGED
            if clicked:
                if current.state == BrawlScreen.PREPARATION:
                    field = read_field(image, observation=current)
                    incoming = [hero for hero in field.heroes if hero.name == request.hero.name
                                and all(abs(a - b) <= 8 for a, b in zip(hero.foot, request.target))]
                    if len(incoming) == 1 and not any(
                            hero.name == request.outgoing_name
                            and all(abs(a - b) <= 8 for a, b in zip(hero.foot, request.target))
                            for hero in field.heroes):
                        return SwapOutcome.CONFIRMED
                if timeout.reached():
                    return SwapOutcome.TIMEOUT
                continue
            entry = _selected_entry(image, current)
            if entry is None or entry.imprint is None:
                if timeout.reached():
                    return SwapOutcome.TIMEOUT
                continue
            if BenchHero(entry.name, entry.imprint) != request.hero:
                return SwapOutcome.SELECTION_CHANGED
            if timeout.reached():
                return SwapOutcome.TIMEOUT
            if not self.interval_is_reached(self.ACTION_TIMER, interval=self.ACTION_INTERVAL):
                continue
            if current.state == BrawlScreen.PREPARATION:
                # A stale selected card must not swap a different outgoing hero.
                try:
                    if request.outgoing_name is None:
                        fresh = capture_identification_swap(
                            image, request.screen_row, request.screen_column, request.bench_before)
                    else:
                        fresh = capture_swap(image, request.screen_row, request.screen_column)
                except ObservationError:
                    return SwapOutcome.TARGET_UNAVAILABLE
                if fresh != request:
                    return SwapOutcome.CONTEXT_CHANGED
                self.device.click(ClickButton(entry.plus_area, name="BrawlSwapOpen"))
                self.interval_reset(self.ACTION_TIMER, interval=self.ACTION_INTERVAL)
                opened = True
                continue
            targets = read_swap_targets(image, observation=current)
            if targets is None:
                continue
            cell = next((cell for cell in targets.occupied
                         if (cell.screen_row, cell.screen_column) == (
                             request.screen_row, request.screen_column)), None)
            if cell is None or cell.center != request.target:
                return SwapOutcome.TARGET_UNAVAILABLE
            x, y = cell.center
            self.device.click(ClickButton((x, y, x + 1, y + 1), name="BrawlSwapCell"))
            self.interval_reset(self.ACTION_TIMER, interval=self.ACTION_INTERVAL)
            clicked = True
            timeout.reset()
