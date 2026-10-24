"""Track a new game's verified roster and plan one reversible formation step.

Cells are screen coordinates (seven rows, four columns), not strategy's logical
front/back rows. Expected transitions never prove a click succeeded: only a
complete post-action bench scan and an exact occupied-cell set may commit them.
The caller must invalidate its session when an action remains unconfirmed.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import Enum

from tasks.brawl.bench import BenchHero
from tasks.brawl.heroes import HeroResult
from tasks.brawl.rules import (
    BENCH_CAPACITY,
    TOTAL_ROUNDS,
    Imprint,
    RuleInputError,
    get_hero,
    team_capacity,
    validate_imprint,
)
from tasks.brawl.scanner import BenchScanResult, ScanOutcome
from tasks.brawl.vision import BrawlMode, PreparationPanel

Cell = tuple[int, int]


def _cell(value):
    if (not isinstance(value, tuple) or len(value) != 2
            or any(type(item) is not int for item in value)
            or not 0 <= value[0] < 7 or not 0 <= value[1] < 4):
        raise RuleInputError("A screen cell requires an explicit row 0..6 and column 0..3")
    return value


def _identity(name, imprint):
    if get_hero(name).name != name:
        raise RuleInputError("Roster names must already be canonical")
    return validate_imprint(name, imprint)


def _panel(panel):
    if not isinstance(panel, PreparationPanel):
        raise RuleInputError("A complete preparation panel is required")
    numbers = (panel.round_number, panel.level, panel.deployed, panel.capacity,
               panel.coins, panel.synergy_tokens)
    if (any(type(value) is not int for value in numbers)
            or not 1 <= panel.round_number <= TOTAL_ROUNDS
            or panel.capacity != team_capacity(panel.level)
            or not 0 <= panel.deployed <= panel.capacity
            or not 0 <= panel.coins <= 999999 or not 0 <= panel.synergy_tokens <= 999999):
        raise RuleInputError("Preparation panel values are incomplete or inconsistent")


@dataclass(frozen=True)
class RosterHero:
    name: str
    imprint: Imprint | str
    cell: Cell | None = None

    def __post_init__(self):
        object.__setattr__(self, "imprint", _identity(self.name, self.imprint))
        if self.cell is not None:
            _cell(self.cell)


@dataclass(frozen=True)
class RosterState:
    heroes: tuple[RosterHero, ...]
    panel: PreparationPanel
    game_mode: BrawlMode
    revision: int = 0

    def __post_init__(self):
        _panel(self.panel)
        if (not isinstance(self.game_mode, BrawlMode)
                or type(self.revision) is not int or self.revision < 0
                or not isinstance(self.heroes, tuple)
                or any(not isinstance(hero, RosterHero) for hero in self.heroes)):
            raise RuleInputError("A roster requires explicit immutable heroes, mode and revision")
        cells = [hero.cell for hero in self.heroes if hero.cell is not None]
        if (len({hero.name for hero in self.heroes}) != len(self.heroes)
                or len(set(cells)) != len(cells)
                or len(cells) != self.panel.deployed
                or len(self.heroes) - len(cells) > BENCH_CAPACITY):
            raise RuleInputError("Roster identities, locations and displayed counts disagree")


class FormationKind(str, Enum):
    PLACE = "place"
    SWAP = "swap"
    MOVE = "move"
    WITHDRAW = "withdraw"


@dataclass(frozen=True)
class FormationStep:
    kind: FormationKind
    hero: str
    source: Cell | None
    target: Cell | None
    outgoing_name: str | None
    revision: int


def _scan(scan):
    if (not isinstance(scan, BenchScanResult) or scan.outcome is not ScanOutcome.COMPLETE
            or not isinstance(scan.game_mode, BrawlMode) or not isinstance(scan.heroes, tuple)
            or len(scan.heroes) > BENCH_CAPACITY):
        raise RuleInputError("Only a complete bench scan can update a roster")
    _panel(scan.panel)
    result = {}
    for hero in scan.heroes:
        if not isinstance(hero, BenchHero):
            raise RuleInputError("Bench identities must be explicit and unique")
        imprint = _identity(hero.name, hero.imprint)
        if hero.name in result:
            raise RuleInputError("Bench identities must be explicit and unique")
        result[hero.name] = imprint
    return result


def _occupied(values):
    if not isinstance(values, (tuple, list, set, frozenset)):
        raise RuleInputError("An explicitly observed occupied-cell collection is required")
    cells = tuple(_cell(value) for value in values)
    if len(set(cells)) != len(cells):
        raise RuleInputError("Occupied cells must be unique")
    return frozenset(cells)


def begin_roster(scan):
    """Initialize only from a complete first-round scan of an empty board."""
    heroes = _scan(scan)
    if scan.panel.round_number != 1 or scan.panel.deployed != 0:
        raise RuleInputError("A new-game roster must start in round one with nobody deployed")
    return RosterState(tuple(RosterHero(name, imprint) for name, imprint in heroes.items()),
                       scan.panel, scan.game_mode)


def _desired(state, desired):
    if not isinstance(state, RosterState):
        raise RuleInputError("A verified roster is required")
    pairs = tuple(desired.items()) if isinstance(desired, Mapping) else desired
    if not isinstance(pairs, (tuple, list)):
        raise RuleInputError("A complete explicit desired lineup is required")
    result = {}
    owned = {hero.name for hero in state.heroes}
    for pair in pairs:
        if not isinstance(pair, tuple) or len(pair) != 2:
            raise RuleInputError("Each desired placement requires a name and screen cell")
        name, cell = pair
        if not isinstance(name, str) or name not in owned or name in result:
            raise RuleInputError("Desired heroes must be unique members of this roster")
        result[name] = _cell(cell)
    if (len(result) != min(state.panel.capacity, len(state.heroes))
            or len(set(result.values())) != len(result)):
        raise RuleInputError("Desired lineup must fill the available team with distinct cells")
    return result


def next_step(state, desired):
    """Choose one step, retaining correct heroes and using empty cells for moves."""
    wanted = _desired(state, desired)
    by_name = {hero.name: hero for hero in state.heroes}
    occupied = {hero.cell: hero for hero in state.heroes if hero.cell is not None}
    incoming = [by_name[name] for name in sorted(wanted) if by_name[name].cell is None]
    outgoing = sorted((hero for hero in occupied.values() if hero.name not in wanted),
                      key=lambda hero: (hero.cell, hero.name))
    if incoming and outgoing:
        return FormationStep(FormationKind.SWAP, incoming[0].name, None,
                             outgoing[0].cell, outgoing[0].name, state.revision)
    if outgoing:
        # Kept as an explicit capacity guard even though a full desired lineup
        # normally supplies an incoming hero for each unwanted field member.
        if len(state.heroes) - len(occupied) >= BENCH_CAPACITY:
            raise RuleInputError("No bench slot is available for withdrawal")
        return FormationStep(FormationKind.WITHDRAW, outgoing[0].name, outgoing[0].cell,
                             None, None, state.revision)
    for hero in incoming:
        if wanted[hero.name] not in occupied:
            return FormationStep(FormationKind.PLACE, hero.name, None,
                                 wanted[hero.name], None, state.revision)
    misplaced = sorted((hero for hero in occupied.values() if hero.cell != wanted[hero.name]),
                       key=lambda hero: (hero.cell, hero.name))
    for hero in misplaced:
        if wanted[hero.name] not in occupied:
            return FormationStep(FormationKind.MOVE, hero.name, hero.cell,
                                 wanted[hero.name], None, state.revision)
    if misplaced:
        # A cycle has no empty destination. A temporary cell outside every
        # desired destination breaks it without exchanging occupied cells or
        # disturbing a hero already placed correctly. At most ten of 28 cells
        # are occupied, so a spare cell exists for every valid roster.
        spare = next((row, column) for row in range(7) for column in range(4)
                     if (row, column) not in occupied and (row, column) not in wanted.values())
        hero = misplaced[0]
        return FormationStep(FormationKind.MOVE, hero.name, hero.cell, spare, None, state.revision)
    if incoming:
        raise RuleInputError("The desired lineup cannot be reached from this roster")
    return None


def derive_after(state, step):
    """Return an expectation, never a verified update or permission to retry."""
    if (not isinstance(state, RosterState) or not isinstance(step, FormationStep)
            or not isinstance(step.kind, FormationKind) or type(step.revision) is not int
            or step.revision != state.revision):
        raise RuleInputError("Formation steps must belong to the current roster revision")
    by_name = {hero.name: hero for hero in state.heroes}
    if not isinstance(step.hero, str) or step.hero not in by_name:
        raise RuleInputError("The formation hero is not known")
    hero = by_name[step.hero]
    if step.source is not None:
        _cell(step.source)
    if step.target is not None:
        _cell(step.target)
    occupied = {item.cell: item for item in state.heroes if item.cell is not None}
    delta = 0
    if step.kind == FormationKind.SWAP:
        old = occupied.get(step.target)
        if (hero.cell is not None or step.source is not None or old is None
                or old.name != step.outgoing_name):
            raise RuleInputError("A swap requires a known bench hero and the exact occupied target")
        by_name[old.name] = replace(old, cell=None)
        by_name[hero.name] = replace(hero, cell=step.target)
    else:
        if step.outgoing_name is not None:
            raise RuleInputError("Only a swap may name an outgoing hero")
        if step.kind == FormationKind.PLACE:
            if (hero.cell is not None or step.source is not None or step.target is None
                    or step.target in occupied or state.panel.deployed >= state.panel.capacity):
                raise RuleInputError("Placement requires an empty cell and available deployment capacity")
            delta = 1
        elif step.kind == FormationKind.MOVE:
            if (hero.cell is None or step.source != hero.cell or step.target is None
                    or step.target in occupied):
                raise RuleInputError("Movement requires the known source and an empty destination")
        elif step.kind == FormationKind.WITHDRAW:
            if (hero.cell is None or step.source != hero.cell or step.target is not None
                    or len(state.heroes) - state.panel.deployed >= BENCH_CAPACITY):
                raise RuleInputError("Withdrawal requires the known source and an available bench slot")
            delta = -1
        by_name[hero.name] = replace(hero, cell=step.target)
    return RosterState(tuple(by_name.values()), replace(state.panel, deployed=state.panel.deployed + delta),
                       state.game_mode, state.revision + 1)


def commit_step(state, step, after_scan, occupied_cells):
    """Commit a step only when all bench identities, cells and counters agree."""
    expected = derive_after(state, step)
    bench = _scan(after_scan)
    if (after_scan.panel != expected.panel or after_scan.game_mode != expected.game_mode
            or bench != {hero.name: hero.imprint for hero in expected.heroes if hero.cell is None}
            or _occupied(occupied_cells) != {hero.cell for hero in expected.heroes if hero.cell is not None}):
        raise RuleInputError("Post-action evidence does not match the complete expected roster")
    return expected


def _results(values):
    if not isinstance(values, (tuple, list)):
        raise RuleInputError("Explicit hero results are required")
    result = []
    seen = set()
    for value in values:
        if not isinstance(value, HeroResult):
            raise RuleInputError("Every hero result must include its explicit imprint")
        current = _identity(value.name, value.imprint)
        previous = (None if value.previous_imprint is None
                    else _identity(value.name, value.previous_imprint))
        if previous is not None:
            first = {3: Imprint.D, 4: Imprint.C, 5: Imprint.B}[get_hero(value.name).rarity.stars]
            expected = first if previous == Imprint.NONE else int(previous) + 1
            if current != expected and not current == previous == Imprint.SSS:
                raise RuleInputError("The result is not a legal imprint transition")
        normalized = HeroResult(value.name, current, previous)
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def reconcile(state, scan, occupied, hero_results=()):
    """Refresh resources/bench/results while retaining every confirmed field cell.

    Every imprint change needs an explicit result chain; the complete bench
    independently confirms the retained results for its heroes. New names need
    both an acquisition result and the complete bench. This cannot infer a sale, a manual
    formation change, a missing hero, or a new game from unchanged counters.
    """
    if not isinstance(state, RosterState):
        raise RuleInputError("A verified starting roster is required")
    bench = _scan(scan)
    if (scan.game_mode != state.game_mode
            or scan.panel.round_number not in (state.panel.round_number, state.panel.round_number + 1)
            or scan.panel.level not in (state.panel.level, state.panel.level + 1)
            or scan.panel.deployed != state.panel.deployed
            or _occupied(occupied) != {hero.cell for hero in state.heroes if hero.cell is not None}):
        raise RuleInputError("Reconciliation cannot change game mode, skip rounds or move field heroes")
    by_name = {hero.name: hero for hero in state.heroes}
    if (any(hero.name in bench for hero in state.heroes if hero.cell is not None)
            or any(hero.name not in bench for hero in state.heroes if hero.cell is None)):
        raise RuleInputError("A known hero disappeared or changed location without a confirmed step")
    results = {}
    for result in _results(hero_results):
        chain = results.setdefault(result.name, [])
        if chain and result.previous_imprint != chain[-1].imprint:
            raise RuleInputError("Multiple results for a hero must form one consistent ordered chain")
        chain.append(result)
    for name, chain in results.items():
        first, last = chain[0], chain[-1]
        known = by_name.get(name)
        if known is None:
            if first.previous_imprint is not None:
                raise RuleInputError("An upgrade result cannot introduce an unknown hero")
            by_name[name] = RosterHero(name, last.imprint)
        elif last.imprint == known.imprint:
            # Repeated result frames and the same result passed after a commit
            # must not upgrade a hero again. No acquisition counter is inferred.
            pass
        elif not any(result.previous_imprint == known.imprint for result in chain):
            raise RuleInputError("The result does not continue the known hero imprint")
        else:
            by_name[name] = replace(known, imprint=last.imprint)
    if set(bench) - set(by_name):
        raise RuleInputError("A new bench identity has no explicit hero result")
    for name, hero in tuple(by_name.items()):
        if hero.cell is not None:
            continue
        if name not in bench or bench[name] != hero.imprint:
            raise RuleInputError("The full bench does not confirm the collected hero results")
    updated = RosterState(tuple(by_name.values()), scan.panel, scan.game_mode, state.revision)
    return state if updated == state else replace(updated, revision=state.revision + 1)


def reconcile_transfer(state, scan, occupied, removed, hero_results=()):
    """Apply explicitly sold bench names before independently checking gifts.

    The action controller proves the selected identities, quote and payment.
    This function never infers a sale from missing cards. A sold hero may be
    granted again by the same transaction; removing it before applying the
    result ensures the old imprint cannot leak into the newly acquired copy.
    """
    if (not isinstance(state, RosterState) or not isinstance(removed, tuple)
            or not 1 <= len(removed) <= 5 or len(set(removed)) != len(removed)):
        raise RuleInputError("A transfer requires one to five explicit unique bench names")
    bench = {hero.name for hero in state.heroes if hero.cell is None}
    if not set(removed) <= bench:
        raise RuleInputError("Only verified bench heroes may be transferred")
    _scan(scan)
    if (scan.panel.round_number, scan.panel.level, scan.panel.deployed, scan.panel.capacity) != (
            state.panel.round_number, state.panel.level, state.panel.deployed, state.panel.capacity):
        raise RuleInputError("A transfer cannot change the round, level or deployed field")
    retained = replace(state, heroes=tuple(hero for hero in state.heroes if hero.name not in removed))
    updated = reconcile(retained, scan, occupied, hero_results)
    # Even a gift that recreates an identical name/imprint is a new transaction.
    # Its quote and selection evidence must not retain the old revision.
    return replace(updated, revision=state.revision + 1)
