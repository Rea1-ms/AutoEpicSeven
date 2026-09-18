"""Game facts, independent of task implementations and account state.

Design reference: MAA's OnSideStory condition and activity query, documented in
README.md. This implementation does not reuse MAA code or its game data.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
import re
from types import MappingProxyType
from typing import Mapping

import yaml


INFO_PATH = Path(__file__).with_name("data.yaml")
INFO_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")
SERVER_FAMILIES = ("OVERSEA", "CN")


def aware_time(value: datetime | None = None) -> datetime:
    """Convert scheduler-local naive times or aware times to the data timezone."""
    return (value or datetime.now(INFO_TIMEZONE)).astimezone(INFO_TIMEZONE)


@dataclass(frozen=True)
class GamePeriod:
    event_id: str
    kind: str
    name: str
    server_family: str
    start: datetime
    end: datetime
    values: Mapping
    source: str
    inferred: bool = False

    def contains(self, now: datetime) -> bool:
        return self.start <= aware_time(now) < self.end


@dataclass(frozen=True)
class GameInfo:
    defaults: Mapping
    periods: tuple[GamePeriod, ...]

    def current(self, kind: str, family: str, now: datetime | None = None) -> GamePeriod | None:
        _family(family)
        now = aware_time(now)
        return next((p for p in self.periods
                     if p.kind == kind and p.server_family == family and p.contains(now)), None)

    def values(self, kind: str, family: str, now: datetime | None = None) -> Mapping:
        values = dict(self.defaults.get(kind, {}))
        period = self.current(kind, family, now)
        if period is not None:
            values.update(period.values)
            values["source"] = period.source
        return MappingProxyType(values)

    def level_cap(self, kind: str, family: str, now: datetime | None = None) -> int:
        value = self.values(kind, family, now).get("max_level")
        if type(value) is not int or value <= 0:
            raise ValueError(f"No positive max_level for {kind}/{family}")
        return value

    def next_change(self, kind: str, family: str, after: datetime) -> datetime | None:
        """Find a boundary after an observation; it may already be in the past."""
        _family(family)
        after = aware_time(after)
        return min((time for p in self.periods
                    if p.kind == kind and p.server_family == family
                    for time in (p.start, p.end) if time > after), default=None)


def _family(value):
    if value not in SERVER_FAMILIES:
        raise ValueError(f"Unknown game-info server family: {value}")


def _mapping(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a mapping")
    return value


def _keys(value, allowed, label):
    unknown = set(value) - set(allowed)
    if unknown:
        raise ValueError(f"Unknown fields in {label}: {unknown}")


def _text(value, label):
    if not isinstance(value, str) or not value.strip() or any(c in value for c in "\r\n"):
        raise ValueError(f"{label} must be nonempty single-line text")
    return value


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", value):
        raise ValueError(f"Invalid game-info identifier: {value}")
    return value


def _timestamp(value):
    timestamp = datetime.fromisoformat(str(value))
    if timestamp.tzinfo is None:
        raise ValueError("Game-info timestamps must include a timezone")
    return timestamp.astimezone(INFO_TIMEZONE)


def _values(value):
    value = _mapping(value, "values")
    for key, item in value.items():
        _identifier(key)
        if type(item) not in (str, int, bool):
            raise ValueError(f"Game-info values must be strings, integers or booleans: {key}")
        if key == "max_level" and (type(item) is not int or item <= 0):
            raise ValueError("max_level must be a positive integer")
    return MappingProxyType(dict(value))


class _UniqueLoader(yaml.SafeLoader):
    """Reject duplicate YAML keys instead of silently discarding earlier facts."""


def _unique_mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if not isinstance(key, str) or key in result:
            raise ValueError(f"Non-string or duplicate game-info key: {key}")
        result[key] = loader.construct_object(value_node, deep=True)
    return result


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


@lru_cache(maxsize=4)
def parse_info(text: str) -> GameInfo:
    data = _mapping(yaml.load(text, Loader=_UniqueLoader), "game info")
    _keys(data, ("schema_version", "defaults", "cn_delay_days", "events"), "game info")
    if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise ValueError("Unsupported game-info schema_version (expected 1)")
    defaults = {}
    for kind, values in _mapping(data.get("defaults", {}), "defaults").items():
        _identifier(kind)
        defaults[kind] = _values(values)
        _text(values.get("source"), f"defaults.{kind}.source")
    delays = _mapping(data.get("cn_delay_days", {}), "cn_delay_days")
    for kind, days in delays.items():
        _identifier(kind)
        if type(days) is not int or days < 0:
            raise ValueError("cn_delay_days values must be nonnegative integers")
    if not isinstance(data.get("events"), list):
        raise ValueError("events must be a list")
    periods, ids = [], set()
    for event in data["events"]:
        _mapping(event, "event")
        _keys(event, ("id", "kind", "name", "source", "oversea_start", "oversea_end",
                      "cn_start", "cn_end", "values", "cn_values"), "event")
        event_id = _identifier(event.get("id"))
        if event_id in ids:
            raise ValueError(f"Duplicate game-info id: {event_id}")
        ids.add(event_id)
        kind = _identifier(event.get("kind"))
        name = _text(event.get("name"), f"{event_id}.name")
        source = _text(event.get("source"), f"{event_id}.source")
        common = dict(_values(event.get("values", {})))
        cn_values = dict(_values(event.get("cn_values", {})))
        oversea = {}
        if "oversea_start" in event or "oversea_end" in event:
            if not all(f"oversea_{key}" in event for key in ("start", "end")):
                raise ValueError(f"Incomplete OVERSEA window: {event_id}")
            oversea = {key: _timestamp(event[f"oversea_{key}"]) for key in ("start", "end")}
        cn = {}
        inferred = False
        if oversea and kind in delays:
            cn = {key: value + timedelta(days=delays[kind]) for key, value in oversea.items()}
            inferred = not all(f"cn_{key}" in event for key in ("start", "end"))
        for key in ("start", "end"):
            if f"cn_{key}" in event:
                cn[key] = _timestamp(event[f"cn_{key}"])
        if cn and len(cn) != 2:
            raise ValueError(f"Incomplete CN window: {event_id}; set dates or a kind-specific delay")
        if not oversea and not cn:
            raise ValueError(f"Event has no server windows: {event_id}")
        if cn_values and not cn:
            raise ValueError(f"cn_values without a CN window: {event_id}")
        for family, window in (("OVERSEA", oversea), ("CN", cn)):
            if not window:
                continue
            if window["start"] >= window["end"]:
                raise ValueError(f"Period must end after it starts: {event_id}/{family}")
            values = {**common, **(cn_values if family == "CN" else {})}
            periods.append(GamePeriod(event_id, kind, name, family, window["start"], window["end"],
                                      MappingProxyType(values), source, inferred and family == "CN"))
    # A category describes one version of a feature per server. Ambiguous
    # overlaps must fail validation rather than depend on YAML ordering.
    for index, first in enumerate(periods):
        for second in periods[index + 1:]:
            if (first.kind == second.kind and first.server_family == second.server_family
                    and first.start < second.end and second.start < first.end):
                raise ValueError(f"Overlapping game periods: {first.event_id}/{second.event_id}")
    return GameInfo(MappingProxyType(defaults), tuple(periods))


def load_info(path: Path | None = None) -> GameInfo:
    # Read the current file on every query; cache parsing by content, not by
    # mtime. A replaced file with the same timestamp must not leave stale facts
    # in a long-running worker. Each returned snapshot is immutable.
    path = path or INFO_PATH
    try:
        return parse_info(path.read_text(encoding="utf-8"))
    except (ValueError, yaml.YAMLError) as exc:
        raise ValueError(f"Invalid game info in {path}: {exc}") from exc
