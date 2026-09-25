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
    new: bool = False


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
    return min(loot, key=lambda o: (not o.new, o.price, o.index), default=None)


@dataclass(frozen=True)
class EventChoice:
    index: int
    text: str
    # Historical attribute name: the magnifier is a detail-preview button,
    # not an unrecorded-journal marker. It must not affect event priorities.
    journal: bool = False


def choose_event(choices: list[EventChoice], *, cores: int, fragments: int,
                 life: int, loot: int | None) -> EventChoice | None:
    """Compatibility entry for callers without persisted event observations."""
    from tasks.dimensional_exploration.event import decide_event

    decision = decide_event(choices, cores=cores, fragments=fragments, life=life, loot=loot)
    return decision.choice if decision else None


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
