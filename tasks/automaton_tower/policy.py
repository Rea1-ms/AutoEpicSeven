"""Data-only tower profiles and deterministic, screenshot-independent decisions."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import yaml

PROFESSIONS = frozenset(("骑士", "战士", "盗贼", "射手", "魔导士", "精灵师"))
ELEMENTS = frozenset(("火焰", "寒气", "自然", "光明", "黑暗"))
POSITIONS = frozenset(("前排", "后排", "上方", "下方"))
CONDITIONS = frozenset(("主输出职业", "主输出属性", "主输出位置", "队伍包含英雄"))
MAX_PROFILE_BYTES = 131072


class ProfileError(ValueError):
    """Invalid user data; report before any game action."""


class DecisionUnavailable(ValueError):
    """The current offer or team cannot authorize a choice."""


def normalize_name(value: str) -> str:
    """Normalize layout punctuation only; never invent missing name characters."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))


@dataclass(frozen=True)
class DeviceInfo:
    id: str
    name: str
    profession: str | None = None
    elements: tuple[str, ...] = ()
    hero: str | None = None
    position: str | None = None
    level: int | None = None
    max_level: int | None = None


class DeviceCatalog:
    def __init__(self, path: Path | None = None):
        data = json.loads(
            (path or Path(__file__).with_name("device_catalog.json")).read_text(
                encoding="utf-8"
            )
        )
        self.by_id = {}
        self.by_name = {}
        for entry in data["devices"]:
            fields = {
                key: value
                for key, value in entry.items()
                if key not in ("effect", "source", "aliases")
            }
            if "elements" in fields:
                fields["elements"] = tuple(fields["elements"])
            info = DeviceInfo(**fields)
            if info.id in self.by_id:
                raise ProfileError(f"装置编号重复：{info.id}")
            self.by_id[info.id] = info
            for name in (info.name, *entry.get("aliases", [])):
                key = normalize_name(name)
                if key in self.by_name:
                    raise ProfileError(f"装置名称重复：{name}")
                self.by_name[key] = info

    def resolve(self, text: str) -> DeviceInfo | None:
        return self.by_name.get(normalize_name(text))

    def require(self, text: str) -> DeviceInfo:
        info = self.resolve(text)
        if info is None:
            raise ProfileError(f"资料库未登记此完整装置名：{text}")
        return info


@dataclass(frozen=True)
class Hero:
    name: str
    profession: str | None = None
    element: str | None = None
    position: str | None = None


@dataclass(frozen=True)
class Team:
    heroes: tuple[Hero, ...] = ()
    main: Hero | None = None

    def applicable(self, device: DeviceInfo) -> bool | None:
        """Proven absence requires all four members and the required fields."""
        checks = []
        if device.profession:
            checks.append(("profession", (device.profession,)))
        if device.elements:
            checks.append(("element", device.elements))
        if device.hero:
            checks.append(("name", (device.hero,)))
        if device.position:
            checks.append(("position", (device.position,)))
        if not checks:
            return True
        complete = len(self.heroes) == 4 and all(
            getattr(hero, key) is not None for hero in self.heroes for key, _ in checks
        )
        members = self.heroes
        if self.main and (self.main.name or not complete):
            members += (self.main,)
        unknown = len(self.heroes) < 4
        for hero in members:
            # All restrictions describe one beneficiary; a front-row warrior
            # and a rear-row thief do not prove a front-row thief exists.
            states = [
                None if getattr(hero, key) is None else getattr(hero, key) in allowed
                for key, allowed in checks
            ]
            if all(state is True for state in states):
                return True
            if False not in states:
                unknown = True
        return None if unknown else False

    def condition(self, key: str, value: str) -> bool | None:
        if key == "队伍包含英雄":
            if any(hero.name == value for hero in self.heroes):
                return True
            return False if len(self.heroes) == 4 else None
        field_name = {
            "主输出职业": "profession",
            "主输出属性": "element",
            "主输出位置": "position",
        }[key]
        observed = getattr(self.main, field_name) if self.main else None
        return observed == value if observed is not None else None


