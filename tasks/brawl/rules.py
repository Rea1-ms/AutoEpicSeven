"""Verified brawl rules from 大乱斗攻略.md and user-confirmed game limits.

Unknown probabilities and unrecorded skill information are deliberately absent.
This module never reads account configuration or interacts with a device.
"""

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum, IntEnum
from types import MappingProxyType


class RuleInputError(ValueError):
    """The supplied state is unknown, incomplete, or contradictory."""


class UnknownRuleError(RuleInputError):
    """The guide does not provide the requested rule."""


class Rarity(str, Enum):
    NORMAL_3 = "normal_3"
    NORMAL_4 = "normal_4"
    MOONLIGHT_4 = "moonlight_4"
    NORMAL_5 = "normal_5"
    MOONLIGHT_5 = "moonlight_5"

    @property
    def stars(self):
        return int(self.value[-1])


class Imprint(IntEnum):
    NONE = 0
    D = 1
    C = 2
    B = 3
    A = 4
    S = 5
    SS = 6
    SSS = 7


class SummonKind(str, Enum):
    NORMAL = "normal"
    LOWER = "lower"
    UPPER = "upper"


@dataclass(frozen=True)
class Hero:
    name: str
    rarity: Rarity
    faction: str
    profession: str
    attack_range: int
    unlock_rank: int = 0
    skill_count: int | None = None
    ultimate_imprint: Imprint | None = None


@dataclass(frozen=True)
class Resources:
    coins: int
    tokens: int


@dataclass(frozen=True)
class Synergy:
    name: str
    thresholds: tuple[int, ...]
    effects: tuple[str, ...]
    profession: bool = False


@dataclass(frozen=True)
class ActiveSynergy:
    name: str
    count: int
    threshold: int
    tier: int
    effect: str


PROFESSION_RANGES = MappingProxyType({
    "战士": 1, "骑士": 1, "盗贼": 1, "精灵师": 1, "魔导士": 3, "射手": 4,
})
HERO_ALIASES = MappingProxyType({"露斌": "露彬"})
MAX_LEVEL = 9
BENCH_CAPACITY = 10
BOARD_ROWS = 4
BOARD_COLUMNS = 7
TOTAL_ROUNDS = 25
STARTING_LIVES = 3
LEVEL_UP_COSTS = MappingProxyType(dict(enumerate((20, 50, 70, 100, 120, 150, 200, 250), start=1)))


def _hero(name, rarity, faction, profession, unlock_rank=0, skill_count=None, ultimate_imprint=None):
    attack_range = {"黎洁特": 3, "露彬": 1, "莱娅": 2}.get(name, PROFESSION_RANGES[profession])
    return Hero(name, rarity, faction, profession, attack_range, unlock_rank, skill_count, ultimate_imprint)


