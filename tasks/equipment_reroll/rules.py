"""Decide which equipment substats to retain without touching a device."""

import re
from dataclasses import dataclass


STAT_NAMES = {
    "AttackPercent": "攻击力%",
    "DefensePercent": "防御力%",
    "HealthPercent": "生命值%",
    "Effectiveness": "效果命中",
    "Resistance": "效果抗性",
    "Speed": "速度",
    "CriticalChance": "暴击率",
    "CriticalDamage": "暴击伤害",
    "FlatAttack": "小攻击",
    "FlatDefense": "小防御",
    "FlatHealth": "小生命",
}
STAT_LABELS = {
    "AttackPercent": "攻击力", "FlatAttack": "攻击力",
    "DefensePercent": "防御力", "FlatDefense": "防御力",
    "HealthPercent": "生命值", "FlatHealth": "生命值",
    "Effectiveness": "效果命中", "Resistance": "效果抗性", "Speed": "速度",
    "CriticalChance": "暴击率", "CriticalDamage": "暴击伤害",
}
PERCENT_STATS = {
    "AttackPercent", "DefensePercent", "HealthPercent", "Effectiveness",
    "Resistance", "CriticalChance", "CriticalDamage",
}
MAX_VALUES = {
    "AttackPercent": 8, "DefensePercent": 8, "HealthPercent": 8,
    "Effectiveness": 8, "Resistance": 8, "Speed": 5,
    "CriticalChance": 5, "CriticalDamage": 7,
}
# Flat-stat observations are defaults, not proven roll caps.
DEFAULT_TARGETS = {**MAX_VALUES, "FlatAttack": 44, "FlatDefense": 34, "FlatHealth": 201}
REFRESH_COSTS = (20, 60, 150)


@dataclass(frozen=True)
class Substat:
    kind: str
    value: int


@dataclass(frozen=True)
class Target:
    kind: str
    value: int

    def __post_init__(self):
        if self.kind not in STAT_NAMES:
            raise ValueError(f"Unknown substat: {self.kind}")
        if type(self.value) is not int or self.value <= 0:
            raise ValueError("Substat target must be a positive integer")
        if self.kind == "Speed" and self.value != 5:
            raise ValueError("Speed target must be 5 when speed is requested")
        if self.kind in MAX_VALUES and self.value > MAX_VALUES[self.kind]:
            raise ValueError(f"{self.kind} target exceeds the known maximum of {MAX_VALUES[self.kind]}")


def parse_substat(name: str, value: str, *, validate_roll=True) -> Substat:
    """Require an explicit percentage sign; never infer it from a small number."""
    name = re.sub(r"\s+", "", name).rstrip(".．·。，：:")
    value = re.sub(r"\s+", "", value).replace("％", "%")
    match = re.fullmatch(r"([0-9]+)(%?)", value)
    if match is None:
        raise ValueError(f"Unrecognized substat value: {name} {value}")
    number = int(match[1])
    percent = bool(match[2])
    flat_or_percent = {
        "攻击力": ("FlatAttack", "AttackPercent"),
        "防御力": ("FlatDefense", "DefensePercent"),
        "生命值": ("FlatHealth", "HealthPercent"),
    }
    other = {
        "速度": "Speed", "效果命中": "Effectiveness", "效果抗性": "Resistance",
        "暴击率": "CriticalChance", "暴击伤害": "CriticalDamage",
    }
    if name in flat_or_percent:
        kind = flat_or_percent[name][int(percent)]
    elif name in other:
        kind = other[name]
        if percent != (kind in PERCENT_STATS):
            raise ValueError(f"Substat percentage sign does not match its type: {name} {value}")
    else:
        raise ValueError(f"Unrecognized substat name: {name}")
    if number <= 0:
        raise ValueError("Substat value must be greater than zero")
    if validate_roll:
        if kind in MAX_VALUES and number > MAX_VALUES[kind]:
            raise ValueError(f"Substat value exceeds the known range: {name} {value}")
        # Missing '%' on a percent roll must not silently become a flat stat.
        # The generous upper bound accepts flat rolls above today's observations.
        if kind.startswith("Flat") and not 10 <= number <= 9999:
            raise ValueError(f"Suspicious flat value; percentage sign may be missing: {name} {value}")
        if kind == "Speed" and number < 2:
            raise ValueError(f"Suspicious speed value: {value}")
    return Substat(kind, number)


def parse_points(text: str) -> int:
    text = text.strip().replace("，", ",")
    if not re.fullmatch(r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)", text):
        raise ValueError(f"Unrecognized point balance: {text}")
    return int(text.replace(",", ""))


@dataclass(frozen=True)
class UnreadSubstat:
    """A row whose label or number is unnecessary after the gold prefilter."""

    name: str | None = None


@dataclass(frozen=True)
class RejectedCandidate:
    """Partial observation, valid only for the policy that rejected it.

    Its image signature includes all unprotected name AND value glyphs and
    gold state. Startup also includes protected rows until their values are
    confirmed; afterwards the user permits reusing that record. Skipping only
    OCR on an unread unlocked number must never bypass frame stability.
    This partial observation is never a replaceable roll.
    """

    rows: tuple[Substat | UnreadSubstat, ...]
    targets: tuple[Target, ...]
    image_signature: bytes

    def __post_init__(self):
        if len(self.rows) != 4 or not self.targets or not self.image_signature:
            raise ValueError("Partial candidate observation lacks validation data")
        known = [row.kind for row in self.rows if isinstance(row, Substat)]
        if len(set(known)) != len(known):
            raise ValueError("Candidate contains duplicate substat types")
        if any(row.name is not None and row.name not in STAT_LABELS.values()
               for row in self.rows if isinstance(row, UnreadSubstat)):
            raise ValueError("Candidate substat name could not be confirmed")

    def __getitem__(self, row):
        return self.rows[row]


