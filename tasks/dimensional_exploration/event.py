"""Event catalogue, selection policy and confirmed observations, without UI actions."""

from dataclasses import dataclass, field
from functools import lru_cache
import json
from pathlib import Path
import re

from tasks.dimensional_exploration.policy import EventChoice, normalize


@dataclass(frozen=True)
class EventCost:
    fragments: int = 0
    dice: int = 0
    loot: int = 0
    life: int = 0
    random_hp_percent: int = 0
    all_hp_percent: int = 0
    battle: int = 0

    def unavailable(self, *, cores, fragments, life, loot, dice=None):
        if self.life and self.life >= life:
            return "保留最后一点生命体征"
        if self.fragments and self.fragments + (50 if cores < 20 else 0) > fragments:
            return "碎片不足或需要保留投资额度"
        if self.loot and (loot is None or self.loot > loot):
            return "战利品数量不足或未识别"
        if self.dice and (dice is None or self.dice > dice):
            return "骰子数量不足或未识别"
        # Hero HP is a different resource from the run's death allowance. A
        # percentage cannot be subtracted from life, or validated against it.
        # Without individual HP readings, damage remains an explicit cost in
        # the catalogue and low-damage repeat routes are preferred by policy.
        return ""


@dataclass(frozen=True)
class EventBranch:
    event_id: str
    branch_id: str
    pattern: str
    cost: EventCost
    rewards: tuple[dict, ...]
    priority: int

    @property
    def key(self):
        return f"{self.event_id}.{self.branch_id}"

    @property
    def collectible(self):
        return next((r["name"] for r in self.rewards if r["kind"] == "loot" and r.get("name")), None)


@dataclass(frozen=True)
class EventSpec:
    event_id: str
    name: str
    anchors: tuple[str, ...]
    branches: tuple[EventBranch, ...]


@lru_cache(maxsize=1)
def event_catalog():
    data = json.loads(Path(__file__).with_name("event_catalog.json").read_text(encoding="utf-8"))
    result = []
    reward_kinds = {"loot", "dice", "fragments", "experience", "rank", "all_hp_percent", "life"}
    for event_id, item in data.items():
        branches = []
        for branch_id, entry in item["branches"].items():
            re.compile(entry["pattern"])
            cost = EventCost(**entry["cost"])
            if any(type(value) is not int or value < 0 for value in vars(cost).values()):
                raise ValueError(f"Invalid event cost: {event_id}.{branch_id}")
            if max(cost.random_hp_percent, cost.all_hp_percent) > 100:
                raise ValueError(f"Invalid HP percentage: {event_id}.{branch_id}")
            for reward in entry["rewards"]:
                if reward["kind"] not in reward_kinds or not 0 < reward.get("chance", 100) <= 100:
                    raise ValueError(f"Invalid event reward: {event_id}.{branch_id}")
            branches.append(EventBranch(event_id, branch_id, entry["pattern"], cost,
                                        tuple(entry["rewards"]), entry["priority"]))
        result.append(EventSpec(event_id, item["name"], tuple(item["anchors"]), tuple(branches)))
    return tuple(result)


def event_branch(key):
    return next((branch for event in event_catalog() for branch in event.branches if branch.key == key), None)


@dataclass
class EventMemory:
    """Keep attempts separate from acquisitions, scoped to one task profile.

    A successful click only creates a pending choice. Repeated/unknown frames
    never mark it visited. Positive outcome pages mark the branch visited; only
    a matching reward card reached from our option click records acquisition.
    The magnifier opens a preview and must never be used as an unrecorded flag.
    """

    visited: set[str] = field(default_factory=set)
    obtained: set[str] = field(default_factory=set)
    pending: str | None = None
    advanced: bool = False

    @classmethod
    def from_saved(cls, value):
        if not value:
            return cls()
        if (not isinstance(value, dict) or value.get("version") != 1
                or not isinstance(value.get("visited"), list) or not isinstance(value.get("obtained"), list)
                or not all(isinstance(v, str) for v in value["visited"] + value["obtained"])
                or (value.get("pending") is not None and event_branch(value["pending"]) is None)):
            raise ValueError("次元探查事件记录格式不正确，请检查本配置的事件记录。")
        return cls(set(value["visited"]), set(value["obtained"]), value.get("pending"),
                   bool(value.get("advanced", False)))

    def as_dict(self):
        return {"version": 1, "visited": sorted(self.visited), "obtained": sorted(self.obtained),
                "pending": self.pending, "advanced": self.advanced}

    def begin(self, branch):
        self.pending, self.advanced = branch.key, False

    def observe(self, state, *, narration=False, reward_name=""):
        if not self.pending:
            return False
        before = self.as_dict()
        branch = event_branch(self.pending)
        if state == "reward" and branch.collectible and normalize(reward_name) == normalize(branch.collectible):
            self.obtained.add(branch.collectible)
        if narration or state in ("reward", "loot", "prepare", "battle", "victory", "map", "failed", "settlement"):
            self.visited.add(self.pending)
            self.advanced = True
        # Keep the pending branch through narration/battle: a reward may appear
        # later. Returning to the map or starting another run closes it. These
        # observations are persisted, so a restart on a reward page is safe.
        if state in ("map", "failed", "settlement", "lobby", "start_supply", "recruitment"):
            self.pending, self.advanced = None, False
        return before != self.as_dict()


@dataclass(frozen=True)
class EventDecision:
    event: EventSpec
    branch: EventBranch
    choice: EventChoice
    first_collectible: bool


def match_event(choices, story=""):
    """Match all visible options, including their exact costs, before selecting.

    These screens have narration instead of individual event titles. Stable
    story anchors plus full option patterns identify the catalogue entry. No
    fuzzy matching is allowed for amounts, chances or HP percentages.
    """
    matches = []
    for event in event_catalog():
        if story and not all(anchor in normalize(story) for anchor in event.anchors):
            continue
        paired = []
        for choice in choices:
            text = normalize(choice.text).replace("％", "%")
            branches = [b for b in event.branches if re.fullmatch(b.pattern, text)]
            if len(branches) != 1:
                break
            paired.append((choice, branches[0]))
        else:
            if paired and len({b.key for _, b in paired}) == len(paired):
                matches.append((event, paired))
    return matches[0] if len(matches) == 1 else None


def decide_event(choices, *, cores, fragments, life, loot, dice=None, story="", memory=None):
    matched = match_event(choices, story)
    if matched is None:
        return None
    event, paired = matched
    memory = memory or EventMemory()
    if memory.pending and not memory.advanced:
        for choice, branch in paired:
            if memory.pending == branch.key:
                if branch.cost.unavailable(cores=cores, fragments=fragments, life=life, loot=loot, dice=dice):
                    return None
                return EventDecision(event, branch, choice, False)
    ranked = []
    for choice, branch in paired:
        if branch.cost.unavailable(cores=cores, fragments=fragments, life=life, loot=loot, dice=dice):
            continue
        # A first attempt is only a collection opportunity, not proof of unlock.
        # Check all routes to the same named item to avoid paying/fighting twice
        # for it. Failed/chance attempts still switch to the repeat route; the
        # obtained set remains truthful even when no reward was received.
        item = branch.collectible
        tried_item = any(b.collectible == item and b.key in memory.visited for b in event.branches)
        first = bool(item and item not in memory.obtained and not tried_item)
        ranked.append((first, branch.priority, -choice.index, EventDecision(event, branch, choice, first)))
    return max(ranked, key=lambda row: row[:3])[-1] if ranked else None