_HERO_LIST = (
    _hero("最后的骑士克劳乌", Rarity.MOONLIGHT_5, "失落科技", "骑士", skill_count=3, ultimate_imprint=Imprint.B),
    _hero("蕾诺娅", Rarity.MOONLIGHT_5, "卡厄思", "射手", 2, 3, Imprint.B),
    _hero("仆人克萝爱", Rarity.MOONLIGHT_5, "奇迹女仆", "精灵师", 5, 3, Imprint.B),
    _hero("黎洁特", Rarity.MOONLIGHT_5, "神圣", "精灵师", 10, 3, Imprint.B),
    _hero("实验体赛兹", Rarity.MOONLIGHT_5, "人造生命体", "盗贼", 15, 3, Imprint.B),
    _hero("操作员赛珂兰特", Rarity.MOONLIGHT_5, "失落科技", "射手", 20, 3, Imprint.B),
    _hero("护卫队长克劳乌", Rarity.NORMAL_5, "继承的意志", "战士", skill_count=2),
    _hero("赛兹", Rarity.NORMAL_5, "夏日假期", "盗贼", skill_count=2),
    _hero("利纳柯", Rarity.NORMAL_5, "继承的意志", "盗贼", skill_count=2),
    _hero("指挥型莱伊卡", Rarity.NORMAL_5, "人造生命体", "射手", skill_count=2),
    _hero("露彬", Rarity.NORMAL_5, "继承的意志", "射手", skill_count=2),
    _hero("赛娜", Rarity.NORMAL_5, "猎龙人", "骑士", skill_count=3, ultimate_imprint=Imprint.A),
    _hero("度假风优芬妮", Rarity.NORMAL_5, "夏日假期", "战士", skill_count=3, ultimate_imprint=Imprint.A),
    _hero("莱娅", Rarity.NORMAL_5, "奇迹女仆", "战士", skill_count=2),
    _hero("塔玛林尔", Rarity.NORMAL_5, "奇迹女仆", "精灵师", skill_count=2, ultimate_imprint=Imprint.A),
    _hero("梅铃", Rarity.NORMAL_5, "卡厄思", "盗贼", skill_count=3, ultimate_imprint=Imprint.B),
    _hero("小春", Rarity.NORMAL_5, "卡厄思", "战士", skill_count=3, ultimate_imprint=Imprint.B),
    _hero("战斗型玛雅", Rarity.MOONLIGHT_4, "人造生命体", "骑士"),
    _hero("辅助型拉兹", Rarity.MOONLIGHT_4, "人造生命体", "魔导士"),
    _hero("西风处刑者修里", Rarity.MOONLIGHT_4, "失落科技", "射手"),
    _hero("最后的碎片卡琳", Rarity.MOONLIGHT_4, "失落科技", "盗贼"),
    _hero("未知可能性雅卡泰丝", Rarity.MOONLIGHT_4, "失落科技", "精灵师"),
    _hero("萝季", Rarity.NORMAL_4, "猎龙人", "骑士"),
    _hero("维多丽卡", Rarity.NORMAL_4, "继承的意志", "骑士"),
    _hero("卡琳", Rarity.NORMAL_4, "夏日假期", "盗贼"),
    _hero("玫勒赛德丝", Rarity.NORMAL_4, "人造生命体", "魔导士"),
    _hero("雅卡泰丝", Rarity.NORMAL_4, "夏日假期", "精灵师"),
    _hero("萝里娜", Rarity.NORMAL_3, "转职", "战士"),
    _hero("卡麦萝兹", Rarity.NORMAL_3, "转职", "魔导士"),
    _hero("格伦", Rarity.NORMAL_3, "转职", "射手"),
    _hero("卡萝", Rarity.NORMAL_3, "转职", "魔导士"),
    _hero("玫拉妮", Rarity.NORMAL_3, "猎龙人", "战士"),
    _hero("克莉丝缇", Rarity.NORMAL_3, "猎龙人", "骑士"),
    _hero("缪斯黎玛", Rarity.NORMAL_3, "奇迹女仆", "射手"),
)
HEROES = MappingProxyType({hero.name: hero for hero in _HERO_LIST})

_SYNERGY_LIST = (
    Synergy("转职", (2, 4), ("刻印记忆最高的转职英雄变更形态", "所有转职英雄变更形态")),
    Synergy("夏日假期", (2, 4), ("开战时海啸攻击全体敌人", "海啸额外降低敌人攻击速度10秒")),
    Synergy("猎龙人", (2, 4), ("攻击力最高的猎龙人暴击率提升100%", "所有猎龙人暴击率提升100%")),
    Synergy("奇迹女仆", (2, 4), ("聚光灯目标攻击力与攻击速度提升", "聚光灯大幅强化，攻击恢复自身生命")),
    Synergy("人造生命体", (2, 4), ("实验体受击后生命低于50%召唤萝菲，全体伤害与5秒晕眩",
                                  "实验体受击后生命低于50%召唤末日萝菲，全体伤害与5秒晕眩")),
    Synergy("继承的意志", (2, 4), ("同阵营英雄死亡时，剩余英雄继承攻击与防御提升", "继承效果提升")),
    Synergy("失落科技", (2, 4), ("攻击力最高的1名同阵营英雄获得尘封装备", "攻击力最高的2名同阵营英雄获得特殊尘封装备")),
    Synergy("卡厄思", (2, 3), ("同阵营英雄获得随机闪光", "同阵营英雄获得随机神之闪光")),
    Synergy("神圣", (1,), ("每7秒全队获得神圣，攻击力与防御力每阶段提升100%，最多7阶段",)),
    Synergy("战士", (2, 4, 6), ("生命+100，攻击+20", "生命+200，攻击+40，攻击3次恢复生命",
                               "生命+400，攻击+80，攻击3次恢复生命"), True),
    Synergy("骑士", (2, 4, 6), ("生命+200，防御+20", "生命+400，防御+40，攻击3次晕眩5秒",
                               "生命+800，防御+60，攻击3次晕眩5秒"), True),
    Synergy("射手", (2, 4, 6), ("攻速+15%，暴击+15%", "攻速+30%，暴击+25%，攻击额外伤害200",
                               "攻速+60%，暴击+50%，攻击额外伤害400"), True),
    Synergy("魔导士", (2, 4), ("生命+100，攻击+20", "生命+200，攻击+40，开战获得10秒防护罩"), True),
    Synergy("盗贼", (2, 4, 6), ("暴击+15%，暴伤+25%", "暴击+30%，暴伤+50%，开战隐身10秒",
                               "暴击+60%，暴伤+100%，开战隐身20秒，3次技能伤害无效"), True),
    Synergy("精灵师", (2, 4), ("生命+100，攻击恢复附近其他队友中生命比例最低者",
                              "生命+200，攻击恢复附近其他队友中生命比例最低者"), True),
)
SYNERGIES = MappingProxyType({synergy.name: synergy for synergy in _SYNERGY_LIST})

