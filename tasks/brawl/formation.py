"""Arrange a continuously tracked roster, confirming every individual step.

An empty-field opening or the separate saved-run recovery establishes hero
identities through verified transitions, not animated field sprites.
An interrupted action invalidates the session; the last confirmed roster is
retained for diagnostics only and cannot authorize another click.
"""

from dataclasses import dataclass, replace
from enum import Enum

import numpy as np

from module.base.button import ClickButton
from module.base.timer import Timer
from module.logger import logger
from tasks.brawl.bench import BenchHero, read_bench
from tasks.brawl.board import _cells, logical_to_screen, read_formation_grid
from tasks.brawl.cursor import read_cursor
from tasks.brawl.observation_cache import invalidate_panel_observation_cache
from tasks.brawl.placement import _selected_entry
from tasks.brawl.roster import (
    FormationKind,
    RosterState,
    begin_roster,
    commit_step,
    derive_after,
    next_step,
    reconcile,
    reconcile_transfer,
)
from tasks.brawl.rules import RuleInputError, validate_class_bonuses
from tasks.brawl.scanner import BrawlBenchScanner, SelectionOutcome
from tasks.brawl.strategy import HeroState, recommend_lineup
from tasks.brawl.vision import BrawlScreen, BrawlVision, ObservationError
from tasks.brawl.withdraw import _bench_agrees, read_withdraw_control


class FormationOutcome(str, Enum):
    READY = "ready"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass(frozen=True)
class FormationResult:
    outcome: FormationOutcome
    roster: RosterState | None
    reason: str = ""
    steps: tuple = ()


@dataclass(frozen=True)
class OpeningEvidence:
    scan: object
    roster: RosterState
    image: np.ndarray
    pixels: np.ndarray
    expiry: Timer


def _indices(cells):
    return frozenset((cell.screen_row, cell.screen_column) for cell in cells)


def _occupied(roster):
    return frozenset(hero.cell for hero in roster.heroes if hero.cell is not None)


