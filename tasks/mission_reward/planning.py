"""Plan daily milestones without replacing observed progress with click counts."""

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from math import ceil

from module.config import server
from module.config.utils import get_server_last_update, get_server_next_update
from tasks.mission_reward.scheduling import (
    ARENA_MISSIONS,
    CORE_MISSIONS,
    STAMINA_MISSIONS,
    _current_daily_plan,
    daily_priority_enabled,
    daily_snapshot_context,
)

DAILY_BUDGET_PATH = "MissionReward.MissionReward.DailyBudget"
_MISSION_KEYS = ARENA_MISSIONS | CORE_MISSIONS | STAMINA_MISSIONS | {
    "login", "gacha", "flower", "hero", "equipment",
}
_KINDS = {"Arena": ("arena",), "Combat": ("fast", "repeat")}


@dataclass(frozen=True)
class BatchDecision:
    count: int
    extra: bool = False
    reason: str = "run"


def _number(value) -> int:
    return value if type(value) is int and value >= 0 else 0


def _update(config):
    return config.cross_get(
        "MissionReward.Scheduler.ServerUpdate", default=config.Scheduler_ServerUpdate
    )


def _context(config) -> dict:
    return {
        **daily_snapshot_context(config),
        "day": get_server_last_update(_update(config)).isoformat(),
    }


def _key(context: dict) -> str:
    # Changing recognition language invalidates observations, not resources
    # already consumed on the same account/server day.
    return "|".join(context[name] for name in ("server", "day"))


def _plan(config) -> dict | None:
    plan = _current_daily_plan(config)
    if plan is None:
        return None
    # Once all boxes have been claimed, the client replaces the entire list
    # with a confirmation message. Otherwise all thirteen rows are required;
    # a partial list cannot prove that another route has no remaining reward.
    if plan["points"] >= 100 and plan["rewards_claimed"]:
        return plan
    keys = [mission["key"] for mission in plan["missions"]]
    if len(keys) != len(_MISSION_KEYS) or set(keys) != _MISSION_KEYS:
        return None
    return plan


def _supported(config, executor: str) -> bool:
    if executor not in _KINDS or not daily_priority_enabled(config):
        return False
    if not config.cross_get(f"{executor}.Scheduler.Enable", default=False):
        return False
    if config.cross_get(f"{executor}.{executor}.BurnoutMode", default="Daily") not in ("Daily", False):
        return False
    if executor == "Arena":
        return bool(config.cross_get("Arena.Arena.NPCCombat", default=True))
    return _combat_supported(config)


def _combat_supported(config) -> bool:
    domain = config.cross_get("Combat.Combat.Domain", default="Hunt")
    if domain == "UrgentTasks":
        domain = "Hunt"
    if domain not in ("Hunt", "SpiritAltar", "Saint37", "Episode4"):
        return False
    if domain == "Hunt" and config.cross_get("Combat.Combat.HuntBoss", default="Wyvern") == "Ogre":
        return False
    return not (
        domain == "SpiritAltar"
        and server.is_cn_server(config.Emulator_PackageName)
        and config.cross_get("Combat.Combat.AltarBalance", default=False)
    )


def _new_state(limits: dict) -> dict:
    return {
        "limits": dict(limits), "used": {}, "extra_used": {}, "pending": None,
        "awaiting_review": False, "stamina_per_unit": {}, "capacity": {},
    }