_BASE_REWARDS = MappingProxyType({
    Rarity.NORMAL_3: Resources(2, 0), Rarity.NORMAL_4: Resources(3, 2),
    Rarity.MOONLIGHT_4: Resources(5, 5), Rarity.NORMAL_5: Resources(7, 10),
    Rarity.MOONLIGHT_5: Resources(10, 20),
})
_NORMAL_ODDS = {8: (10, 20, 33, 25, 12), 9: (5, 10, 25, 40, 20)}
_FATE_ODDS = {SummonKind.LOWER: (0, 60, 40, 0, 0), SummonKind.UPPER: (0, 0, 0, 70, 30)}
_SUMMON_COSTS = {
    SummonKind.NORMAL: Resources(10, 0), SummonKind.LOWER: Resources(0, 10),
    SummonKind.UPPER: Resources(0, 40),
}


def _integer(value, label, minimum, maximum=None):
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise RuleInputError(f"Invalid {label}: {value!r}")
    return value


def get_hero(name):
    if not isinstance(name, str):
        raise RuleInputError(f"Hero name must be explicit text: {name!r}")
    canonical = HERO_ALIASES.get(name, name)
    if canonical not in HEROES:
        raise RuleInputError(f"Unknown hero: {name!r}")
    return HEROES[canonical]


def hero_candidates(faction, profession):
    """Resolve two independently read traits without choosing an ambiguous hero.

    A valid but unpopulated trait pair returns an empty tuple. Missing or
    misclassified fields are invalid; a lone faction must never expand into a
    guessed identity. Candidate order follows the verified catalogue and is
    not a preference ranking. Imprint, ownership and unlock state are separate
    evidence and are not inferred here.
    """
    if (not isinstance(faction, str) or faction not in SYNERGIES
            or SYNERGIES[faction].profession):
        raise RuleInputError(f"Unknown or unread faction: {faction!r}")
    if (not isinstance(profession, str) or profession not in SYNERGIES
            or not SYNERGIES[profession].profession):
        raise RuleInputError(f"Unknown or unread profession: {profession!r}")
    return tuple(hero.name for hero in HEROES.values()
                 if (hero.faction, hero.profession) == (faction, profession))


def parse_imprint(value):
    """NONE explicitly means no imprint; None means unread and is invalid."""
    if isinstance(value, Imprint):
        return value
    if isinstance(value, str) and value in Imprint.__members__:
        return Imprint[value]
    raise RuleInputError(f"Unknown imprint: {value!r}")


def validate_imprint(name, imprint):
    hero = get_hero(name)
    imprint = parse_imprint(imprint)
    first = {3: Imprint.D, 4: Imprint.C, 5: Imprint.B}[hero.rarity.stars]
    if imprint != Imprint.NONE and imprint < first:
        raise RuleInputError(f"Impossible imprint for {hero.name}: {imprint.name}")
    return imprint


def team_capacity(level):
    return _integer(level, "level", 1, MAX_LEVEL) + 1


def upgrade_cost(level):
    _integer(level, "level to upgrade", 1, MAX_LEVEL - 1)
    return LEVEL_UP_COSTS[level]


def available_heroes(total_rank):
    _integer(total_rank, "total rank", 0)
    return tuple(hero for hero in HEROES.values() if hero.unlock_rank <= total_rank)


def _summon_kind(kind):
    try:
        return SummonKind(kind)
    except (TypeError, ValueError) as exc:
        raise RuleInputError(f"Unknown summon kind: {kind!r}") from exc


def summon_cost(kind):
    return _SUMMON_COSTS[_summon_kind(kind)]


