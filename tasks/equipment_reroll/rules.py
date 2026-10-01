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
            raise ValueError(f"未知副属性：{self.kind}")
        if type(self.value) is not int or self.value <= 0:
            raise ValueError("副属性目标必须是正整数")
        if self.kind == "Speed" and self.value != 5:
            raise ValueError("有速度要求时，速度目标固定为 5")
        if self.kind in MAX_VALUES and self.value > MAX_VALUES[self.kind]:
            raise ValueError(f"{STAT_NAMES[self.kind]}的目标超过已知上限 {MAX_VALUES[self.kind]}")


def parse_substat(name: str, value: str, *, validate_roll=True) -> Substat:
    """Require an explicit percentage sign; never infer it from a small number."""
    name = re.sub(r"\s+", "", name).rstrip(".．·。，：:")
    value = re.sub(r"\s+", "", value).replace("％", "%")
    match = re.fullmatch(r"([0-9]+)(%?)", value)
    if match is None:
        raise ValueError(f"副属性数值无法识别：{name} {value}")
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
            raise ValueError(f"副属性百分号不匹配：{name} {value}")
    else:
        raise ValueError(f"副属性名称无法识别：{name}")
    if number <= 0:
        raise ValueError("副属性数值必须大于零")
    if validate_roll:
        if kind in MAX_VALUES and number > MAX_VALUES[kind]:
            raise ValueError(f"副属性超出已知范围：{name} {value}")
        # Missing '%' on a percent roll must not silently become a flat stat.
        # The generous upper bound accepts flat rolls above today's observations.
        if kind.startswith("Flat") and not 10 <= number <= 9999:
            raise ValueError(f"固定数值可疑，可能丢失百分号：{name} {value}")
        if kind == "Speed" and number < 2:
            raise ValueError(f"速度数值可疑：{value}")
    return Substat(kind, number)


def parse_points(text: str) -> int:
    text = text.strip().replace("，", ",")
    if not re.fullmatch(r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)", text):
        raise ValueError(f"点数无法识别：{text}")
    return int(text.replace(",", ""))


@dataclass(frozen=True)
class Snapshot:
    current: tuple[Substat, ...]
    candidate: tuple[Substat, ...]
    locked: tuple[bool, ...]
    points: int
    cost: int
    main: Substat

    def __post_init__(self):
        columns = (self.current, self.candidate) if self.candidate else (self.current,)
        for stats in columns:
            if len(stats) != 4 or len({stat.kind for stat in stats}) != 4:
                raise ValueError("必须完整识别四条互不重复的副属性")
        if len(self.locked) != 4 or sum(self.locked) > 2:
            raise ValueError("锁定状态无效")
        if self.cost != REFRESH_COSTS[sum(self.locked)] or self.points < 0:
            raise ValueError("刷新价格与当前锁定数量不一致")


class RerollPolicy:
    def __init__(self, targets: tuple[Target, ...]):
        if len(targets) != 4 or len({target.kind for target in targets}) != 4:
            raise ValueError("请配置四种互不重复的目标副属性")
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
            raise ValueError("刷新次数、点数预算和保留点数必须是非负整数；0 表示不设该上限")

    def stop_reason(self, snapshot: Snapshot, *, cost=None) -> str | None:
        cost = snapshot.cost if cost is None else cost
        if self.max_refresh and self.refreshes >= self.max_refresh:
            return "达到最大刷新次数"
        if self.max_points and self.spent + cost > self.max_points:
            return "达到点数预算"
        if snapshot.points - cost < self.reserve_points:
            return "剩余点数不足或已达到保留点数"
        return None

    def confirm_refresh(self, before: Snapshot, after: Snapshot) -> bool:
        if after.points == before.points:
            return False
        if after.points != before.points - before.cost:
            raise ValueError("刷新扣费与预期不一致，停止等待人工检查")
        if after.current != before.current or after.locked != before.locked:
            raise ValueError("刷新期间当前装备属性或锁定状态发生变化")
        if not after.candidate:
            return False
        for row, locked in enumerate(before.locked):
            if locked and after.candidate[row] != before.current[row]:
                raise ValueError("刷新结果未保留已锁定的属性")
        self.refreshes += 1
        self.spent += before.cost
        return True