class DailyBudget:
    """Persist reservations before irreversible actions, separately from OCR.

    A reservation consumes its allowance immediately. A restart cannot establish
    whether the game accepted a click, so an unresolved reservation is never
    refunded or replayed. Only the executor's positive settlement evidence may
    call ``confirm``. A fresh daily scan alone is not settlement evidence.
    """

    retry_minutes = 5

    def __init__(self, config, executor: str, limits: dict[str, int]):
        self.config = config
        self.executor = executor
        self.limits = {kind: _number(limits.get(kind)) for kind in _KINDS.get(executor, ())}
        self.active = _supported(config, executor)
        self._context = _context(config) if self.active else {}
        self._key = _key(self._context) if self.active else ""
        self._decision = None
        if self.active:
            root, state = self._load()
            state["limits"] = dict(self.limits)
            self._save(root)

    def _load(self):
        root = deepcopy(self.config.cross_get(DAILY_BUDGET_PATH, default={}))
        if not isinstance(root, dict):
            raise TypeError("Daily budget must be a dictionary")
        root.setdefault("version", 1)
        contexts = root.setdefault("contexts", {})
        if root["version"] != 1 or not isinstance(contexts, dict):
            raise ValueError("Unknown daily budget schema")
        bucket = contexts.setdefault(self._key, {**self._context, "executors": {}})
        state = bucket["executors"].setdefault(self.executor, _new_state(self.limits))
        return root, state

    def _save(self, root):
        self.config.cross_set(DAILY_BUDGET_PATH, root)

    def _pending_state(self, root):
        # A language change or reset does not settle a battle. Preserve old
        # contexts and block this executor on the same server until the actual
        # result is confirmed; another server has independent resources.
        for bucket in root["contexts"].values():
            if bucket.get("server") != self._context["server"]:
                continue
            state = bucket.get("executors", {}).get(self.executor, {})
            if state.get("pending") is not None:
                return state
        return None

    @property
    def pending(self) -> dict | None:
        if not self.active:
            return None
        root, _ = self._load()
        state = self._pending_state(root)
        return deepcopy(state["pending"]) if state else None

    @property
    def awaiting_review(self) -> bool:
        return bool(self._load()[1]["awaiting_review"]) if self.active else False

    @property
    def needs_daily(self) -> bool:
        if not self.active:
            return False
        plan = _plan(self.config)
        return plan is None or not (plan["points"] >= 100 and plan["rewards_claimed"])

    @property
    def has_remaining(self) -> bool:
        return any(self.remaining(kind) > 0 for kind in self.limits)

    def remaining(self, kind: str) -> int:
        if not self.active:
            return self.limits.get(kind, 0)
        _, state = self._load()
        return max(0, self.limits.get(kind, 0) - _number(state["used"].get(kind)))

    def choose(self, kind: str, affordable: int, stamina_per_unit: int = 0) -> BatchDecision:
        """Choose a bounded batch; affordability must come from fresh UI evidence."""
        self._decision = None
        if kind not in self.limits:
            return BatchDecision(0, reason="complete")
        affordable = _number(affordable)
        remaining = self.remaining(kind)
        if not self.active:
            return BatchDecision(min(remaining, affordable), reason="run" if remaining and affordable else "complete")
        if _context(self.config) != self._context or self.pending is not None:
            return BatchDecision(0, reason="uncertain")
        self.observe_capacity(kind, affordable)
        plan = _plan(self.config)
        if self.awaiting_review or plan is None:
            return BatchDecision(0, reason="review")
        if plan["points"] >= 100:
            if not plan["rewards_claimed"]:
                return BatchDecision(0, reason="review")
            decision = BatchDecision(min(remaining, affordable), reason="run" if remaining and affordable else "complete")
            self._decision = (kind, decision)
            return decision
        root, state = self._load()
        if kind != "arena" and _number(stamina_per_unit):
            state["stamina_per_unit"][kind] = stamina_per_unit
            self._save(root)
        keys = ARENA_MISSIONS if kind == "arena" else STAMINA_MISSIONS
        missing = [mission for mission in plan["missions"]
                   if mission["key"] in keys and mission["current"] < mission["total"]]
        if not missing:
            return BatchDecision(0, reason="wait" if remaining else "complete")
        per_unit = 1 if kind == "arena" else _number(stamina_per_unit)
        if not per_unit:
            return BatchDecision(0, reason="uncertain")
        needed = min(ceil((mission["total"] - mission["current"]) / per_unit) for mission in missing)
        if not affordable:
            return BatchDecision(0, reason="wait")
        if remaining:
            decision = BatchDecision(min(needed, remaining, affordable))
        elif self._future_can_complete(plan, root):
            return BatchDecision(0, reason="wait")
        else:
            decision = BatchDecision(min(needed, affordable), extra=True)
        self._decision = (kind, decision)
        return decision

    def observe_capacity(self, kind: str, affordable: int):
        """Record observed exhaustion so another executor can reconsider waiting."""
        if not self.active:
            return
        root, state = self._load()
        state.setdefault("capacity", {})[kind] = _number(affordable)
        self._save(root)

    def reserve(self, kind: str, count: int, extra: bool = False):
        if not self.active:
            return
        if self.pending is not None or _context(self.config) != self._context:
            raise ValueError("An unresolved daily action cannot be repeated")
        if type(count) is not int or count <= 0:
            raise ValueError("A daily reservation must have a positive count")
        decision = self._decision
        if (decision is None or decision[0] != kind or decision[1].reason != "run"
                or decision[1].extra != extra or count > decision[1].count):
            raise ValueError("Reserve only an approved daily batch")
        root, state = self._load()
        name = "extra_used" if extra else "used"
        state[name][kind] = _number(state[name].get(kind)) + count
        capacity = state.setdefault("capacity", {}).get(kind)
        if type(capacity) is int:
            state["capacity"][kind] = max(0, capacity - count)
        state["pending"] = {"kind": kind, "count": count, "extra": extra,
                            "reserved_at": datetime.now(timezone.utc).isoformat()}
        self._save(root)
        self._decision = None

    def confirm(self):
        if not self.active:
            return
        root, _ = self._load()
        state = self._pending_state(root)
        if state is not None:
            state["pending"] = None
            self._save(root)

    def request_review(self):
        if not self.active:
            return
        root, state = self._load()
        state["awaiting_review"] = True
        self._save(root)

    def _scheduled_today(self, task: str) -> bool:
        if not self.config.cross_get(f"{task}.Scheduler.Enable", default=False):
            return False
        if task == self.executor:
            return True
        value = self.config.cross_get(f"{task}.Scheduler.NextRun", default=None)
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value)
            except ValueError:
                return False
        if not isinstance(value, datetime):
            return False
        try:
            return value < get_server_next_update(_update(self.config))
        except TypeError:
            return False

    def _future_can_complete(self, plan: dict, root: dict) -> bool:
        missions = {mission["key"]: mission for mission in plan["missions"]}
        potential = plan["points"]
        for key, task in (("gacha", "Gacha"), ("flower", "SanctuaryDaily")):
            mission = missions[key]
            if mission["current"] < mission["total"] and self._scheduled_today(task):
                potential += mission["points"]
        states = root["contexts"][self._key]["executors"]
        for task, keys in (("Arena", ARENA_MISSIONS), ("Combat", STAMINA_MISSIONS)):
            if not self._scheduled_today(task):
                continue
            missing = [missions[key] for key in keys if missions[key]["current"] < missions[key]["total"]]
            if not missing:
                continue
            if task == "Arena" and not self.config.cross_get("Arena.Arena.NPCCombat", default=True):
                continue
            if task == "Combat" and not _combat_supported(self.config):
                continue
            state = states.get(task, {})
            if state.get("pending") is not None:
                # The other executor must settle before deciding that its
                # reserved resources yielded no progress. Do not speculate.
                return True
            burnout = self.config.cross_get(f"{task}.{task}.BurnoutMode", default="Daily")
            if burnout is True or burnout == "Burnout":
                potential += sum(mission["points"] for mission in missing)
                continue
            if task == "Arena":
                defaults = {"arena": _number(self.config.cross_get("Arena.Arena.NPCCombatCount", default=5))}
            else:
                fast = self.config.cross_get("Combat.Combat.FastCombat", default=True)
                domain = self.config.cross_get("Combat.Combat.Domain", default="Hunt")
                defaults = {
                    "fast": _number(self.config.cross_get("Combat.Combat.FastCombatCount", default=10))
                    if fast and domain != "Saint37" else 0,
                    "repeat": _number(self.config.cross_get("Combat.Combat.RepeatCombatLeifCount", default=1)),
                }
            limits = self.limits if task == self.executor else defaults
            progress = max(missions[key]["current"] for key in keys)
            unknown = False
            for kind, limit in limits.items():
                remaining = max(0, limit - _number(state.get("used", {}).get(kind)))
                cost = 1 if kind == "arena" else _number(state.get("stamina_per_unit", {}).get(kind))
                if kind == "repeat":
                    cost = 80
                elif kind == "fast" and not cost:
                    from tasks.dungeon.burnout import (
                        ALTAR_STAMINA_COST,
                        HUNT_STAMINA_COST,
                    )

                    domain = self.config.cross_get("Combat.Combat.Domain", default="Hunt")
                    if domain == "SpiritAltar":
                        grade = self.config.cross_get("Combat.Combat.AltarGrade", default="Hell")
                        cost = ALTAR_STAMINA_COST.get(grade, 0)
                    elif domain == "Episode4":
                        cost = 20
                    elif domain == "Saint37":
                        cost = 8
                    else:
                        grade = self.config.cross_get("Combat.Combat.HuntGrade", default="Hell")
                        cost = HUNT_STAMINA_COST.get(grade, 0)
                capacity = state.get("capacity", {}).get(kind)
                if type(capacity) is int:
                    remaining = min(remaining, max(0, capacity))
                if remaining and not cost:
                    unknown = True
                progress += remaining * cost
            # Unknown costs can cover at most this route's remaining rewards.
            # Waiting is justified only if even that upper bound could finish.
            potential += sum(mission["points"] for mission in missing
                             if unknown or progress >= mission["total"])
        return potential >= 100


def acknowledge_daily_review(config):
    """A complete current observation releases review waits, never reservations."""
    if not daily_priority_enabled(config) or _plan(config) is None:
        return
    root = deepcopy(config.cross_get(DAILY_BUDGET_PATH, default={}))
    if not isinstance(root, dict) or root.get("version") != 1:
        return
    bucket = root.get("contexts", {}).get(_key(_context(config)))
    if bucket is None:
        return
    changed = False
    for state in bucket.get("executors", {}).values():
        if state.get("awaiting_review"):
            state["awaiting_review"] = False
            changed = True
    if changed:
        config.cross_set(DAILY_BUDGET_PATH, root)
