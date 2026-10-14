"""Scan every bench selection without treating a stalled gesture as an endpoint."""

from dataclasses import dataclass
from enum import Enum

from module.base.base import ModuleBase
from module.base.button import ClickButton
from module.base.timer import Timer
from tasks.brawl.bench import BenchHero
from tasks.brawl.cursor import agrees_selection, confirms_next, read_cursor
from tasks.brawl.rules import BENCH_CAPACITY
from tasks.brawl.vision import BrawlMode, BrawlScreen, BrawlVision, PreparationPanel


class ScanOutcome(str, Enum):
    COMPLETE = "complete"
    TIMEOUT = "timeout"
    SCENE_CHANGED = "scene_changed"
    CONTEXT_CHANGED = "context_changed"
    TRANSITION_MISMATCH = "transition_mismatch"
    ACTION_LIMIT = "action_limit"
    CAPACITY_CONFLICT = "capacity_conflict"


@dataclass(frozen=True)
class BenchScanResult:
    heroes: tuple[BenchHero, ...]
    panel: PreparationPanel | None
    game_mode: BrawlMode | None
    outcome: ScanOutcome

    @property
    def coverage_complete(self):
        return self.outcome == ScanOutcome.COMPLETE

    @property
    def roster_complete(self):
        # A complete bench says nothing about the identities of deployed heroes.
        # The empty board is the only presently supported whole-roster case.
        return self.coverage_complete and self.panel is not None and self.panel.deployed == 0

    @property
    def free_slots(self):
        # A partial scan cannot authorize a removal or another new hero. Only
        # the verified whole bench establishes how many of its ten seats remain.
        if not self.coverage_complete or len(self.heroes) > BENCH_CAPACITY:
            return None
        return BENCH_CAPACITY - len(self.heroes)