@dataclass(frozen=True)
class Rule:
    devices: tuple[str, ...]
    conditions: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Profile:
    name: str
    team: Team
    rules: tuple[Rule, ...]
    denied: frozenset[str] = field(default_factory=frozenset)
    fallback: str = "第一张适用装置"


@dataclass(frozen=True)
class Candidate:
    slot: int
    device: DeviceInfo
    level: int | None = None
    max_level: int | None = None


@dataclass(frozen=True)
class Choice:
    candidate: Candidate
    reason: str


class _UniqueLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str) or key in result:
            raise ProfileError(f"配置键必须是唯一文字：{key}")
        result[key] = loader.construct_object(value_node)
    return result


_UniqueLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping
)


def _mapping(value, allowed, location):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise ProfileError(f"{location}包含未知字段或不是配置对象")
    return value


def _text(value, location):
    if not isinstance(value, str) or not value.strip():
        raise ProfileError(f"{location}需要非空文字")
    return value.strip()


def _option(value, allowed, location):
    if value is not None and (not isinstance(value, str) or value not in allowed):
        raise ProfileError(f"{location}不在允许范围内")
    return value


def parse_profile(
    content: bytes | str, catalog: DeviceCatalog | None = None
) -> Profile:
    if isinstance(content, bytes):
        try:
            content = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ProfileError("方案需要使用统一编码保存") from exc
    if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_PROFILE_BYTES:
        raise ProfileError("方案文件过大或不是文本")
    try:
        data = yaml.load(content, Loader=_UniqueLoader)
    except yaml.YAMLError as exc:
        raise ProfileError(f"方案格式错误：{exc}") from exc
    _mapping(
        data,
        (
            "版本",
            "方案名",
            "队伍",
            "装置优先顺序",
            "禁选装置",
            "名单外处理",
            "识别失败处理",
        ),
        "方案",
    )
    if type(data.get("版本", 1)) is not int or data.get("版本", 1) != 1:
        raise ProfileError("方案版本目前只支持1")
    name = _text(data.get("方案名"), "方案名")
    team_data = _mapping(
        data.get("队伍", {}),
        ("英雄", "主输出", "主输出职业", "主输出属性", "主输出位置"),
        "队伍",
    )
    heroes_data = team_data.get("英雄", [])
    if not isinstance(heroes_data, list) or len(heroes_data) > 4:
        raise ProfileError("队伍英雄需要填写最多四项的列表")
    heroes = []
    for item in heroes_data:
        _mapping(item, ("姓名", "职业", "属性", "位置"), "英雄")
        heroes.append(
            Hero(
                _text(item.get("姓名"), "英雄姓名"),
                _option(item.get("职业"), PROFESSIONS, "英雄职业"),
                _option(item.get("属性"), ELEMENTS, "英雄属性"),
                _option(item.get("位置"), POSITIONS, "英雄位置"),
            )
        )
    if len({hero.name for hero in heroes}) != len(heroes):
        raise ProfileError("队伍英雄不能重复")
    positions = [hero.position for hero in heroes if hero.position]
    if len(set(positions)) != len(positions):
        raise ProfileError("队伍站位不能重复")
    main = None
    if "主输出" in team_data:
        main_name = _text(team_data["主输出"], "主输出")
        main = next((hero for hero in heroes if hero.name == main_name), None)
        if main is None:
            raise ProfileError("主输出需要在已填写的队伍英雄中")
    traits = (
        _option(team_data.get("主输出职业"), PROFESSIONS, "主输出职业"),
        _option(team_data.get("主输出属性"), ELEMENTS, "主输出属性"),
        _option(team_data.get("主输出位置"), POSITIONS, "主输出位置"),
    )
    if main:
        original = (main.profession, main.element, main.position)
        if any(old and new and old != new for old, new in zip(original, traits)):
            raise ProfileError("主输出描述与队伍英雄填写不一致")
        main = Hero(main.name, *(new or old for old, new in zip(original, traits)))
    elif any(traits):
        if len(heroes) == 4 and not any(
            all(
                old is None or new is None or old == new
                for old, new in zip(
                    (hero.profession, hero.element, hero.position), traits
                )
            )
            for hero in heroes
        ):
            raise ProfileError("主输出画像与已填写的四人队伍不一致")
        main = Hero("", *traits)
    catalog = catalog or DeviceCatalog()

    def device_ids(value, location):
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list) or not value:
            raise ProfileError(f"{location}需要装置名称或非空列表")
        ids = tuple(catalog.require(_text(text, location)).id for text in value)
        if len(set(ids)) != len(ids):
            raise ProfileError(f"{location}包含重复装置")
        return ids

    rules_data = data.get("装置优先顺序", [])
    if not isinstance(rules_data, list):
        raise ProfileError("装置优先顺序需要列表")
    rules = []
    for item in rules_data:
        conditions = {}
        if isinstance(item, dict):
            _mapping(item, ("装置", "条件"), "装置规则")
            conditions = _mapping(item.get("条件", {}), CONDITIONS, "规则条件")
            item = item.get("装置")
        for key, value in conditions.items():
            _text(value, key)
            allowed = {
                "主输出职业": PROFESSIONS,
                "主输出属性": ELEMENTS,
                "主输出位置": POSITIONS,
            }.get(key)
            if allowed:
                _option(value, allowed, key)
        rule = Rule(device_ids(item, "优先装置"), tuple(conditions.items()))
        if rule in rules:
            raise ProfileError("装置规则重复")
        rules.append(rule)
    denied_data = data.get("禁选装置", [])
    denied = (
        frozenset(device_ids(denied_data, "禁选装置"))
        if denied_data != []
        else frozenset()
    )
    fallback = _option(
        _text(data.get("名单外处理", "第一张适用装置"), "名单外处理"),
        ("第一张适用装置", "暂停"),
        "名单外处理",
    )
    if data.get("识别失败处理", "暂停") != "暂停":
        raise ProfileError("识别失败处理目前只支持暂停")
    return Profile(name, Team(tuple(heroes), main), tuple(rules), denied, fallback)


