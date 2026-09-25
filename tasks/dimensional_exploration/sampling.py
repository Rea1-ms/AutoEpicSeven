"""Collect whole event transitions and choose unlisted options conservatively."""

from dataclasses import asdict, dataclass
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import re
from uuid import uuid4

from PIL import Image

from tasks.dimensional_exploration.event import EventCost
from tasks.dimensional_exploration.policy import EventChoice, normalize
from tasks.dimensional_exploration.assets.assets_dimensional_exploration import OCR_EVENT_LOOT


def text_key(text):
    return sha256(normalize(text).encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class SampleOption:
    choice: EventChoice
    cost: EventCost
    tier: int
    reason: str


def inspect_option(choice):
    """Parse every displayed cost clause; leave unknown cost clauses blocked.

    A label without a cost is an experiment, not a claim that it is free. We
    preserve the raw text and result for later review instead of learning a
    permanent rule from one click or guessing an unrecognized amount.
    """
    text = normalize(choice.text).replace("％", "%")
    if len(text) < 4:
        return None
    values = {}
    residual = text
    for name, pattern in (
        ("fragments", r"(?:消耗|支付|花费)(\d+)个?次元碎片"),
        ("dice", r"(?:消耗|支付|花费)(\d+)个?次元骰子"),
        ("loot", r"(?:消耗|支付|花费)(\d+)个?战利品"),
        ("life", r"(?:消耗|支付|花费)(\d+)点?生命体征"),
        ("random_hp_percent", r"1名随机英雄生命值降低(\d+)%"),
        ("all_hp_percent", r"所有英雄生命值降低(\d+)%"),
    ):
        values[name] = sum(int(m[1]) for m in re.finditer(pattern, residual))
        residual = re.sub(pattern, "", residual)
    if max(values["random_hp_percent"], values["all_hp_percent"]) >= 100:
        return None
    if re.search(r"消耗|支付|花费|降低|减少|失去|损失|扣除|扣减|牺牲|献出|交出|损耗|死亡|阵亡|受伤|承受", residual):
        return None
    if re.search(r"[-−]\d|生命(?:值|体征)(?!恢复)|用.*(?:换|交换)", residual):
        return None
    values["battle"] = int("战斗" in text or "挑战" in text)
    cost = EventCost(**values)
    # Explore different equally inexpensive options, without escalating to HP
    # or death-allowance spending just to fill a dataset. Explicit exit options
    # remain preferable to sacrifices and combat when no cheap trial exists.
    if cost.life:
        tier, reason = 7, "消耗生命体征，保留至少1点"
    elif cost.battle:
        tier, reason = 6, "需要战斗"
    elif cost.all_hp_percent:
        tier, reason = 5, "消耗全队生命值"
    elif cost.random_hp_percent:
        tier, reason = 4, "消耗随机英雄生命值"
    elif cost.loot:
        tier, reason = 3, "消耗战利品"
    elif cost.fragments or cost.dice:
        tier, reason = 1, "消耗可负担的局内货币"
    elif re.search(r"离开|撤离|放弃|无视|不予理会", text):
        tier, reason = 2, "离开以避免较大消耗"
    else:
        tier, reason = 0, "未显示已知代价，尝试并记录结果"
    return SampleOption(choice, cost, tier, reason)


def choose_sample(choices, balances, visits=None, pending_text=None):
    visits = visits or {}
    options = []
    for choice in choices:
        option = inspect_option(choice)
        if option is None or option.cost.unavailable(**balances):
            continue
        if pending_text is not None:
            if normalize(choice.text) == normalize(pending_text):
                return option
            continue
        cost = option.cost
        amount = (cost.life, cost.all_hp_percent, cost.random_hp_percent, cost.loot, cost.dice, cost.fragments)
        count = visits.get(text_key(choice.text), 0)
        tier = 3 if option.tier == 1 and count else option.tier
        options.append((tier, amount, count, choice.index, option))
    return min(options, key=lambda item: item[:-1])[-1] if options else None


class EventSampler:
    """Persist raw evidence before clicking, with bounded outcome screenshots.

    Each choice has a receipt; retries reuse it. Only positive outcome pages
    finish it and increase counts. active.json survives process restarts, and
    neither unknown frames nor OCR-only differences establish completion.
    Files are deliberately retained for offline review, never auto-deleted.
    """

    END_STATES = {"map", "failed", "settlement", "lobby", "start_supply", "recruitment"}
    OUTCOME_STATES = END_STATES | {"reward", "loot", "prepare", "battle", "victory", "upgrade", "revive"}

    def __init__(self, root):
        self.root = Path(root)
        self.index = self._read("index.json", {"version": 1, "events": {}})
        self.active = self._read("active.json", None)
        if self.active:
            folder = (self.root / self.active["folder"]).resolve()
            if not folder.is_relative_to(self.root.resolve()):
                raise ValueError("事件采样记录路径超出采样目录。")
            if self.active["finished"] is not None:
                self._finish(self.active["finished"])
        self._candidate = None

    def _read(self, name, default):
        path = self.root / name
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default

    def _write(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

    def _save(self):
        if self.active:
            self._write(self.root / self.active["folder"] / "record.json", self.active)
        self._write(self.root / "active.json", self.active)

    def visits(self, story):
        entry = self.index["events"].get(text_key(story), {})
        return {key: value["completed"] for key, value in entry.get("choices", {}).items()}

    def pending_text(self, story):
        # A changed OCR story is not proof of advancement. Pin the selected
        # text until a positive outcome, even if its event hash drifts.
        if self.active and self.active["clicked"] and not self.active["advanced"]:
            return self.active["selected_text"]
        return None

    def stable(self, story, choices, balances):
        candidate = (normalize(story), tuple(normalize(c.text) for c in choices), tuple(balances.items()))
        same = self._candidate == candidate
        self._candidate = candidate
        return same

    def before(self, image, story, choices, balances, selected, source, reason):
        key = text_key(story)
        texts = [c.text for c in choices]
        selected_text = selected.text if selected is not None else None
        if self.active and self.active["event"] == key and self.active["selected_text"] == selected_text:
            if not self.active["advanced"]:
                return
        if self.active:
            self._finish("next_options" if self.active["advanced"] else "unconfirmed_transition")
        folder = f"{key}/{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:8]}"
        path = self.root / folder
        path.mkdir(parents=True, exist_ok=True)
        Image.fromarray(image).save(path / "before.png")
        self.active = {
            "version": 1, "folder": folder, "event": key, "story": story,
            "choices": texts, "before": balances, "selected_text": selected_text,
            "selected_index": selected.index if selected is not None else None,
            "source": source, "reason": reason, "clicked": False, "advanced": False,
            "frames": [], "finished": None,
        }
        event = self.index["events"].setdefault(key, {"story": story, "choices": {}})
        event.setdefault("samples", []).append(folder)
        self._write(self.root / "index.json", self.index)
        self._save()

    def clicked(self):
        self.active["clicked"] = True
        self._save()

    def _finish(self, state):
        self._candidate = None
        if self.active["clicked"] and self.active["advanced"]:
            event = self.index["events"].setdefault(self.active["event"], {"story": self.active["story"], "choices": {}})
            key = text_key(self.active["selected_text"])
            choice = event["choices"].setdefault(key, {"text": self.active["selected_text"], "completed": 0})
            # Writing the index and closing active.json is not one atomic
            # operation. A receipt ID makes replay after interruption idempotent.
            receipts = choice.setdefault("receipts", [])
            if self.active["folder"] not in receipts:
                receipts.append(self.active["folder"])
                choice["completed"] += 1
            self._write(self.root / "index.json", self.index)
        self.active["finished"] = state
        self._save()
        self.active = None
        self._save()

    def observe(self, state, image, *, text="", resources=None, narration=False):
        if not self.active or not self.active["clicked"]:
            return
        if state not in self.OUTCOME_STATES and not narration:
            return
        self._candidate = None
        frames = self.active["frames"]
        signature = (state, normalize(text))
        if signature not in {(f["state"], normalize(f["text"])) for f in frames} and len(frames) < 12:
            name = f"after-{len(frames)+1:02d}-{state}.png"
            Image.fromarray(image).save(self.root / self.active["folder"] / name)
            before = self.active["before"]
            delta = {k: value - before[k] for k, value in (resources or {}).items()
                     if isinstance(value, int) and isinstance(before.get(k), int)}
            frames.append({"state": state, "text": text, "image": name, "resources": resources, "delta": delta})
        self.active["advanced"] = True
        self._save()
        if state in self.END_STATES:
            self._finish(state)


def sampling_balances(vision):
    resources = vision.resources()
    if resources is None:
        return None
    values = asdict(resources)
    values.pop("max_life")
    values["loot"] = vision.number(OCR_EVENT_LOOT)
    values["dice"] = vision.event_dice()
    return values
