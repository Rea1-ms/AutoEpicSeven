"""Screenshot-independent decisions for a complete exploration run."""

from dataclasses import dataclass
import re


def normalize(text: str) -> str:
    return re.sub(r"[\s·：:，,。.!！?？]", "", text)


def parse_number(text: str, maximum=99999) -> int | None:
    text = text.strip().replace(",", "").replace("，", "")
    if re.fullmatch(r"\d+", text):
        value = int(text)
        if value <= maximum:
            return value
    return None


def parse_counter(text: str, maximum=99) -> tuple[int, int] | None:
    match = re.fullmatch(r"\s*(\d+)\s*/\s*(\d+)\s*", text)
    if match:
        current, total = map(int, match.groups())
        if 0 <= current <= maximum and 0 < total <= maximum:
            return current, total
    return None


def priorities(text: str) -> tuple[str, ...]:
    return tuple(filter(None, (normalize(part) for part in re.split(r"[>＞,，;；\n]", text))))


def node_priority(kind: str, cores: int) -> int:
    first = ("shop", "event") if cores < 20 else ("event", "shop")
    return (*first, "supply", "elite", "battle", "rest", "boss").index(kind)


@dataclass(frozen=True)
class Offer:
    index: int
    name: str
    price: int | None
    sold: bool = False


def choose_offer(offers: list[Offer], fragments: int, life: int, max_life: int) -> Offer | None:
    available = [o for o in offers if not o.sold and o.price is not None and 0 < o.price <= fragments]
    # Investment is intentionally independent of held cores. Reaching 20 only
    # changes map routing; it must not stop accumulating resonance for the owner.
    for offer in available:
        if normalize(offer.name) == "未来投资":
            return offer
    for offer in available:
        if normalize(offer.name) == "恢复生命体征" and life < max_life:
            return offer
    loot = [o for o in available if o.index % 4 >= 2 and o.name]
    return min(loot, key=lambda o: (o.price, o.index), default=None)


@dataclass(frozen=True)
class EventChoice:
    index: int
    text: str
    journal: bool = False


def choose_event(choices: list[EventChoice], *, cores: int, fragments: int,
                 life: int, loot: int | None) -> EventChoice | None:
    """Rank understood rewards; unknown costs never imply a free option."""
    ranked = []
    forced = []
    for choice in choices:
        text = normalize(choice.text)
        fragment_cost = re.search(r"消耗(\d+)个?次元碎片", text)
        life_cost = re.search(r"消耗(\d+)点?生命体征", text)
        loot_cost = re.search(r"消耗(\d+)个?战利品", text)
        if "消耗" in text and not any((fragment_cost, life_cost, loot_cost)):
            continue
        if fragment_cost and int(fragment_cost[1]) + (50 if cores < 20 else 0) > fragments:
            continue
        if life_cost and int(life_cost[1]) > life:
            continue
        if loot_cost and (loot is None or int(loot_cost[1]) > loot):
            continue
        # Named loot and the journal magnifier take precedence over experience.
        # Prefer keeping one life whenever the encounter offers an alternative.
        reward = text.split("获得", 1)[-1] if "获得" in text else ""
        if reward and ("战利品" in reward or any(
                name in reward for name in ("月亮之书", "探险家指南针", "望远镜镜片"))):
            score = 80
        elif "升阶" in text or "Rank提升" in text:
            score = 50
        elif "恢复" in text:
            score = 30
        elif "经验" in text or "exp" in text.lower() or "修好烛台并将其放回原位" in text:
            score = 20
        elif "离开" in text:
            score = 0
        else:
            continue
        score += 100 if choice.journal else 0
        score -= 10 if "降低" in text else 0
        if life_cost and int(life_cost[1]) == life:
            # Some pool encounters offer only paid choices and have no Leave
            # button. If no nonlethal option exists, take the affordable choice
            # and let the game's normal failed-run settlement finish the run.
            forced.append((score, -choice.index, choice))
        else:
            ranked.append((score, -choice.index, choice))
    if not ranked:
        ranked = forced
    return max(ranked, key=lambda item: item[:2])[2] if ranked else None


@dataclass
class RunProgress:
    """Count a result only once and persist its latch through restarts.

    A settlement can stay visible across many screenshots and failed clicks.
    Re-arm only after positively identifying the next run's map/setup. Lobby
    frames do not re-arm it: replayed settlement frames must not consume runs.
    """

    completed: int = 0
    settlement_seen: bool = False

    def settle(self, rewarded: bool) -> bool:
        if self.settlement_seen:
            return False
        self.settlement_seen = True
        if rewarded:
            self.completed += 1
        return True

    def entered_run(self):
        self.settlement_seen = False
