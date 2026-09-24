"""Choose rune farming targets from five-star awakening requirements."""

from dataclasses import dataclass
from fractions import Fraction
from math import isfinite
from typing import Mapping


RUNE_ELEMENTS = ("Dark", "Light", "Water", "Fire", "Nature")
RUNE_REQUIREMENTS = (45, 22, 16)
RUNE_TIERS = ("normal", "greater", "epic")
ALTAR_DROPS = {
    "Pri": (True, False, False),
    "Mid": (True, True, False),
    "High": (True, True, True),
    "Hell": (False, True, True),
}


@dataclass(frozen=True)
class RuneStock:
    normal: int
    greater: int
    epic: int

    def __post_init__(self):
        if any(type(value) is not int or value < 0 for value in self.counts):
            raise ValueError("Rune counts must be non-negative integers")

    @property
    def counts(self) -> tuple[int, int, int]:
        return self.normal, self.greater, self.epic

    @property
    def coverage(self) -> Fraction:
        # Keep fractional coverage: flooring would tie all five colors at zero
        # whenever each lacks just one tier, causing fixed-order starvation.
        return min(
            Fraction(count, need)
            for count, need in zip(self.counts, RUNE_REQUIREMENTS)
        )

    @property
    def next_character_deficits(self) -> tuple[int, int, int]:
        target = int(self.coverage) + 1
        return tuple(
            max(target * need - count, 0)
            for count, need in zip(self.counts, RUNE_REQUIREMENTS)
        )


# User-reported total drops, received 2026-09-24. Preserve the original sample
# sizes instead of rounding yields or confusing item quantities with drop-event
# probabilities. The user believes all samples had no bonuses; treat that as a
# provisional assumption, not independently verified universal drop rates.
RUNE_DROP_SAMPLES = {
    "Pri": (302, RuneStock(1209, 0, 0)),
    "Mid": (360, RuneStock(568, 330, 0)),
    "High": (363, RuneStock(186, 449, 32)),
    "Hell": (166, RuneStock(0, 289, 36)),
}


@dataclass(frozen=True)
class RuneTarget:
    element: str
    grade: str
    stock: RuneStock
    expected_runs: dict[str, float]
    estimated_stamina: float


def choose_rune_target(
    stocks: Mapping[str, RuneStock],
    *,
    samples: Mapping[str, tuple[int, RuneStock]] = RUNE_DROP_SAMPLES,
    stamina_costs: Mapping[str, int] | None = None,
) -> RuneTarget:
    """Balance colors, then estimate the cheapest mix to supply the next hero.

    The linear model uses observed mean item yields and allows fractional runs.
    Its plan compares difficulties; it neither promises a stopping time nor
    changes the configured battle budget. Actual inventory remains authoritative.
    """
    from scipy.optimize import linprog

    from tasks.dungeon.burnout import ALTAR_STAMINA_COST

    if set(stocks) != set(RUNE_ELEMENTS):
        raise ValueError("A complete, verified five-color inventory is required")
    if stamina_costs is None:
        stamina_costs = ALTAR_STAMINA_COST
    grades = tuple(ALTAR_DROPS)
    if set(samples) != set(grades) or set(stamina_costs) != set(grades):
        raise ValueError("Samples and stamina costs must cover all four altar grades")
    yields = {}
    for grade in grades:
        runs, drops = samples[grade]
        cost = stamina_costs[grade]
        if type(runs) is not int or runs <= 0:
            raise ValueError("Sample completed run counts must be positive integers")
        if isinstance(cost, bool) or not isfinite(cost) or cost <= 0:
            raise ValueError("Altar stamina costs must be finite and positive")
        if any(count and not allowed for count, allowed in zip(drops.counts, ALTAR_DROPS[grade])):
            raise ValueError(f"Sample contains an unsupported rune tier: {grade}")
        yields[grade] = tuple(float(Fraction(count, runs)) for count in drops.counts)

    def priority(element):
        stock = stocks[element]
        # Equal bottlenecks prefer the color with the larger normalized deficit
        # to its next complete character. Surplus in another tier cannot hide
        # this deficit: synthesis costs gold and is not performed by this task.
        deficit = sum(
            Fraction(count, need)
            for count, need in zip(stock.next_character_deficits, RUNE_REQUIREMENTS)
        )
        return stock.coverage, -deficit, RUNE_ELEMENTS.index(element)

    element = min(RUNE_ELEMENTS, key=priority)
    stock = stocks[element]
    result = linprog(
        [stamina_costs[grade] for grade in grades],
        A_ub=[[-yields[grade][tier] for grade in grades] for tier in range(3)],
        b_ub=[-count for count in stock.next_character_deficits],
        bounds=(0, None),
        method="highs",
    )
    if not result.success:
        raise ValueError(f"Rune samples cannot supply the requested deficits: {result.message}")
    expected_runs = {
        grade: float(runs) for grade, runs in zip(grades, result.x) if runs > 1e-8
    }
    limiting_tiers = [
        tier for tier, (count, need) in enumerate(zip(stock.counts, RUNE_REQUIREMENTS))
        if Fraction(count, need) == stock.coverage
    ]
    # A cost-efficient combination can include several difficulties. Farm the
    # one that improves the current bottleneck fastest per stamina, rather than
    # selecting the longest sub-plan and oversupplying a non-limiting tier.
    # All four grades remain candidates in the model: a later sample or stamina
    # change can make Mid/High useful without changing this selection policy.
    grade = max(
        expected_runs,
        key=lambda candidate: (
            sum(yields[candidate][tier] / RUNE_REQUIREMENTS[tier] for tier in limiting_tiers)
            / stamina_costs[candidate],
            -stamina_costs[candidate],
            -grades.index(candidate),
        ),
    )
    return RuneTarget(
        element=element, grade=grade, stock=stock,
        expected_runs=expected_runs, estimated_stamina=float(result.fun),
    )


