"""Scan every bench selection without treating a stalled gesture as an endpoint."""

from dataclasses import dataclass
from enum import Enum

import numpy as np

from module.base.base import ModuleBase
from module.base.button import ClickButton
from module.base.timer import Timer
from module.logger import logger
from tasks.brawl.bench import BenchHero
from tasks.brawl.cursor import agrees_selection, confirms_next, read_cursor
from tasks.brawl.rules import BENCH_CAPACITY, RuleInputError, get_hero, validate_imprint
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


class SelectionOutcome(str, Enum):
    SELECTED = "selected"
    INVALID_REQUEST = "invalid_request"
    TARGET_MISSING = "target_missing"
    BENCH_CHANGED = "bench_changed"
    TIMEOUT = "timeout"
    SCENE_CHANGED = "scene_changed"
    CONTEXT_CHANGED = "context_changed"
    TRANSITION_MISMATCH = "transition_mismatch"
    ACTION_LIMIT = "action_limit"
    CAPACITY_CONFLICT = "capacity_conflict"


@dataclass(frozen=True)
class BenchSelectionResult:
    outcome: SelectionOutcome
    hero: BenchHero | None
    panel: PreparationPanel | None
    game_mode: BrawlMode | None


@dataclass(frozen=True)
class SelectionEvidence:
    scan: BenchScanResult
    image: np.ndarray
    pixels: np.ndarray
    selected: BenchHero | None
    expiry: Timer