class BrawlBenchScanner(ModuleBase):
    ACTION_INTERVAL = 3
    STEP_TIMEOUT = 12
    MAX_TOP_SWIPES = 3
    MAX_ACTIONS = 80
    TOP_START = (1150, 205)
    TOP_END = (1150, 520)
    ACTION_TIMER = "BrawlBenchScan"

    def scan(self, skip_first_screenshot=True):
        """Return a verified top-to-bottom bench, or explicit partial evidence.

        Pages:
            in: stable brawl preparation with the bench visible
            out: the positively recognized last selection, an interruption,
                or the final unconfirmed frame after a bounded attempt

        The action budget limits attempts, not inventory size. Return-to-top
        swipes may retry because they do not choose a hero. A next-card click
        remains pending until its actual portrait becomes selected. Repeating
        that click without revalidating the pending portrait could choose a
        different hero, so unconfirmed next-card clicks are not retried.
        """
        heroes = []
        panel = mode = None
        started = False
        pending_image = None
        accepted_image = None
        swipes = actions = 0
        timeout = Timer(self.STEP_TIMEOUT, count=3).start()

        def result(outcome):
            return BenchScanResult(tuple(heroes), panel, mode, outcome)

        while True:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            observation = BrawlVision(self.device.image).observe()
            if observation.state not in (BrawlScreen.PREPARATION, BrawlScreen.UNKNOWN):
                return result(ScanOutcome.SCENE_CHANGED)
            cursor = None
            selection_stable = False
            if observation.state == BrawlScreen.PREPARATION:
                if panel is not None and (
                    observation.panel is not None and observation.panel != panel
                    or observation.game_mode is not None and observation.game_mode != mode
                ):
                    return result(ScanOutcome.CONTEXT_CHANGED)
                if observation.panel is not None and observation.game_mode is not None:
                    if panel is None:
                        panel, mode = observation.panel, observation.game_mode
                    cursor = read_cursor(self.device.image, observation=observation)
                    if cursor.panel != panel or cursor.game_mode != mode:
                        cursor = None

            if cursor is not None:
                if cursor.empty:
                    if started or pending_image is not None:
                        return result(ScanOutcome.CONTEXT_CHANGED)
                    if cursor.top and cursor.bottom and cursor.selected is None and cursor.next_area is None:
                        return result(ScanOutcome.COMPLETE)
                selected = cursor.selected
                if selected is not None and selected.imprint is not None:
                    # Bench selections never change an imprint. A known name
                    # with a different readable imprint therefore belongs to
                    # a changed inventory, even if a next-card click is still
                    # pending. Do not downgrade this mutation to a loop or
                    # accept it as another copy of the same hero.
                    if any(hero.name == selected.name and hero.imprint != selected.imprint
                           for hero in heroes):
                        return result(ScanOutcome.CONTEXT_CHANGED)
                    if not started and cursor.top:
                        heroes.append(selected)
                        started = True
                        accepted_image = self.device.image.copy()
                        selection_stable = True
                        timeout.reset()
                    elif started and selected != heroes[-1]:
                        if pending_image is None:
                            return result(ScanOutcome.CONTEXT_CHANGED)
                        if any(hero.name == selected.name for hero in heroes):
                            return result(ScanOutcome.TRANSITION_MISMATCH)
                        matches = confirms_next(pending_image, self.device.image)
                        if matches is False:
                            return result(ScanOutcome.TRANSITION_MISMATCH)
                        if matches is True:
                            heroes.append(selected)
                            pending_image = None
                            accepted_image = self.device.image.copy()
                            selection_stable = True
                            timeout.reset()
                    if (started and pending_image is None and selected == heroes[-1]
                            and not selection_stable):
                        # The action interval can span several frames after a
                        # successful selection. Check the visible neighbours
                        # against that accepted frame before another click;
                        # unchanged resources alone do not preserve ordering.
                        # Unknown/occluded neighbours never prove a mutation,
                        # but also cannot authorize another stale-list action.
                        agreement = agrees_selection(accepted_image, self.device.image)
                        if agreement is False:
                            return result(ScanOutcome.CONTEXT_CHANGED)
                        selection_stable = agreement is True
                    # Ten is the confirmed seat limit, not an inferred endpoint.
                    # Reaching it forbids advancing to an eleventh card, but an
                    # unread lower edge still cannot certify the list. A visible
                    # next card contradicts the inventory and must stay partial.
                    if selection_stable and len(heroes) >= BENCH_CAPACITY:
                        if len(heroes) > BENCH_CAPACITY or cursor.next_area is not None:
                            return result(ScanOutcome.CAPACITY_CONFLICT)
                    # A lower endpoint is meaningful only after continuous,
                    # identity-verified advancement from the upper endpoint.
                    # Check it before timeout and before issuing another action.
                    if (selection_stable and pending_image is None and selected == heroes[-1]
                            and cursor.bottom and cursor.next_area is None):
                        return result(ScanOutcome.COMPLETE)

            if timeout.reached():
                return result(ScanOutcome.TIMEOUT)
            # An exhausted action budget prohibits another gesture, but the
            # final issued click still needs its normal confirmation window.
            # Returning now would discard a delayed, valid final selection.
            if actions >= self.MAX_ACTIONS and pending_image is None:
                return result(ScanOutcome.ACTION_LIMIT)
            if cursor is None or cursor.empty:
                continue
            if not self.interval_is_reached(self.ACTION_TIMER, interval=self.ACTION_INTERVAL):
                continue
            if not started and not cursor.top:
                if swipes >= self.MAX_TOP_SWIPES:
                    return result(ScanOutcome.ACTION_LIMIT)
                self.device.swipe(self.TOP_START, self.TOP_END, name="BrawlBenchTop")
                self.interval_reset(self.ACTION_TIMER, interval=self.ACTION_INTERVAL)
                swipes += 1
                actions += 1
                continue
            if (selection_stable and pending_image is None and cursor.selected == heroes[-1]
                    and len(heroes) < BENCH_CAPACITY
                    and cursor.next_area is not None and not cursor.bottom):
                left, top, right, bottom = cursor.next_area
                # The reader supplies the current adjacent card, never a stale
                # historical coordinate. Constrain the center to the bench body
                # so malformed observations cannot click the formation plus.
                x, y = (left + right) // 2, (top + bottom) // 2
                if not (1000 <= x < 1280 and 327 <= y < 550 and left < right and top < bottom):
                    continue
                pending_image = self.device.image.copy()
                self.device.click(ClickButton((x, y, x + 1, y + 1), name="BrawlBenchNext"))
                self.interval_reset(self.ACTION_TIMER, interval=self.ACTION_INTERVAL)
                actions += 1
                timeout.reset()