def summon_odds(kind, level=None):
    """Return category probabilities, never an invented per-hero distribution."""
    kind = _summon_kind(kind)
    if level is not None:
        _integer(level, "level", 1, MAX_LEVEL)
    if kind == SummonKind.NORMAL:
        if level is None:
            raise RuleInputError("Normal summon probabilities require an observed level")
        if level not in _NORMAL_ODDS:
            raise UnknownRuleError(f"Normal summon probabilities are not recorded for level {level}")
        odds = _NORMAL_ODDS[level]
    else:
        odds = _FATE_ODDS[kind]
    return {rarity: percent / 100 for rarity, percent in zip(Rarity, odds)}


def imprint_after_acquisitions(name, acquired_copies, owned_before_run):
    hero = get_hero(name)
    _integer(acquired_copies, "acquired copies", 1)
    if type(owned_before_run) is not bool:
        raise RuleInputError("Initial ownership must be explicitly True or False")
    first = {3: Imprint.D, 4: Imprint.C, 5: Imprint.B}[hero.rarity.stars]
    if not owned_before_run and acquired_copies == 1:
        return Imprint.NONE
    steps = acquired_copies - (1 if owned_before_run else 2)
    return Imprint(min(Imprint.SSS, first + steps))


def maximum_retained_copies(name, owned_before_run):
    hero = get_hero(name)
    if type(owned_before_run) is not bool:
        raise RuleInputError("Initial ownership must be explicitly True or False")
    first = {3: Imprint.D, 4: Imprint.C, 5: Imprint.B}[hero.rarity.stars]
    return int(Imprint.SSS - first + (1 if owned_before_run else 2))


def overflow_reward(name):
    """Reward for one new copy automatically transferred after SSS."""
    return _BASE_REWARDS[get_hero(name).rarity]


def transfer_reward(name, acquired_copies, owned_before_run):
    """Reward for the current retained hero, based on its acquisition history.

    Acquisition history resets when the retained hero is transferred. It cannot
    be reconstructed from imprint alone: an initially owned B hero and an
    initially unowned B hero represent different numbers of acquired copies.
    Copies acquired after SSS have already been transferred and must not be
    counted again in a manual transfer reward. A missing history is an error.
    """
    _integer(acquired_copies, "acquired copies", 1)
    maximum = maximum_retained_copies(name, owned_before_run)
    if acquired_copies > maximum:
        raise RuleInputError("Overflow copies were already transferred; retained copy history is inconsistent")
    base = overflow_reward(name)
    return Resources(base.coins * acquired_copies, base.tokens * acquired_copies)


def unlocked_skill_count(name, imprint):
    hero = get_hero(name)
    imprint = validate_imprint(name, imprint)
    if hero.skill_count is None:
        raise UnknownRuleError(f"Skill count is not recorded for {hero.name}")
    locked = hero.ultimate_imprint is not None and imprint < hero.ultimate_imprint
    return hero.skill_count - int(locked)


def validate_class_bonuses(class_bonuses=None):
    """Omitted classes have no talent; unknown talent state must not be passed."""
    if class_bonuses is None:
        return {}
    if not isinstance(class_bonuses, Mapping):
        raise RuleInputError("Profession bonuses must be a mapping")
    result = {}
    for name, value in class_bonuses.items():
        if name not in PROFESSION_RANGES:
            raise RuleInputError(f"Unknown profession talent: {name!r}")
        result[name] = _integer(value, f"{name} talent", 0, 1)
    return result


def _team_names(names):
    if isinstance(names, (str, bytes)) or not isinstance(names, Iterable):
        raise RuleInputError("Team must be an explicit collection of hero names")
    canonical = tuple(get_hero(name).name for name in names)
    if len(canonical) != len(set(canonical)):
        raise RuleInputError("Duplicate heroes combine; they cannot count as separate team members")
    if len(canonical) > MAX_LEVEL + 1:
        raise RuleInputError("A team cannot exceed ten heroes")
    return canonical


def synergy_counts(names, class_bonuses=None):
    names = _team_names(names)
    bonuses = validate_class_bonuses(class_bonuses)
    counts = Counter({name: 0 for name in SYNERGIES})
    for name in names:
        hero = HEROES[name]
        counts[hero.faction] += 1
        counts[hero.profession] += 1
    counts.update(bonuses)
    return dict(counts)


def active_synergies(names, class_bonuses=None):
    counts = synergy_counts(names, class_bonuses)
    result = []
    for name, rule in SYNERGIES.items():
        reached = [i for i, threshold in enumerate(rule.thresholds) if counts[name] >= threshold]
        if reached:
            index = reached[-1]
            result.append(ActiveSynergy(name, counts[name], rule.thresholds[index], index + 1, rule.effects[index]))
    return tuple(result)