class BrawlBenchScanner(ModuleBase):
    ACTION_INTERVAL = 3
    STEP_TIMEOUT = 12
    MAX_TOP_SWIPES = 3
    MAX_ACTIONS = 80
    TOP_START = (1150, 205)
    TOP_END = (1150, 520)
    ACTION_TIMER = "BrawlBenchScan"

    def select(self, hero, expected_scan, skip_first_screenshot=True):
        """Revalidate the bench, then leave the requested hero selected.

        Pages:
            in: preparation matching a previously complete bench scan
            out: the verified requested selection, or an explicit failure

        Selection is not a new complete scan. The first pass rechecks every
        expected identity, including cards below the target, before returning
        to the top. The second pass follows confirmed adjacent portraits and
        compares each visible queue with its first-pass frame. Both passes
        share a bounded action budget. Only retrying the same return-to-top
        gesture uses an interval; a newly confirmed stable queue can advance
        immediately. Neither historical card coordinates nor a changed focus
        alone can authorize a selection.
        """
        evidence = getattr(self, "_selection_evidence", None)
        self._selection_evidence = None
        invalid = BenchSelectionResult(SelectionOutcome.INVALID_REQUEST, None, None, None)
        if (not isinstance(expected_scan, BenchScanResult) or not expected_scan.coverage_complete
                or not isinstance(expected_scan.panel, PreparationPanel)
                or not isinstance(expected_scan.game_mode, BrawlMode)
                or not isinstance(hero, BenchHero)
                or not isinstance(expected_scan.heroes, tuple)
                or len(expected_scan.heroes) > BENCH_CAPACITY):
            return invalid
        names = set()
        try:
            for item in (*expected_scan.heroes, hero):
                if (not isinstance(item, BenchHero) or get_hero(item.name).name != item.name
                        or item.imprint is None):
                    return invalid
                validate_imprint(item.name, item.imprint)
            for item in expected_scan.heroes:
                if item.name in names:
                    return invalid
                names.add(item.name)
        except (RuleInputError, TypeError):
            return invalid
        if hero not in expected_scan.heroes:
            return BenchSelectionResult(SelectionOutcome.TARGET_MISSING, None,
                                        expected_scan.panel, expected_scan.game_mode)
        # A complete scan already proved its final selection. Reuse that proof
        # once, only in the exact unchanged frame and within one action interval.
        # Another screenshot, in-place mutation, elapsed time or interaction
        # invalidates it. Any traversal to a different card still uses the full
        # independent scan and portrait checks below; hidden cards are not cached.
        if (skip_first_screenshot and evidence is not None
                and evidence.scan is expected_scan and evidence.selected == hero
                and self.device.image is evidence.image and not evidence.expiry.reached()
                and np.array_equal(self.device.image, evidence.pixels)):
            logger.info("Reusing the completed scan's selection in the same unchanged frame")
            return BenchSelectionResult(SelectionOutcome.SELECTED, hero,
                                        expected_scan.panel, expected_scan.game_mode)
        return self._walk(skip_first_screenshot, expected_scan=expected_scan, target=hero)

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
        self._selection_evidence = None
        return self._walk(skip_first_screenshot)

    def _walk(self, skip_first_screenshot, *, expected_scan=None, target=None):
        heroes = []
        panel = expected_scan.panel if expected_scan is not None else None
        mode = expected_scan.game_mode if expected_scan is not None else None
        selecting = False
        verified_frames = {}
        started = False
        # Keep each immutable cursor with the exact image that produced it.
        # Comparisons must not reread old screenshots or accidentally attach
        # the latest cursor to a pending/accepted frame. New screenshots are
        # always read independently before their own pair can be accepted.
        pending_frame = None
        accepted_frame = None
        swipes = actions = 0
        top_retry = Timer(self.ACTION_INTERVAL)
        timeout = Timer(self.STEP_TIMEOUT, count=3).start()
        previous_gate = None
        gate_detail = "No bench frame has been read"

        def result(outcome):
            logger.info(f"Brawl bench scan ended: outcome={outcome.value}, "
                        f"verified={len(heroes)}, actions={actions}, "
                        f"pending_next={pending_frame is not None}; {gate_detail}")
            if expected_scan is not None:
                outcome = SelectionOutcome(outcome.value)
                return BenchSelectionResult(outcome, target if outcome == SelectionOutcome.SELECTED else None,
                                            panel, mode)
            scanned = BenchScanResult(tuple(heroes), panel, mode, outcome)
            if outcome == ScanOutcome.COMPLETE:
                self._selection_evidence = SelectionEvidence(
                    scanned, self.device.image, self.device.image.copy(), cursor.selected,
                    Timer(self.ACTION_INTERVAL).start())
            return scanned

        while True:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            observation = BrawlVision(self.device.image).observe()
            if observation.state not in (BrawlScreen.PREPARATION, BrawlScreen.UNKNOWN):
                gate_detail = f"Scene changed to {observation.state.value}; {observation.reason}"
                return result(ScanOutcome.SCENE_CHANGED)
            cursor = None
            selection_stable = False
            queue_changed = False
            if observation.state == BrawlScreen.PREPARATION:
                if panel is not None and (
                    observation.panel is not None and observation.panel != panel
                    or observation.game_mode is not None and observation.game_mode != mode
                ):
                    gate_detail = (f"Preparation changed: previous={panel}, current={observation.panel}, "
                                   f"previous_mode={mode}, current_mode={observation.game_mode}")
                    return result(ScanOutcome.CONTEXT_CHANGED)
                if observation.panel is not None and observation.game_mode is not None:
                    if panel is None:
                        panel, mode = observation.panel, observation.game_mode
                    cursor = read_cursor(self.device.image, observation=observation)
                    if cursor.panel != panel or cursor.game_mode != mode:
                        cursor = None

            if cursor is None:
                gate = (observation.state, None)
                gate_detail = (f"scene={observation.state.value}, "
                               f"panel_read={observation.panel is not None}, "
                               f"mode_read={observation.game_mode is not None}; {observation.reason}")
            else:
                # Report only a changed gate, not the varying OCR confidence
                # on every identical waiting frame. Preserve the latest detail
                # for the bounded exit so a missing name, imprint or plus can
                # be distinguished from an unconfirmed card transition.
                gate = (observation.state, cursor.selected, cursor.top, cursor.bottom,
                        cursor.empty, cursor.next_area, started, pending_frame is not None)
                selected = cursor.selected
                imprint = selected.imprint.name if selected is not None and selected.imprint is not None else None
                gate_detail = (f"selected={selected.name if selected is not None else None!r}, "
                               f"imprint={imprint}, top={cursor.top}, bottom={cursor.bottom}, "
                               f"empty={cursor.empty}, next={cursor.next_area}; "
                               f"{getattr(cursor, 'reason', '')}")
            if gate != previous_gate:
                logger.info(f"Brawl bench scan gate: {gate_detail}")
                previous_gate = gate

            if cursor is not None:
                if cursor.empty:
                    if expected_scan is not None:
                        return result(SelectionOutcome.BENCH_CHANGED)
                    if started or pending_frame is not None:
                        return result(ScanOutcome.CONTEXT_CHANGED)
                    if cursor.top and cursor.bottom and cursor.selected is None and cursor.next_area is None:
                        return result(ScanOutcome.COMPLETE)
                selected = cursor.selected
                if selected is not None and selected.imprint is not None:
                    if expected_scan is not None and selected not in expected_scan.heroes:
                        return result(SelectionOutcome.BENCH_CHANGED)
                    # Bench selections never change an imprint. A known name
                    # with a different readable imprint therefore belongs to
                    # a changed inventory, even if a next-card click is still
                    # pending. Do not downgrade this mutation to a loop or
                    # accept it as another copy of the same hero.
                    if any(hero.name == selected.name and hero.imprint != selected.imprint
                           for hero in heroes):
                        return result(ScanOutcome.CONTEXT_CHANGED)
                    if not started and cursor.top:
                        # Positive top ends this rewind request. A later full
                        # traversal may request another rewind immediately;
                        # only retries of the still-unconfirmed gesture wait.
                        top_retry.clear()
                        heroes.append(selected)
                        started = True
                        accepted_frame = (self.device.image.copy(), cursor)
                        selection_stable = True
                        queue_changed = swipes > 0
                        timeout.reset()
                    elif started and selected != heroes[-1]:
                        if pending_frame is None:
                            return result(ScanOutcome.CONTEXT_CHANGED)
                        if any(hero.name == selected.name for hero in heroes):
                            return result(ScanOutcome.TRANSITION_MISMATCH)
                        matches = confirms_next(pending_frame[0], self.device.image,
                                                before_cursor=pending_frame[1], after_cursor=cursor)
                        gate_detail += f"; pending portrait confirmation={matches}"
                        if matches is False:
                            return result(ScanOutcome.TRANSITION_MISMATCH)
                        if matches is True:
                            heroes.append(selected)
                            pending_frame = None
                            accepted_frame = (self.device.image.copy(), cursor)
                            selection_stable = True
                            queue_changed = True
                            timeout.reset()
                    if (started and pending_frame is None and selected == heroes[-1]
                            and not selection_stable):
                        # A newly selected portrait proves identity but not
                        # that its adjacent rows have finished moving. Check
                        # the next fresh frame against that accepted queue;
                        # unchanged resources alone do not preserve ordering.
                        # Unknown/occluded neighbours never prove a mutation,
                        # but also cannot authorize another stale-list action.
                        agreement = agrees_selection(accepted_frame[0], self.device.image,
                                                     before_cursor=accepted_frame[1], after_cursor=cursor)
                        gate_detail += f"; visible queue agreement={agreement}"
                        if agreement is False:
                            return result(ScanOutcome.CONTEXT_CHANGED)
                        selection_stable = agreement is True
                    if expected_scan is not None and started:
                        if tuple(heroes) != expected_scan.heroes[:len(heroes)]:
                            return result(SelectionOutcome.BENCH_CHANGED)
                        if selection_stable and not queue_changed:
                            if selecting:
                                prior_frame = verified_frames[selected.name]
                                agreement = agrees_selection(prior_frame[0], self.device.image,
                                                             before_cursor=prior_frame[1], after_cursor=cursor)
                                if agreement is False:
                                    return result(SelectionOutcome.BENCH_CHANGED)
                                selection_stable = agreement is True
                            else:
                                verified_frames.setdefault(selected.name, accepted_frame)
                        if selection_stable and pending_frame is None and selected == heroes[-1]:
                            if ((cursor.bottom and cursor.next_area is None
                                 and len(heroes) != len(expected_scan.heroes))
                                    or (len(heroes) == len(expected_scan.heroes)
                                        and cursor.next_area is not None)):
                                return result(SelectionOutcome.BENCH_CHANGED)
                            if selecting and selected == target and not queue_changed:
                                return result(SelectionOutcome.SELECTED)
                    # Ten is the confirmed seat limit, not an inferred endpoint.
                    # Reaching it forbids advancing to an eleventh card, but an
                    # unread lower edge still cannot certify the list. A visible
                    # next card contradicts the inventory and must stay partial.
                    if (selection_stable and len(heroes) >= BENCH_CAPACITY
                            and (len(heroes) > BENCH_CAPACITY or cursor.next_area is not None)):
                        return result(ScanOutcome.CAPACITY_CONFLICT)
                    # A lower endpoint is meaningful only after continuous,
                    # identity-verified advancement from the upper endpoint.
                    # Check it before timeout and before issuing another action.
                    if (selection_stable and not queue_changed and pending_frame is None and selected == heroes[-1]
                            and cursor.bottom and cursor.next_area is None):
                        if expected_scan is not None:
                            if selected == target:
                                return result(SelectionOutcome.SELECTED)
                            # A full verification may finish below the target.
                            # Restart only the traversal state, never its action
                            # retry timer or budget. The next fresh frame must prove
                            # the top before another adjacent card can be used.
                            selecting = True
                            heroes = []
                            started = False
                            accepted_frame = None
                            swipes = 0
                            timeout.reset()
                            continue
                        return result(ScanOutcome.COMPLETE)

            # A changed selection owns a new identity, but its moving rows are
            # not yet reusable selection evidence. Require the next fresh
            # agreeing queue before either another click or COMPLETE/SELECTED;
            # otherwise a caller could open formation from that transient frame.
            if queue_changed:
                continue
            if timeout.reached():
                return result(ScanOutcome.TIMEOUT)
            # An exhausted action budget prohibits another gesture, but the
            # final issued click still needs its normal confirmation window.
            # Returning now would discard a delayed, valid final selection.
            if actions >= self.MAX_ACTIONS and pending_frame is None:
                return result(ScanOutcome.ACTION_LIMIT)
            if cursor is None or cursor.empty:
                continue
            if not started and not cursor.top:
                if swipes >= self.MAX_TOP_SWIPES:
                    return result(ScanOutcome.ACTION_LIMIT)
                # This timer belongs only to repeated return-to-top gestures.
                # It cannot hold a verified top/next-card state behind an
                # unrelated formation or previous card click's cooldown.
                if not top_retry.reached():
                    continue
                self.device.swipe(self.TOP_START, self.TOP_END, name="BrawlBenchTop")
                top_retry.reset()
                swipes += 1
                actions += 1
                continue
            if (selection_stable and not queue_changed and pending_frame is None and cursor.selected == heroes[-1]
                    and len(heroes) < BENCH_CAPACITY
                    and cursor.next_area is not None and not cursor.bottom):
                left, top, right, bottom = cursor.next_area
                # The reader supplies the current adjacent card, never a stale
                # historical coordinate. Constrain the center to the bench body
                # so malformed observations cannot click the formation plus.
                x, y = (left + right) // 2, (top + bottom) // 2
                if not (1000 <= x < 1280 and 327 <= y < 550 and left < right and top < bottom):
                    continue
                pending_frame = (self.device.image.copy(), cursor)
                self.device.click(ClickButton((x, y, x + 1, y + 1), name="BrawlBenchNext"))
                actions += 1
                timeout.reset()
