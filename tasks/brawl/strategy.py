"""Independent, deterministic lineup heuristics; not a combat simulator.

Weights are planning preferences, not measured game statistics. Recommendations
compare complete lineup thresholds and cannot claim optimal combat strength.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from itertools import combinations
from math import comb

from tasks.brawl.rules import (
    BENCH_CAPACITY, BOARD_COLUMNS, BOARD_ROWS, HEROES, MAX_LEVEL, ActiveSynergy, Imprint, Rarity, RuleInputError,
    active_synergies, get_hero, team_capacity, validate_class_bonuses, validate_imprint,
)


@dataclass(frozen=True)
class HeroState:
    name: str
    imprint: Imprint | str

    def __post_init__(self):
        hero = get_hero(self.name)
        object.__setattr__(self, "name", hero.name)
        object.__setattr__(self, "imprint", validate_imprint(hero.name, self.imprint))


@dataclass(frozen=True)
class Placement:
    name: str
    row: int
    column: int
    attack_range: int


@dataclass(frozen=True)
class LineupRecommendation:
    team: tuple[HeroState, ...]
    bench: tuple[HeroState, ...]
    synergies: tuple[ActiveSynergy, ...]
    placements: tuple[Placement, ...]
    score: float
    search_method: str
    heuristic: bool = True


_RARITY_WEIGHTS = {
    Rarity.NORMAL_3: 3.0, Rarity.NORMAL_4: 4.0, Rarity.MOONLIGHT_4: 5.0,
    Rarity.NORMAL_5: 6.0, Rarity.MOONLIGHT_5: 8.0,
}
_TIER_WEIGHTS = (0.0, 6.0, 15.0, 25.0)
_EXACT_LIMIT = 50_000
_BEAM_WIDTH = 128


def _states(states, maximum=None):
    if isinstance(states, (str, bytes)) or not isinstance(states, Iterable):
        raise RuleInputError("Hero state collection is required")
    result = tuple(states)
    if any(not isinstance(state, HeroState) for state in result):
        raise RuleInputError("Every hero needs an explicit HeroState, including its observed imprint")
    if len({state.name for state in result}) != len(result):
        raise RuleInputError("Duplicate hero states must be resolved before planning")
    if maximum is not None and len(result) > maximum:
        raise RuleInputError(f"Too many heroes for this team: {len(result)}")
    return tuple(sorted(result, key=lambda state: state.name))


def _score(team, bonuses):
    score = 0.0
    for state in team:
        hero = HEROES[state.name]
        score += _RARITY_WEIGHTS[hero.rarity] + 0.7 * state.imprint
        if hero.ultimate_imprint is not None and state.imprint >= hero.ultimate_imprint:
            score += 2.0
    # Thresholds belong to the whole lineup. Summing independently ranked heroes
    # loses the discontinuity when the second/fourth/sixth member activates a
    # trait. Score only the highest active tier, never all lower tiers again.
    score += sum(_TIER_WEIGHTS[synergy.tier] for synergy in active_synergies(
        (state.name for state in team), bonuses,
    ))
    if len(team) >= 2:
        melee = sum(HEROES[state.name].attack_range == 1 for state in team)
        if melee == 0:
            score -= 6.0
        elif melee == len(team):
            score -= 2.0
        else:
            score += 2.0
    return round(score, 4)


def score_lineup(states, class_bonuses=None):
    return _score(_states(states, MAX_LEVEL + 1), validate_class_bonuses(class_bonuses))


def place_lineup(states):
    """Return logical cells, with row zero closest to the enemy.

    The board is four staggered rows of seven cells. This function deliberately
    does not convert cells into screen coordinates: orientation, row offsets,
    and actual drag locations require verified screenshots from the UI layer.
    """
    team = _states(states, MAX_LEVEL + 1)
    class_order = {"骑士": 0, "战士": 1, "盗贼": 2, "精灵师": 3, "魔导士": 4, "射手": 5}
    ordered = sorted(team, key=lambda state: (
        HEROES[state.name].attack_range, class_order[HEROES[state.name].profession],
        -state.imprint, state.name,
    ))
    columns = (3, 2, 4, 1, 5, 0, 6)
    # Row preferences are a positioning heuristic, not an observed game rule.
    # Range two starts behind the melee row without being sent to the mage row.
    rows = {1: (0, 1, 2, 3), 2: (1, 0, 2, 3), 3: (2, 3, 1, 0), 4: (3, 2, 1, 0)}
    occupied = set()
    result = []
    for state in ordered:
        attack_range = HEROES[state.name].attack_range
        row, column = next((row, column) for row in rows[attack_range] for column in columns
                           if (row, column) not in occupied)
        assert 0 <= row < BOARD_ROWS and 0 <= column < BOARD_COLUMNS
        occupied.add((row, column))
        result.append(Placement(state.name, row, column, attack_range))
    return tuple(result)


def recommend_lineup(states, level, class_bonuses=None):
    """Plan using only explicitly supplied heroes and observed imprints.

    Small pools enumerate all full lineups under a heuristic score. Large pools
    use bounded beam search, with the same complete-team scoring at each step.
    Neither method predicts enemy targeting, exact damage, or spotlight cells.
    """
    capacity = team_capacity(level)
    # The full pool includes deployed heroes as well as the ten bench slots.
    # This is a planning bound, not evidence of the current deployed count;
    # screenshot-backed roster input must check that stricter current count too.
    pool = _states(states, capacity + BENCH_CAPACITY)
    bonuses = validate_class_bonuses(class_bonuses)
    if not pool:
        raise RuleInputError("Cannot recommend a lineup from an empty roster")
    size = min(capacity, len(pool))
    if comb(len(pool), size) <= _EXACT_LIMIT:
        candidates = combinations(pool, size)
        team = max(candidates, key=lambda candidate: (_score(candidate, bonuses), tuple(s.name for s in candidate)))
        method = "exhaustive_heuristic"
    else:
        # Increasing indices keep each subset unique. Only extend partial teams
        # that can still reach the requested size, so early pruning cannot leave
        # a short team when enough explicit heroes were supplied.
        beam = [()]
        for depth in range(size):
            candidates = (
                chosen + (index,)
                for chosen in beam
                for index in range(chosen[-1] + 1 if chosen else 0, len(pool) - size + depth + 1)
            )
            beam = sorted(candidates, key=lambda indices: (
                _score(tuple(pool[index] for index in indices), bonuses), indices,
            ), reverse=True)[:_BEAM_WIDTH]
        team = tuple(pool[index] for index in beam[0])
        method = "beam_heuristic"
    selected = {state.name for state in team}
    return LineupRecommendation(
        team=team, bench=tuple(state for state in pool if state.name not in selected),
        synergies=active_synergies((state.name for state in team), bonuses), placements=place_lineup(team),
        score=_score(team, bonuses), search_method=method,
    )
