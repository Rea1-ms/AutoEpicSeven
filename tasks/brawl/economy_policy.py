"""Conservative normal-mode economy choices from a complete verified roster.

The reserve and transfer loss scores are deterministic planning heuristics, not
combat predictions. Transfer prices are never derived from rarity or imprint:
only quotes observed for this exact roster revision may fund an upgrade.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from itertools import combinations

from tasks.brawl.roster import RosterState
from tasks.brawl.rules import (
    BENCH_CAPACITY,
    HEROES,
    MAX_LEVEL,
    Resources,
    RuleInputError,
    upgrade_cost,
    validate_class_bonuses,
)
from tasks.brawl.strategy import HeroState, recommend_lineup, score_lineup
from tasks.brawl.vision import BrawlMode


class EconomyAction(str, Enum):
    UPGRADE = "upgrade"
    SUMMON_UPPER = "summon_upper"
    SUMMON_NORMAL = "summon_normal"
    QUOTE_TRANSFER = "quote_transfer"
    TRANSFER = "transfer"
    SAVE = "save"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class EconomyPlan:
    action: EconomyAction
    reason: str
    revision: int
    targets: tuple[str, ...] = ()
    protected: tuple[str, ...] = ()
    expected_reward: Resources = field(default_factory=lambda: Resources(0, 0))


@lru_cache(maxsize=128)
def _recommended_reserves(pool, level, bonuses):
    """Keep current, next-slot and later attainable trait investments.

    Future reserves use only already owned heroes. A higher trait threshold in
    the level-nine recommendation protects its participating heroes; hypothetical
    future draws do not protect every member of the catalogue. Keeping these
    exact teams also avoids rerunning the lineup search for every sale subset.
    The small cache key contains the full identities, imprints and talents, so a
    newly received or upgraded hero cannot reuse a stale recommendation.
    """
    current = recommend_lineup(pool, level, dict(bonuses))
    protected = {hero.name for hero in current.team}
    if level == MAX_LEVEL:
        return frozenset(protected)
    following = recommend_lineup(pool, level + 1, dict(bonuses))
    protected.update(hero.name for hero in following.team)
    future = recommend_lineup(pool, MAX_LEVEL, dict(bonuses))
    reached = {}
    for synergy in (*current.synergies, *following.synergies):
        reached[synergy.name] = max(reached.get(synergy.name, 0), synergy.threshold)
    future_traits = {synergy.name for synergy in future.synergies
                     if synergy.threshold > reached.get(synergy.name, 0)}
    for state in future.team:
        hero = HEROES[state.name]
        if hero.faction in future_traits or hero.profession in future_traits:
            protected.add(state.name)
    return frozenset(protected)


def transfer_candidates(state, class_bonuses=None):
    """Return safe bench identities and protected identities, both sorted.

    This helper only ranks eligibility. It never claims an upgrade is affordable
    and does not authorize clicking a transfer confirmation.
    """
    if not isinstance(state, RosterState):
        raise RuleInputError("Economy planning requires a complete verified roster")
    bonuses = tuple(sorted(validate_class_bonuses(class_bonuses).items()))
    if state.game_mode != BrawlMode.NORMAL or not state.heroes:
        return (), tuple(sorted(hero.name for hero in state.heroes))
    pool = tuple(sorted((HeroState(hero.name, hero.imprint) for hero in state.heroes),
                        key=lambda hero: hero.name))
    protected = set(_recommended_reserves(pool, state.panel.level, bonuses))
    protected.update(hero.name for hero in state.heroes if hero.cell is not None)
    candidates = tuple(sorted(hero.name for hero in state.heroes if hero.name not in protected))
    return candidates, tuple(sorted(protected))


def _quotes(state, observed_quotes, quote_revision):
    if observed_quotes is None:
        if quote_revision is not None:
            raise RuleInputError("A quote revision without observed transfer quotes is invalid")
        return None
    if (not isinstance(observed_quotes, Mapping) or type(quote_revision) is not int
            or quote_revision != state.revision):
        raise RuleInputError("Transfer quotes require the exact current roster revision")
    bench = {hero.name for hero in state.heroes if hero.cell is None}
    for name, reward in observed_quotes.items():
        if (name not in bench or not isinstance(reward, Resources)
                or type(reward.coins) is not int or type(reward.tokens) is not int
                or reward.coins < 0 or reward.tokens < 0
                or reward.coins + reward.tokens <= 0):
            raise RuleInputError("Transfer quotes need a current bench identity and explicit resources")
    return dict(observed_quotes)


def plan_economy(state, class_bonuses=None, *, observed_quotes=None, quote_revision=None,
                 transfer_budget=5, max_level_slot_freed=False):
    """Choose one step; re-observe and call again after every confirmed action.

    ``transfer_budget`` is the remaining part of five heroes for one upgrade,
    not a fresh allowance per invocation. The caller resets it only after that
    upgrade succeeds. ``max_level_slot_freed`` persists for the current round so
    a transfer gift filling the last slot cannot trigger another unwanted sale.
    A transfer plan proves the complete subset can fund the upgrade before the
    caller begins selling; gift rewards are intentionally excluded from funding.
    """
    if not isinstance(state, RosterState):
        raise RuleInputError("Economy planning requires a complete verified roster")
    bonuses = validate_class_bonuses(class_bonuses)
    if type(transfer_budget) is not int or not 0 <= transfer_budget <= 5:
        raise RuleInputError("The remaining transfer allowance must be between zero and five")
    if type(max_level_slot_freed) is not bool:
        raise RuleInputError("The current round's full-bench transfer must be explicit")
    quotes = _quotes(state, observed_quotes, quote_revision)
    panel = state.panel

    def plan(action, reason, **kwargs):
        return EconomyPlan(action, reason, state.revision, **kwargs)

    if state.game_mode != BrawlMode.NORMAL:
        return plan(EconomyAction.UNSUPPORTED, "challenge_economy_not_confirmed")
    if panel.level < MAX_LEVEL and panel.coins >= upgrade_cost(panel.level):
        return plan(EconomyAction.UPGRADE, "upgrade_affordable")
    bench_count = sum(hero.cell is None for hero in state.heroes)
    if panel.synergy_tokens >= 40 and bench_count < BENCH_CAPACITY:
        return plan(EconomyAction.SUMMON_UPPER, "upper_summon_affordable")
    if len(state.heroes) < panel.capacity:
        if panel.coins >= 10 and bench_count < BENCH_CAPACITY:
            return plan(EconomyAction.SUMMON_NORMAL, "fill_missing_members")
        return plan(EconomyAction.SAVE, "insufficient_coins_to_fill")
    if panel.deployed < panel.capacity:
        return plan(EconomyAction.SAVE, "formation_required")
    if transfer_budget == 0:
        return plan(EconomyAction.SAVE, "transfer_allowance_exhausted")
    clearing = panel.level == MAX_LEVEL
    if clearing and (bench_count < BENCH_CAPACITY or max_level_slot_freed):
        return plan(EconomyAction.SAVE, "save_resources")
    # Never sell the last available future teammate to buy an empty new slot.
    # This count includes field and bench heroes; duplicate summons have already
    # combined in the verified roster and therefore cannot inflate it.
    maximum = min(transfer_budget, len(state.heroes) - (panel.capacity + int(not clearing)))
    if maximum <= 0:
        return plan(EconomyAction.SAVE, "retain_next_capacity")
    candidates, protected = transfer_candidates(state, bonuses)
    if not candidates:
        return plan(EconomyAction.SAVE, "all_candidates_reserved", protected=protected)
    if quotes is None or any(name not in quotes for name in candidates):
        return plan(EconomyAction.QUOTE_TRANSFER, "read_current_transfer_quotes",
                    targets=candidates, protected=protected)
    required = 0 if clearing else upgrade_cost(panel.level) - panel.coins
    pool = {hero.name: HeroState(hero.name, hero.imprint) for hero in state.heroes}
    choices = []
    for size in range(1, (1 if clearing else maximum) + 1):
        for names in combinations(candidates, size):
            reward = Resources(sum(quotes[name].coins for name in names),
                               sum(quotes[name].tokens for name in names))
            if reward.coins < required:
                continue
            # Existing lineup scoring accounts for imprints and trait groups.
            # Among feasible subsets prefer the smallest heuristic sacrifice,
            # then fewer sales and less unnecessary excess funding. Tokens are
            # observed outcomes, not a reason to sacrifice an important hero.
            sold = tuple(pool[name] for name in names)
            # The sold subset is inventory, not a team that will enter combat.
            # In particular, score_lineup penalizes an all-ranged team; using
            # that score alone can make two ranged heroes cheaper to discard
            # than either individual. Retain genuine trait bonuses but never
            # discount a sale below the sum of its individual hero values.
            loss = max(score_lineup(sold), sum(score_lineup((hero,)) for hero in sold))
            choices.append(((loss, len(names), reward.coins - required, names), names, reward))
    if not choices:
        return plan(EconomyAction.SAVE, "safe_transfers_cannot_fund_upgrade", protected=protected)
    _, names, reward = min(choices)
    return plan(EconomyAction.TRANSFER, "free_one_bench_slot" if clearing else "fund_one_upgrade",
                targets=names, protected=protected, expected_reward=reward)
