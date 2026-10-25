"""Reconstruct a normal saved run through reversible, independently checked steps."""

from dataclasses import replace

from module.base.timer import Timer
from module.logger import logger
from tasks.brawl.bench import BenchHero, read_bench
from tasks.brawl.board import _cells, read_formation_grid
from tasks.brawl.formation import (
    BrawlFormation,
    FormationOutcome,
    FormationResult,
    _indices,
)
from tasks.brawl.placement import _selected_entry
from tasks.brawl.roster import (
    FormationKind,
    FormationStep,
    RosterHero,
    RosterState,
    _scan,
)
from tasks.brawl.rules import PROFESSION_RANGES, RuleInputError, validate_class_bonuses
from tasks.brawl.scanner import SelectionOutcome
from tasks.brawl.vision import BrawlMode, BrawlScreen, BrawlVision, ObservationError
from tasks.brawl.withdraw import _bench_agrees


class BrawlResume(BrawlFormation):
    def recover(self, class_bonuses=None, skip_first_screenshot=True):
        """Recover names, imprints, original cells and current class bonuses.

        Pages: normal stable preparation in; the same preparation and original
        lineup out. Every child grid loop returns to preparation before a new
        complete scan. Unknown responses retain their screen and invalidate the
        session; recovery never starts combat, sells, summons or dismisses rewards.
        """
        self._roster = None
        self.class_bonuses = None
        self.invalidate("Saved-run reconstruction has not completed")
        try:
            if class_bonuses is None:
                raise ObservationError("Saved-run recovery requires talents confirmed on the relic page")
            bonuses = validate_class_bonuses(class_bonuses)
            if set(bonuses) != set(PROFESSION_RANGES):
                raise ObservationError("All six profession talents must be explicitly confirmed")
            scan = self.scan(skip_first_screenshot=skip_first_screenshot)
            bench = _scan(scan)
            if scan.game_mode != BrawlMode.NORMAL:
                raise ObservationError("Saved-run reconstruction supports normal mode only")
            cells = self._discover(scan)
            if len(cells) != scan.panel.deployed:
                raise ObservationError("The discovered occupied cells disagree with the deployed count")
            field = []
            for cell in sorted(cells):
                hero, scan = self._identify(scan, cells, cell)
                if hero.name in bench or any(known.name == hero.name for known in field):
                    raise ObservationError("A recovered field identity is duplicated or already on the bench")
                field.append(RosterHero(hero.name, hero.imprint, cell))
                logger.info(f"Recovered field hero: {hero.name}, imprint: {hero.imprint.name}, cell: {cell}")
            self._roster = RosterState(
                tuple(RosterHero(name, imprint) for name, imprint in bench.items()) + tuple(field),
                scan.panel, scan.game_mode)
            self._scan_result = scan
            self._valid = True
            recovered = self.roster
            # Talents come from this startup's explicit relic-page read. Never
            # infer them from a mixed profession/faction sidebar or withdraw the
            # recovered lineup for a second pass. Identity and position evidence
            # must still pass the final check before these bonuses are published.
            # No partial identity list or talent mapping becomes actionable.
            # The last verification is fresh after identity reconstruction;
            # a failed restoration cannot authorize economy or a battle.
            result = self.refresh()
            if result.outcome != FormationOutcome.READY:
                raise ObservationError(result.reason)
            if (self.roster.panel != recovered.panel or self.roster.game_mode != recovered.game_mode
                    or set(self.roster.heroes) != set(recovered.heroes)):
                raise ObservationError("The final recovery check differs from the original lineup or resources")
            self.class_bonuses = bonuses
            logger.info(f"Saved-run reconstruction complete: {len(self.roster.heroes)} heroes, talents: {bonuses}")
            return FormationResult(FormationOutcome.READY, self.roster)
        except (RuleInputError, ObservationError) as exc:
            self.class_bonuses = None
            return self.invalidate(str(exc))
        except Exception:
            self.class_bonuses = None
            self.invalidate("Saved-run reconstruction was interrupted")
            raise

    def _select_recovery_hero(self, hero, scan):
        selected = self.select(hero, scan)
        if (selected.outcome != SelectionOutcome.SELECTED or selected.hero != hero
                or selected.panel != scan.panel or selected.game_mode != scan.game_mode):
            raise ObservationError("The recovery hero was not confirmed by the complete bench")

    def _checked_scan(self, panel, mode):
        scan = self.scan()
        _scan(scan)
        if scan.panel != panel or scan.game_mode != mode:
            raise ObservationError("Recovery changed the round, mode, level, count or resources")
        return scan

    def _identify(self, scan, cells, cell):
        """Read one field hero, restore it, then verify the complete original bench."""
        before = _scan(scan)
        if scan.heroes:
            # A swap works even with ten occupied bench seats. Its identity is
            # the sole set difference between complete scans, never the focus
            # that the game may automatically move to an unchanged neighbor.
            incoming = scan.heroes[-1]
            self._select_recovery_hero(incoming, scan)
            action = FormationStep(FormationKind.SWAP, incoming.name, None, cell, None, 0)
            self._grid_action(scan, cells, action=action, expected_panel=scan.panel)
            after = self._checked_scan(scan.panel, scan.game_mode)
            actual = _scan(after)
            retained = {name: imprint for name, imprint in before.items() if name != incoming.name}
            added = set(actual) - set(before)
            if (len(added) != 1 or len(actual) != len(before)
                    or incoming.name in actual or any(actual.get(name) != value for name, value in retained.items())):
                raise ObservationError("The complete bench does not prove exactly one identification swap")
            name = added.pop()
            outgoing = BenchHero(name, actual[name])
            self._select_recovery_hero(outgoing, after)
            reverse = FormationStep(FormationKind.SWAP, outgoing.name, None, cell, incoming.name, 0)
            self._grid_action(after, cells, action=reverse, expected_panel=scan.panel)
        else:
            # With an empty bench, withdrawal creates one identifiable card.
            # The action requires the requested green source and its minus;
            # an unrecognized sprite or an arbitrary focus supplies no identity.
            panel = replace(scan.panel, deployed=scan.panel.deployed - 1)
            action = FormationStep(FormationKind.WITHDRAW, "", cell, None, None, 0)
            self._grid_action(scan, cells, action=action, expected_panel=panel)
            after = self._checked_scan(panel, scan.game_mode)
            actual = _scan(after)
            if len(actual) != 1:
                raise ObservationError("Withdrawal did not expose exactly one complete hero card")
            name, imprint = next(iter(actual.items()))
            outgoing = BenchHero(name, imprint)
            remaining = cells - {cell}
            self._select_recovery_hero(outgoing, after)
            reverse = FormationStep(FormationKind.PLACE, outgoing.name, None, cell, None, 0)
            self._grid_action(after, remaining, action=reverse, expected_panel=scan.panel)
        restored = self._checked_scan(scan.panel, scan.game_mode)
        if _scan(restored) != before:
            raise ObservationError("Restoring the identified hero did not restore the complete bench")
        # The reverse action proves the complete occupied set before restoring;
        # the next real action proves the restored set. The last hero is covered
        # by recover()'s mandatory final refresh. Extra open/cancel probes here
        # would select an unrelated next hero twice without adding identity
        # evidence. These provisional identities are never usable on failure.
        # Both swaps (or withdraw/replace) and the complete original bench now
        # agree. That is bounded recovery progress, not a two-button retry loop.
        # Reset only here, so failed/partial restorations retain their history
        # and the real device guard remains active during every pending action.
        self.device.click_record_clear()
        return outgoing, restored

    def _discover(self, scan):
        """Open a reversible selection and require the complete positive occupied set.

        An empty bench supplies no plus. Try each screen cell at most once to
        select a field hero; selecting an empty cell does not move a hero. Only
        a fresh green source and all occupied controls authorize discovery. No
        identity, removal, move or destination is inferred from these attempts.
        """
        if scan.panel.deployed == 0:
            return frozenset()
        candidates = iter(_cells())
        source = None
        opened = closed = grid_seen = False
        cells = None
        timer = Timer(self.STEP_TIMEOUT).start()
        candidate_interval = Timer(0.2)
        skip_first_screenshot = True
        while True:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()
            image = self.device.image
            vision = BrawlVision(image)
            current = vision.observe(read_panel=False)
            if (current.state not in (BrawlScreen.PREPARATION, BrawlScreen.FORMATION_SELECTION, BrawlScreen.UNKNOWN)
                    or current.game_mode not in (None, scan.game_mode)):
                raise ObservationError("The recovery discovery scene or mode changed")
            if current.state == BrawlScreen.UNKNOWN or current.game_mode is None:
                if timer.reached():
                    raise ObservationError("Recovery discovery remained unreadable")
                continue
            if current.state == BrawlScreen.FORMATION_SELECTION:
                if not opened:
                    raise ObservationError("An unrequested recovery grid appeared")
                # Cancellation has its own acknowledgement window. Repeated
                # grid frames after that click are animation, not a new grid;
                # wait for positive preparation without clicking a second time.
                grid_seen = True
            # Waiting frames still check scene/mode, but cannot authorize an
            # action. Read fresh counters and cards only when an action can run
            # or positive preparation can acknowledge cancellation. Never reuse
            # an earlier frame's panel or viewport to authorize the next click.
            if timer.reached() and not (closed and current.state == BrawlScreen.PREPARATION):
                raise ObservationError("Recovery discovery timed out")
            if closed and current.state == BrawlScreen.FORMATION_SELECTION:
                continue
            if not closed:
                # Only consecutive empty-bench probes use the short interval.
                # Every probe still resets the shared formation timer below:
                # the first probe, cancellation and later actions keep their
                # normal spacing before independent interactions. Once a grid
                # is positively seen, its one cancellation can proceed without
                # another fixed wait. Never probe again, even if its source or
                # occupied controls are not yet readable; closed prevents any
                # repeated cancellation while fresh frames acknowledge return.
                if opened and not scan.heroes and not grid_seen and current.state == BrawlScreen.PREPARATION:
                    if not candidate_interval.reached():
                        continue
                elif not grid_seen and not self.interval_is_reached(self.ACTION_TIMER, interval=self.ACTION_INTERVAL):
                    continue
            if opened and scan.heroes and current.state == BrawlScreen.PREPARATION and not closed:
                continue
            if current.panel is None:
                if current.reason:
                    if timer.reached():
                        raise ObservationError("Recovery counters remained unreadable")
                    continue
                try:
                    current = replace(current, panel=vision.preparation_panel())
                except ObservationError:
                    if timer.reached():
                        raise ObservationError("Recovery counters remained unreadable")
                    continue
            viewport = read_bench(image, observation=current) if scan.heroes else None
            if current.panel != scan.panel or not _bench_agrees(image, current, scan, viewport=viewport):
                raise ObservationError("The complete recovery context changed during discovery")
            if closed and current.state == BrawlScreen.PREPARATION:
                return cells
            if grid_seen and current.state == BrawlScreen.PREPARATION:
                raise ObservationError("Recovery selection disappeared before cancellation")
            if current.state == BrawlScreen.FORMATION_SELECTION:
                grid = read_formation_grid(image, observation=current, expected_source=source)
                if grid is None:
                    continue
                if scan.heroes and grid.selected_source is not None:
                    raise ObservationError("The discovery plus selected an unexpected field source")
                cells = _indices(grid.occupied)
                self._click_formation(self.ROUND_CANCEL_AREA, "BrawlResumeCancelRound")
                closed = True
                timer.reset()
                continue
            if scan.heroes:
                if opened:
                    continue
                entry = _selected_entry(image, current, viewport=viewport)
                if entry is None or BenchHero(entry.name, entry.imprint) != scan.heroes[-1]:
                    raise ObservationError("The final scanned hero is not selected for discovery")
                area = entry.plus_area
                name = "BrawlResumeDiscover"
            else:
                cell = next(candidates, None)
                if cell is None:
                    raise ObservationError("No requested field cell produced a complete occupied grid")
                source = (cell.screen_row, cell.screen_column)
                x, y = cell.center
                area = (x, y, x + 1, y + 1)
                # These are 28 distinct controls, each tried once. Giving them
                # one button identity would trip the real device's repeated-
                # button guard on the twelfth cell, before a late hero is found.
                # Keep that guard enabled and identify the actual cell instead.
                name = f"BrawlResumeDiscover{source[0]}_{source[1]}"
            self._click_formation(area, name)
            candidate_interval.reset()
            opened = True
            timer.reset()