@dataclass(frozen=True)
class Snapshot:
    current: tuple[Substat, ...]
    candidate: tuple[Substat, ...] | RejectedCandidate
    locked: tuple[bool, ...]
    points: int
    cost: int
    points_signature: bytes = b""

    def __post_init__(self):
        columns = (self.current,)
        if self.candidate and not isinstance(self.candidate, RejectedCandidate):
            columns += (self.candidate,)
        for stats in columns:
            if len(stats) != 4 or len({stat.kind for stat in stats}) != 4:
                raise ValueError("Four distinct substats must be fully recognized")
        if len(self.locked) != 4 or sum(self.locked) > 2:
            raise ValueError("Invalid substat lock state")
        if self.cost != REFRESH_COSTS[sum(self.locked)] or self.points < 0:
            raise ValueError("Refresh cost does not match the lock count, or the point balance is negative")


class RerollPolicy:
    def __init__(self, targets: tuple[Target, ...]):
        if len(targets) != 4 or len({target.kind for target in targets}) != 4:
            raise ValueError("Configure four distinct target substat types")
        self.configured_targets = targets
        # Speed is always the first objective, even if placed last in the GUI.
        self.targets = tuple(t for t in targets if t.kind == "Speed") + tuple(
            t for t in targets if t.kind != "Speed"
        )

    def score(self, stats: tuple[Substat, ...]) -> tuple[int, ...]:
        values = {s.kind: s.value for s in stats}
        return tuple(min(values.get(t.kind, 0), t.value) for t in self.targets)

    def should_replace(self, snapshot: Snapshot) -> bool:
        if not snapshot.candidate:
            return False
        if isinstance(snapshot.candidate, RejectedCandidate):
            if snapshot.candidate.targets != self.targets:
                raise ValueError("Candidate has not been validated against the current targets")
            return False
        return self.score(snapshot.candidate) > self.score(snapshot.current)

    def complete(self, stats: tuple[Substat, ...]) -> bool:
        return self.score(stats) == tuple(t.value for t in self.targets)

    def desired_locks(self, stats: tuple[Substat, ...]) -> tuple[bool, ...]:
        values = {s.kind: s.value for s in stats}
        selected = set()
        for target in self.targets:
            # Lock only the achieved prefix. Locking a lower priority first can
            # occupy the second slot and make the more important roll impossible.
            if values.get(target.kind, 0) < target.value:
                break
            selected.add(target.kind)
            if len(selected) == 2:
                break
        return tuple(s.kind in selected for s in stats)

    def validate_initial_locks(self, snapshot: Snapshot):
        # The supplied equipment must respect the order the user entered,
        # even while the refresh strategy retains its special speed-first
        # rule. Never remove a user's incompatible lock and spend afterwards.
        values = {s.kind: s.value for s in snapshot.current}
        allowed = set()
        for target in self.configured_targets:
            if values.get(target.kind, 0) < target.value:
                break
            allowed.add(target.kind)
            if len(allowed) == 2:
                break
        invalid = [f"row {row + 1}: {stat.kind} {stat.value}"
                   for row, (stat, locked) in enumerate(zip(snapshot.current, snapshot.locked))
                   if locked and stat.kind not in allowed]
        if invalid:
            priority = " > ".join(f"{t.kind} {t.value}" for t in self.configured_targets)
            raise ValueError(f"Initial locked substats conflict with configured priority ({priority}): "
                             + ", ".join(invalid))


@dataclass
class RefreshBudget:
    max_refresh: int
    max_points: int
    reserve_points: int
    refreshes: int = 0
    spent: int = 0

    def __post_init__(self):
        if any(type(v) is not int or v < 0 for v in (
            self.max_refresh, self.max_points, self.reserve_points,
        )):
            raise ValueError("Refresh limit, point budget and point reserve must be nonnegative integers; 0 means unlimited")

    def stop_reason(self, snapshot: Snapshot, *, cost=None) -> str | None:
        cost = snapshot.cost if cost is None else cost
        if self.max_refresh and self.refreshes >= self.max_refresh:
            return "Refresh limit reached"
        if self.max_points and self.spent + cost > self.max_points:
            return "Point budget reached"
        if snapshot.points - cost < self.reserve_points:
            return "Insufficient points or point reserve reached"
        return None

    def confirm_refresh(self, before: Snapshot, after: Snapshot) -> bool:
        if after.points == before.points:
            return False
        if after.points != before.points - before.cost:
            raise ValueError("Refresh payment does not match the expected cost")
        if after.current != before.current or after.locked != before.locked:
            raise ValueError("Current equipment substats or locks changed during refresh")
        if not after.candidate:
            return False
        for row, locked in enumerate(before.locked):
            if locked and after.candidate[row] != before.current[row]:
                raise ValueError(f"Refresh result did not preserve locked substat at row {row + 1}")
        self.refreshes += 1
        self.spent += before.cost
        return True