class BrawlFormation(BrawlBenchScanner):
    MAX_FORMATION_STEPS = 40
    # The user confirmed that the top Round label closes selection without
    # opening another control, with either an occupied or empty bench. Clicking
    # the already selected portrait instead opens hero details. This area is
    # used only after a requested grid, counters and the bench all agree.
    ROUND_CANCEL_AREA = (620, 15, 660, 30)

    @property
    def roster(self):
        return getattr(self, "_roster", None)

    @property
    def valid(self):
        return getattr(self, "_valid", False)

    @property
    def verified_scan(self):
        if not self.valid:
            raise ObservationError("An invalid session has no actionable bench scan")
        return self._scan_result

    def invalidate(self, reason="External actions were not tracked"):
        """The outer game loop must call this on manual or unobserved changes."""
        self._valid = False
        self._opening_evidence = None
        self._selection_evidence = None
        self._economy_before = None
        return FormationResult(FormationOutcome.FAILED, self.roster, reason)

    def begin_economy(self):
        """Suspend formation actions until one external transaction is proved."""
        if not self.valid:
            raise ObservationError("Economic actions require a verified formation session")
        self._opening_evidence = None
        self._selection_evidence = None
        self._valid = False
        self._economy_before = self.roster
        return self.roster

    def confirm_economy(self, before, expected_panel, *, hero_results=(), removed=(),
                        skip_first_screenshot=True):
        """Commit a requested purchase, cancellation or sale from fresh evidence.

        Pages: stable preparation after the external action in; freshly verified
        preparation out. The exact expected resources come from the action's
        observed quote, never from a missing card or a result-dialog count.
        """
        if (getattr(self, "_economy_before", None) is not before
                or self.roster is not before or self.valid):
            return self.invalidate("There is no matching pending economic transaction")
        try:
            scan = self.scan(skip_first_screenshot=skip_first_screenshot)
            if scan.panel != expected_panel or scan.game_mode != before.game_mode:
                raise ObservationError("Economic return differs from the verified quote or preparation")
            if removed:
                reconcile_transfer(before, scan, _occupied(before), removed, hero_results)
            else:
                reconcile(before, scan, _occupied(before), hero_results)
            occupied = self._probe(scan, _occupied(before))
            updated = (reconcile_transfer(before, scan, occupied, removed, hero_results) if removed
                       else reconcile(before, scan, occupied, hero_results))
        except (RuleInputError, ObservationError) as exc:
            return self.invalidate(str(exc))
        self._roster, self._scan_result = updated, scan
        self._economy_before = None
        self._valid = True
        return FormationResult(FormationOutcome.READY, self.roster)

    def begin(self, skip_first_screenshot=True):
        """Initialize in first-round preparation with an explicitly empty field."""
        self._valid = False
        self._roster = None
        self._opening_evidence = None
        try:
            scan = self.scan(skip_first_screenshot=skip_first_screenshot)
            self._roster = begin_roster(scan)
        except (RuleInputError, ObservationError) as exc:
            return self.invalidate(str(exc))
        self._scan_result = scan
        self._valid = True
        # Only the immediate begin -> arrange handoff may reuse this scan.
        # Keep both identity and pixels: a new screenshot (even identical) or
        # an in-place mutation cannot inherit the previous frame's proof. The
        # short expiry also rejects a delayed caller holding a stale image.
        self._opening_evidence = OpeningEvidence(
            scan, self.roster, self.device.image, self.device.image.copy(),
            Timer(self.ACTION_INTERVAL).start())
        return FormationResult(FormationOutcome.READY, self.roster)

    def _opening_matches(self, evidence):
        # Callers check session validity before entering an action. Here it is
        # already false while the first action awaits its post-action proof.
        return (evidence is not None and self.roster is evidence.roster
                and self._scan_result is evidence.scan
                and self.device.image is evidence.image
                and not evidence.expiry.reached()
                and np.array_equal(self.device.image, evidence.pixels))

    def refresh(self, hero_results=(), skip_first_screenshot=True):
        """Reconcile explicitly collected results and fresh bench/field evidence.

        This is for a caller that has tracked all intervening game actions.
        Occupation cannot detect two manually exchanged identities, so manual
        intervention or a missed acquisition must invalidate the session instead.
        No result-page dismissal, summon, transfer or combat action occurs here.
        """
        self._opening_evidence = None
        if not self.valid:
            return self.invalidate("A verified roster session is required")
        self._valid = False
        try:
            scan = self.scan(skip_first_screenshot=skip_first_screenshot)
            # Validate the event chain before opening the grid. The expectation
            # is not committed until a fresh occupied set independently agrees.
            reconcile(self.roster, scan, _occupied(self.roster), hero_results)
            occupied = self._probe(scan, _occupied(self.roster))
            updated = reconcile(self.roster, scan, occupied, hero_results)
        except (RuleInputError, ObservationError) as exc:
            return self.invalidate(str(exc))
        self._roster, self._scan_result = updated, scan
        self._valid = True
        return FormationResult(FormationOutcome.READY, self.roster)

    def arrange(self, class_bonuses, *, hero_results=(), skip_first_screenshot=True):
        """Choose a lineup, arrange it one verified step at a time, then stop.

        Pages: stable preparation in; stable preparation on success. A failure
        retains the unconfirmed frame and invalidates the session. Local grid
        loops always return to preparation before scanning the bench again.
        Class bonuses must be supplied explicitly; this never starts combat.
        """
        evidence = getattr(self, "_opening_evidence", None)
        # Consume on entry, including failures. Neither another arrange call
        # nor a post-action scan can reissue this opening-only authorization.
        self._opening_evidence = None
        if class_bonuses is None:
            raise RuleInputError("Class bonuses must be explicitly supplied, including an empty mapping")
        bonuses = validate_class_bonuses(class_bonuses)
        if (self.valid and skip_first_screenshot and isinstance(hero_results, tuple)
                and not hero_results and self._opening_matches(evidence)):
            logger.info("Reusing the just-completed opening scan in the same frame")
        else:
            evidence = None
            refreshed = self.refresh(hero_results, skip_first_screenshot)
            if refreshed.outcome == FormationOutcome.FAILED:
                return refreshed
        completed = []
        try:
            if not self.roster.heroes:
                raise RuleInputError("No acquired heroes are available to arrange")
            recommendation = recommend_lineup(
                tuple(HeroState(hero.name, hero.imprint) for hero in self.roster.heroes),
                self.roster.panel.level, bonuses)
            desired = {item.name: logical_to_screen(item.row, item.column)
                       for item in recommendation.placements}
            for _ in range(self.MAX_FORMATION_STEPS):
                action = next_step(self.roster, desired)
                if action is None:
                    return FormationResult(FormationOutcome.COMPLETE, self.roster,
                                           steps=tuple(completed))
                opening_evidence, evidence = evidence, None
                self._execute_step(action, opening_evidence=opening_evidence)
                completed.append(action)
            if next_step(self.roster, desired) is None:
                return FormationResult(FormationOutcome.COMPLETE, self.roster, steps=tuple(completed))
            raise ObservationError("Formation action budget exhausted")
        except (RuleInputError, ObservationError) as exc:
            self.invalidate(str(exc))
            return FormationResult(FormationOutcome.FAILED, self.roster, str(exc), tuple(completed))

    def _execute_step(self, action, *, opening_evidence=None):
        if not self.valid:
            raise ObservationError("An invalid session cannot execute formation steps")
        before = self.roster
        expected = derive_after(before, action)
        # Invalidate before any interaction. Only complete post-action proof can
        # restore validity, including when a device exception escapes this call.
        self._valid = False
        scan = self._scan_result
        if action.kind in (FormationKind.PLACE, FormationKind.SWAP):
            hero = next(hero for hero in before.heroes if hero.name == action.hero)
            requested = BenchHero(hero.name, hero.imprint)
            already_selected = False
            if (self._opening_matches(opening_evidence) and scan.heroes
                    and scan.heroes[-1] == requested):
                # A complete scan ends on its last card. Re-read this exact
                # frame's selection and endpoint; do not click the portrait or
                # walk the same list again merely to select that same card.
                cursor = read_cursor(self.device.image)
                already_selected = (cursor.selected == requested and cursor.empty is False
                                    and cursor.bottom is True and cursor.next_area is None
                                    and cursor.panel == before.panel
                                    and cursor.game_mode == before.game_mode
                                    and self._opening_matches(opening_evidence))
            if already_selected:
                logger.info("The opening scan already selected the requested last bench hero")
            else:
                selected = self.select(requested, scan)
                if (selected.outcome != SelectionOutcome.SELECTED
                        or selected.hero != requested
                        or selected.panel != before.panel or selected.game_mode != before.game_mode):
                    raise ObservationError("The complete bench did not confirm the requested selection")
        self._grid_action(scan, _occupied(before), action=action, expected_panel=expected.panel)
        after_scan = self.scan()
        # A lower count or a returned preparation page alone cannot commit.
        # Check the full bench before the reversible occupation probe, then
        # check it together with the actual occupied cells for the final commit.
        commit_step(before, action, after_scan, _occupied(expected))
        occupied = self._probe(after_scan, _occupied(expected))
        self._roster = commit_step(before, action, after_scan, occupied)
        self._scan_result = after_scan
        self._valid = True
        # Repeated verified placements/moves share control names with recovery
        # swaps. Only the full bench and post-action occupied-set commit proves
        # progress; clear the click history here, never on an attempted click,
        # a read-only probe, an unknown frame or a merely returned preparation.
        self.device.click_record_clear()

    def _probe(self, scan, expected_cells):
        if not expected_cells:
            if scan.panel.deployed != 0:
                raise ObservationError("The empty roster disagrees with the deployed count")
            return frozenset()
        return self._grid_action(scan, expected_cells)

    def _click_formation(self, area, name):
        self._selection_evidence = None
        self.device.click(ClickButton(area, name=name))
        # Only a repeat of this same action owns its cooldown. A verified new
        # preparation or grid is sufficient for the next distinct action; the
        # previous selection/cancel must not impose an extra fixed wait there.
        # Actual formation actions remain one-shot through the pending flags.
        self.interval_reset(name, interval=self.ACTION_INTERVAL)

    def _grid_action(self, scan, expected_cells, *, action=None, expected_panel=None,
                     opened_grid=None, observation=None):
        """Select once, act once, and positively return to preparation.

        With no action this probes a known field cell and closes the grid. Grid
        evidence is captured before closing, and preparation/bench are checked
        again afterwards. No stale pixel coordinate authorizes a destination.

        Recovery may hand over an already discovered grid and its explicit
        same-frame observation. This path only closes a read-only selection;
        current geometry and bench are still verified before every attempt.
        The loop must return to preparation before recovery can identify heroes.
        """
        probe = action is None
        # When a bench card exists, its plus exposes the whole
        # occupied set directly. This reads positions without selecting or
        # swapping a field hero, including at full capacity. Only an empty
        # bench requires selecting a known field source for this read-only check.
        probe_plus = probe and bool(scan.heroes)
        source = (None if probe_plus else min(expected_cells)) if probe else action.source
        if opened_grid is not None:
            if (not probe or observation is None or observation.state != BrawlScreen.FORMATION_SELECTION
                    or observation.panel != scan.panel or observation.game_mode != scan.game_mode
                    or opened_grid.panel != scan.panel or _indices(opened_grid.occupied) != expected_cells):
                raise ObservationError("Discovery requires a complete same-frame read-only grid")
            selected = opened_grid.selected_source
            source = None if selected is None else (selected.screen_row, selected.screen_column)
            if (probe_plus and source is not None) or (not probe_plus and source not in expected_cells):
                raise ObservationError("The discovered selection source does not match its origin")
        elif observation is not None:
            raise ObservationError("An initial observation requires a discovered selection grid")
        expected_panel = scan.panel if probe else expected_panel
        timeout = Timer(self.STEP_TIMEOUT).start()
        opened = grid_seen = opened_grid is not None
        clicked = returned = False
        cancel_attempts = 0
        cancel_name = "BrawlResumeCancelRound" if opened_grid is not None else "BrawlFormationCancelRound"
        observed_cells = None
        skip_first_screenshot = True

        def failed(reason):
            logger.warning(
                f"Brawl formation stopped: {reason}; state={current.state.value}, "
                f"observation_reason={current.reason!r}, probe={probe}, opened={opened}, "
                f"clicked={clicked}, returned={returned}, cancel_attempts={cancel_attempts}")
            return ObservationError(reason)

        while True:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()
            image = self.device.image
            vision = BrawlVision(image)
            # Consume the caller's observation only for the unchanged first
            # screenshot. Every subsequent frame acquires its own observation;
            # a previous panel can never authorize a cancellation retry.
            current = observation if observation is not None else vision.observe(read_panel=False)
            observation = None
            if current.state not in (BrawlScreen.PREPARATION, BrawlScreen.FORMATION_SELECTION,
                                     BrawlScreen.UNKNOWN):
                raise failed("The formation scene changed")
            if current.game_mode is not None and current.game_mode != scan.game_mode:
                raise failed("The formation game mode changed")
            if current.state == BrawlScreen.UNKNOWN or current.game_mode is None:
                if timeout.reached():
                    raise failed("Formation observation timed out")
                continue
            if current.state == BrawlScreen.FORMATION_SELECTION:
                if not opened or returned:
                    raise failed("An unrequested selection grid appeared")
                grid_seen = True
            elif grid_seen and not clicked:
                raise failed("Selection closed before its requested action")
            elif clicked:
                # Even unread preparation closes the pending grid. A later
                # grid is a new interaction and cannot confirm the old click.
                returned = True
            # Template-only scene checks continue during the action interval.
            # OCR is fresh at an actionable boundary or at positive return;
            # intermediate grid/animation frames cannot authorize another click.
            retry_cancel = clicked and probe and current.state == BrawlScreen.FORMATION_SELECTION
            if clicked and current.state == BrawlScreen.FORMATION_SELECTION:
                if timeout.reached():
                    raise failed("Formation response timed out")
                # A lost read-only cancellation may be requested again, but a
                # move, placement, swap or withdrawal must never be repeated.
                # Retry only the still-open grid: any observed preparation ends
                # this interaction, even if its counters are unreadable. The
                # full current grid/bench/source checks below remain mandatory.
                # Three total attempts share the original response deadline;
                # retry clicks must not renew it and hide a persistent failure.
                if (not retry_cancel or cancel_attempts >= 3
                        or not self.interval_is_reached(cancel_name,
                                                        interval=self.ACTION_INTERVAL)):
                    continue
            if opened and not clicked and current.state == BrawlScreen.PREPARATION:
                if timeout.reached():
                    raise failed("Formation grid did not open")
                continue
            panel = current.panel
            if panel is None and not current.reason:
                try:
                    panel = vision.preparation_panel()
                except ObservationError:
                    panel = None
            if panel is None:
                if timeout.reached():
                    raise failed("Formation counters remained unreadable")
                continue
            if panel != scan.panel and not (clicked and panel == expected_panel):
                raise failed("The formation context changed")
            # Grid observations omit counters by default. Attach the panel
            # verified against this frame's numeric glyphs so downstream
            # readers share it without another OCR request.
            current = replace(current, panel=panel)
            # Positive return precedes both the timeout and every action branch.
            if clicked and current.state == BrawlScreen.PREPARATION and panel == expected_panel:
                if probe and not _bench_agrees(image, current, scan):
                    raise failed("The bench changed while closing selection")
                return observed_cells
            if timeout.reached():
                raise failed("Formation response timed out")
            if clicked and not retry_cancel:
                continue
            viewport = read_bench(image, observation=current) if scan.heroes else None
            if not _bench_agrees(image, current, scan, viewport=viewport):
                raise failed("The current bench no longer matches its complete scan")
            if current.state == BrawlScreen.PREPARATION:
                if opened:
                    continue
                if source is not None:
                    cell = next(cell for cell in _cells()
                                if (cell.screen_row, cell.screen_column) == source)
                    x, y = cell.center
                    area = (x, y, x + 1, y + 1)
                else:
                    entry = _selected_entry(image, current, viewport=viewport)
                    hero = scan.heroes[-1] if probe else next(
                        hero for hero in scan.heroes if hero.name == action.hero)
                    if entry is None or BenchHero(entry.name, entry.imprint) != hero:
                        raise failed("The requested bench hero is no longer selected")
                    area = entry.plus_area
                self._click_formation(area, "BrawlFormationOpen")
                opened = True
                timeout.reset()
                continue
            grid = read_formation_grid(image, observation=current, expected_source=source,
                                       expected_target=None if probe else action.target)
            if grid is None:
                continue
            if grid.panel != scan.panel or _indices(grid.occupied) != expected_cells:
                raise failed("The actual occupied cells differ from the tracked roster")
            if source is None:
                entry = _selected_entry(image, current, viewport=viewport)
                hero = scan.heroes[-1] if probe else next(
                    hero for hero in scan.heroes if hero.name == action.hero)
                if (grid.selected_source is not None or entry is None
                        or BenchHero(entry.name, entry.imprint) != hero):
                    raise failed("The selected hero changed inside the grid")
            if probe:
                observed_cells = _indices(grid.occupied)
                # _bench_agrees above proves an empty queue with a fresh asset,
                # and checks all visible names/imprints for a nonempty queue.
                area, name = self.ROUND_CANCEL_AREA, cancel_name
            elif action.kind == FormationKind.WITHDRAW:
                control = read_withdraw_control(image, observation=current)
                if control is None:
                    continue
                if (control.panel != scan.panel
                        or (control.source.screen_row, control.source.screen_column) != source):
                    raise failed("The withdrawal control belongs to a different source")
                area, name = control.button_area, "BrawlFormationWithdraw"
            else:
                # Occupation and the current grid anchor determine where to
                # click. A sprite may hide the intended empty cell's yellow
                # edge indefinitely; that edge must not veto a verified move.
                # Swaps still require an occupied target, while place/move
                # require an empty coordinate and the proper capacity/source.
                available = grid.occupied if action.kind == FormationKind.SWAP else grid.empty_destinations
                targets = [cell for cell in available
                           if (cell.screen_row, cell.screen_column) == action.target]
                if len(targets) != 1:
                    raise failed("The requested destination has no positive control")
                x, y = targets[0].center
                area, name = (x, y, x + 1, y + 1), "BrawlFormationTarget"
            self._click_formation(area, name)
            if action is not None and action.kind in (FormationKind.PLACE, FormationKind.WITHDRAW):
                invalidate_panel_observation_cache()
            if probe:
                cancel_attempts += 1
                if retry_cancel:
                    logger.info(f"Brawl formation cancellation retried: attempt={cancel_attempts}, "
                                f"state={current.state.value}")
            if not clicked:
                timeout.reset()
            clicked = True