def choose_device(profile: Profile, candidates: tuple[Candidate, ...]) -> Choice:
    if len(candidates) != 3 or {candidate.slot for candidate in candidates} != {
        0,
        1,
        2,
    }:
        raise DecisionUnavailable("需要本帧完整的三张装置")
    available = sorted(
        (
            candidate
            for candidate in candidates
            if candidate.device.id not in profile.denied
        ),
        key=lambda c: c.slot,
    )
    for index, rule in enumerate(profile.rules, 1):
        matching = [
            candidate for candidate in available if candidate.device.id in rule.devices
        ]
        if not matching:
            continue
        checks = [profile.team.condition(key, value) for key, value in rule.conditions]
        if False in checks:
            continue
        if None in checks:
            raise DecisionUnavailable(f"第{index}优先规则缺少队伍信息")
        for candidate in matching:
            applicable = profile.team.applicable(candidate.device)
            if applicable is None:
                raise DecisionUnavailable(f"无法确认适用队伍：{candidate.device.name}")
            if applicable:
                return Choice(candidate, f"profile priority {index}")
    if profile.fallback == "第一张适用装置":
        for candidate in available:
            applicable = profile.team.applicable(candidate.device)
            if applicable is None:
                raise DecisionUnavailable(
                    f"回退选择缺少队伍信息：{candidate.device.name}"
                )
            if applicable:
                return Choice(candidate, "first applicable fallback")
    raise DecisionUnavailable("没有方案允许的适用装置")