def rune_sample_yields(
    before: RuneStock, after: RuneStock, *, grade: str, completed_runs: int
) -> tuple[Fraction, Fraction, Fraction]:
    """Return observed items per run, including pets and event bonuses.

    Aggregate inventory differences cannot recover drop-event probabilities.
    Callers must keep pet/event conditions fixed and avoid consuming, claiming,
    or synthesizing runes between these two snapshots.
    """
    if grade not in ALTAR_DROPS:
        raise ValueError(f"Unknown altar grade: {grade}")
    if type(completed_runs) is not int or completed_runs <= 0:
        raise ValueError("The actual completed run count must be positive")
    delta = tuple(end - start for start, end in zip(before.counts, after.counts))
    if any(count < 0 for count in delta):
        raise ValueError("Rune consumption or inconsistent readings invalidate this sample")
    if any(count and not drops for count, drops in zip(delta, ALTAR_DROPS[grade])):
        raise ValueError("The sample contains a tier this difficulty cannot drop")
    return tuple(Fraction(count, completed_runs) for count in delta)


class RuneBalanceMixin:
    def _rune_balance_enabled(self) -> bool:
        return self._dungeon_domain() == "SpiritAltar" and bool(
            getattr(self.config, "Combat_AltarBalance", False)
        )

    def _prepare_rune_balance_target(self) -> None:
        self._rune_balance_target = None
        self._rune_balance_before = None
        if not self._rune_balance_enabled():
            return
        stocks = self._read_rune_inventory()
        self._rune_balance_before = stocks
        self._set_rune_balance_target(stocks)

    def _set_rune_balance_target(self, stocks) -> None:
        from module.logger import logger

        target = choose_rune_target(stocks)
        self._rune_balance_target = target
        for element, stock in stocks.items():
            logger.info(
                f"Rune balance {element}: counts={stock.counts}, "
                f"character coverage={float(stock.coverage):.3f}, "
                f"next character deficits={stock.next_character_deficits}"
            )
        logger.info(
            f"Rune balance: choose {target.element}/{target.grade}; "
            f"estimated next-hero plan={target.expected_runs}, "
            f"stamina={target.estimated_stamina:.2f}; "
            "user sample yields (assumed unbuffed), synthesis is disabled"
        )

    def _combat_runtime_build(self) -> dict:
        session = super()._combat_runtime_build()
        before = getattr(self, "_rune_balance_before", None)
        if self._rune_balance_enabled() and before is not None:
            # Carry the pre-battle snapshot through scheduler restarts. Never
            # reread or change targets while an existing background run is live.
            # This data is for reporting net inventory changes, not proving a
            # completed battle count or deriving drop probabilities.
            session["rune_balance_before"] = {
                element: list(stock.counts) for element, stock in before.items()
            }
        return session

    def _rune_balance_after_settled(self, session=None) -> None:
        from module.logger import logger

        if not self._rune_balance_enabled():
            return
        stocks = self._read_rune_inventory()
        before = (session or {}).get("rune_balance_before", {})
        if not before:
            before = {
                element: stock.counts
                for element, stock in (getattr(self, "_rune_balance_before", None) or {}).items()
            }
        for element, stock in stocks.items():
            previous = before.get(element) if isinstance(before, dict) else None
            if (
                isinstance(previous, (tuple, list))
                and len(previous) == 3
                and all(type(value) is int and value >= 0 for value in previous)
            ):
                change = tuple(end - start for start, end in zip(previous, stock.counts))
                logger.info(f"Rune balance {element}: net inventory change={change}")
        self._set_rune_balance_target(stocks)
