"""Bounded hero-list search; confirmation always uses the selected detail pane."""

from difflib import SequenceMatcher
import json
from pathlib import Path
from uuid import uuid4

from module.base.button import ClickButton
from module.logger import logger
from tasks.base.assets.assets_base_page import BACK
from tasks.dimensional_exploration.assets.assets_dimensional_exploration import (
    HERO_CONFIRM, OCR_HERO_TITLE, HERO_CONFIRM_ACTIVE, OCR_REST_HEROES, REST_HERO_CONFIRM,
)
from tasks.dimensional_exploration.policy import normalize, priorities


def same_hero(observed, expected):
    observed, expected = normalize(observed), normalize(expected)
    if observed == expected:
        return True
    # Long Chinese names have occasional one-glyph OCR errors. A short name
    # must match exactly, otherwise variants such as Ras would be conflated.
    return (min(len(observed), len(expected)) >= 5
            and abs(len(observed) - len(expected)) <= 2
            and SequenceMatcher(None, observed, expected).ratio() >= 0.84)


class HeroCosts:
    """Learn displayed costs from stable lists; never invent unseen hero costs."""

    def __init__(self, path: Path | None = None):
        self.path = path
        table = json.loads(Path(__file__).with_name("hero_costs.json").read_text(encoding="utf-8"))
        self.aliases = table["ocr_aliases"]
        self.values = dict(table["heroes"])
        if path and path.exists():
            observed = json.loads(path.read_text(encoding="utf-8"))
            self.values.update({self.aliases.get(name, name): cost for name, cost in observed.items()
                                if name and type(cost) is int and 0 < cost <= 9})

    @classmethod
    def from_runtime(cls, root=Path(".")):
        """Share learned costs without exposing them as GUI account profiles.

        Old per-profile files are archived only after their observations have
        been saved. Existing shared observations win conflicts during migration;
        a later stable game frame can still update them through learn(). Archive
        names are unique so retries never overwrite a previous original file.
        """
        folder = root / "data" / "dimensional_exploration"
        costs = cls(folder / "hero_costs_global_cn.json")
        for source in sorted((root / "config").glob("dimensional_exploration_hero_costs_*.json")):
            previous = cls(source)
            costs.learn([(name, value) for name, value in previous.values.items() if name not in costs.values])
            archive = folder / "legacy"
            archive.mkdir(parents=True, exist_ok=True)
            source.rename(archive / f"{source.stem}_{uuid4().hex}.json")
        return costs

    def learn(self, signature):
        updates = {self.aliases.get(name, name): cost for name, cost, *_ in signature
                   if name and type(cost) is int and 0 < cost <= 9}
        if all(self.values.get(name) == cost for name, cost in updates.items()):
            return
        self.values.update(updates)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.values, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temporary.replace(self.path)

    def cannot_afford(self, preferred, budget):
        # Unknown names (including OCR variants) prevent a preflight skip.
        # Stored observations only avoid a fruitless search; current UI costs
        # and quota must still authorize every actual recruitment.
        names = [self.aliases.get(name, name) for name in preferred]
        return bool(names) and all(name in self.values and self.values[name] > budget for name in names)


class RecruitmentMixin:
    CLASS_OPTIONS = {
        "骑士": "KnightPriority", "战士": "WarriorPriority", "射手": "RangerPriority",
        "魔导士": "MagePriority", "精灵师": "HealerPriority", "盗贼": "ThiefPriority",
    }

    def hero_priorities(self, title):
        keys = [value for key, value in self.CLASS_OPTIONS.items() if key in title]
        if not keys:
            keys = list(self.CLASS_OPTIONS.values())
        return tuple(name for key in keys for name in priorities(
            getattr(self.config, f"DimensionalExploration_{key}")))

    def load_recruitment(self):
        session = getattr(self.config, "DimensionalExplorationRuntime_Session", {})
        saved = session.get("recruitment", {}) if not session.get("finished") else {}
        if not isinstance(saved, dict):
            raise ValueError("次元探查本局招募记录格式不正确。")
        heroes, pending = saved.get("heroes", []), saved.get("pending")
        if (not isinstance(heroes, list) or not all(isinstance(name, str) and name for name in heroes)
                or (pending is not None and (
                    not isinstance(pending, dict) or not isinstance(pending.get("name"), str) or not pending["name"]
                    or type(pending.get("cost")) is not int or not 0 < pending["cost"] <= 9
                    or not isinstance(pending.get("quota"), list) or len(pending["quota"]) != 2
                    or not all(type(value) is int and 0 <= value <= 99 for value in pending["quota"])))):
            raise ValueError("次元探查本局招募记录格式不正确。")
        self._recruited_heroes = list(heroes)
        self._recruit_pending = pending
        self._recruit_receipt = None

    def save_recruitment(self):
        session = dict(getattr(self.config, "DimensionalExplorationRuntime_Session", {}))
        session["recruitment"] = {"heroes": list(self._recruited_heroes), "pending": self._recruit_pending}
        self.config.DimensionalExplorationRuntime_Session = session

    def clear_recruitment(self):
        self._recruited_heroes = []
        self._recruit_pending = None
        self._recruit_receipt = None
        self.reset_hero_search()
        self.save_recruitment()

    def recruited(self, name):
        return any(same_hero(name, previous) for previous in self._recruited_heroes)

    def confirm_hero(self, quota):
        if not self.click_action(HERO_CONFIRM):
            return False
        # Sending confirmation is not recruitment. Persist the intended hero
        # and original quota so a restart cannot mistake a stale picker for a
        # success or discard the pending receipt. Repeated clicks keep the same
        # receipt; only two fresh exact quota increases confirm the expense.
        pending = {"name": self._hero_selected, "cost": self._hero_selected_cost, "quota": list(quota)}
        if self._recruit_pending != pending:
            self._recruit_pending = pending
            self._recruit_receipt = None
            self.save_recruitment()
        return True

    def observe_recruitment(self, state, vision):
        pending = self._recruit_pending
        if not pending:
            return
        if state not in ("hero", "recruitment", "victory", "resume_rewards", "map", "event", "loot"):
            self._recruit_receipt = None
            return
        quota = vision.quota()
        expected = (pending["quota"][0] + pending["cost"], pending["quota"][1])
        if quota != expected:
            self._recruit_receipt = None
            return
        if self._recruit_receipt != quota:
            self._recruit_receipt = quota
            return
        if not self.recruited(pending["name"]):
            self._recruited_heroes.append(pending["name"])
        logger.attr("ExplorationRecruited", self._recruited_heroes)
        self._recruit_pending = None
        self._recruit_receipt = None
        self.reset_hero_search()
        self.save_recruitment()

    def reset_hero_search(self):
        self._hero_view = None
        self._hero_swipes = 0
        self._hero_best = None
        self._hero_fallback_best = None
        self._hero_returning = False
        self._hero_selected = None
        self._hero_selected_cost = None
        self._hero_stalls = 0
        self._hero_before_swipe = None
        self._hero_budget = None

    def skip_recruitment(self):
        if self.appear(BACK) and self.click_action(BACK):
            self._recruit_skipped = True
            return True
        return False

    def handle_hero(self, vision):
        """Select within quota and confirm only the positively matched detail.

        Pages:
            in: hero-list picker
            out: selected detail, recruitment result, or source reward/setup page
        """
        if not self.action_ready():
            return False
        quota = vision.quota()
        if quota is None:
            return False
        budget = quota[1] - quota[0] - self._initial_reserved
        title = vision.text(OCR_HERO_TITLE)
        unrestricted = "全职业" in title
        swipe_limit = 30
        targets = tuple(name for name in self.hero_priorities(title) if not self.recruited(name))
        preferred = tuple(name for name in targets if self.hero_costs.values.get(name, 0) <= budget)
        budget_fallback = not self._initial_recruitment and self.hero_costs.cannot_afford(targets, budget)
        if self._recruit_pending:
            # Never pick another hero while a previous confirmation is awaiting
            # its quota receipt. A repeated unchanged picker may retry only the
            # same detail selection, including after process restart.
            if list(quota) != self._recruit_pending["quota"]:
                return False
        if budget != self._hero_budget:
            self.reset_hero_search()
            self._hero_budget = budget
        if self._recruit_pending:
            self._hero_selected = self._recruit_pending["name"]
            self._hero_selected_cost = self._recruit_pending["cost"]
        if not self._initial_recruitment and (budget <= 0 or (
                not unrestricted and not targets and not self._recruit_pending)):
            return self.skip_recruitment()
        # Selection recolors the row, which can hide its cost-icon template.
        # The detail pane and enabled confirm button are authoritative here.
        # Keep the last stable row cost only until quota changes or we leave
        # this picker; reset_hero_search() invalidates both name and cost.
        if (self._hero_selected is not None and self._hero_selected_cost is not None
                and 0 < self._hero_selected_cost <= budget
                and same_hero(vision.selected_hero(), self._hero_selected)):
            if vision.bright_text(HERO_CONFIRM_ACTIVE):
                return self.confirm_hero(quota)
            return False
        def best_visible(hero):
            if hero.cost > budget or self.recruited(hero.name):
                return False
            # Keep an already selected/pending hero visible in the scan until
            # its quota receipt is confirmed. Stopping at a different listed
            # hero can hide the pinned row and cause a false takeover on retry.
            if self._hero_selected is not None:
                return same_hero(hero.name, self._hero_selected)
            if self._hero_returning and self._hero_best is not None:
                return same_hero(hero.name, self._hero_best[1])
            if budget_fallback:
                return True
            if unrestricted:
                return any(same_hero(hero.name, name) for name in preferred)
            return bool(preferred) and same_hero(hero.name, preferred[0])

        heroes = [hero for hero in vision.heroes(stop_when=best_visible)
                  if hero.cost <= budget and not self.recruited(hero.name)]
        signature = tuple(vision.hero_signature)
        if signature != self._hero_view:
            self._hero_view = signature
            return False
        self.hero_costs.learn(signature)
        if self._hero_selected is not None:
            matching = [h for h in heroes if same_hero(h.name, self._hero_selected)]
            if len(matching) != 1:
                self.require_human("已选英雄不在当前可招募名单中，请检查招募额度或名单。")
            if same_hero(vision.selected_hero(), self._hero_selected):
                if vision.bright_text(HERO_CONFIRM_ACTIVE):
                    self._hero_selected_cost = matching[0].cost
                    return self.confirm_hero(quota)
                return False
            return self.click_action(ClickButton(matching[0].area, name="SelectExplorationHero"))

        # Keep unlisted affordable names even while looking for a priority.
        # A later stable page may teach the last unknown priority's expensive
        # cost; its only affordable fallback can be on an earlier page. Store
        # no click coordinates here: returning must locate it in a fresh frame.
        for hero in heroes:
            candidate = (hero.cost, hero.name)
            if self._hero_fallback_best is None or candidate < self._hero_fallback_best:
                self._hero_fallback_best = candidate
        # Recompute after learning this stable frame. Filtering before learn()
        # loses every newly known expensive target, then mistakes an empty
        # preferred list for a completed roster and skips a usable ticket.
        preferred = tuple(name for name in targets if self.hero_costs.values.get(name, 0) <= budget)
        budget_fallback = not self._initial_recruitment and self.hero_costs.cannot_afford(targets, budget)
        if budget_fallback:
            if heroes:
                chosen = heroes[0]
                self._hero_selected, self._hero_selected_cost = chosen.name, chosen.cost
                logger.info(f"招募剩余额度{budget}，名单英雄均不可负担，改选{chosen.name}（{chosen.cost}点）。")
                return self.click_action(ClickButton(chosen.area, name="SelectExplorationHero"))
            if self._hero_fallback_best is not None:
                cost, name = self._hero_fallback_best
                self._hero_best = ((len(preferred), cost, name), name)
                self._hero_returning = True

        if unrestricted:
            # Full-class tickets use all configured class lists, in encounter
            # order rather than searching for the globally highest priority.
            # When a listed target may still be affordable, unlisted rows are
            # fallback evidence until the boundary or bounded search limit.
            listed = [h for h in heroes if any(same_hero(h.name, name) for name in preferred)]
            if listed:
                self._hero_selected = listed[0].name
                self._hero_selected_cost = listed[0].cost
                return self.click_action(ClickButton(listed[0].area, name="SelectExplorationHero"))
        affordable = heroes
        if not self._initial_recruitment:
            if not unrestricted and not budget_fallback and not self._hero_returning:
                heroes = [h for h in heroes if any(same_hero(h.name, name) for name in preferred)]

        def rank(hero):
            return (next((i for i, name in enumerate(preferred) if same_hero(hero.name, name)),
                         len(preferred)), hero.cost, hero.name)

        if heroes and not self._hero_returning:
            candidate = min(heroes, key=rank)
            if self._hero_best is None or rank(candidate) < self._hero_best[0]:
                self._hero_best = (rank(candidate), candidate.name)
            if not unrestricted and rank(candidate)[0] == 0:
                self._hero_selected = candidate.name
                self._hero_selected_cost = candidate.cost
                return self.click_action(ClickButton(candidate.area, name="SelectExplorationHero"))
        if self._hero_returning:
            matching = [h for h in heroes if same_hero(h.name, self._hero_best[1])]
            if len(matching) == 1:
                self._hero_selected = matching[0].name
                self._hero_selected_cost = matching[0].cost
                return self.click_action(ClickButton(matching[0].area, name="SelectExplorationHero"))
            if self._hero_swipes >= swipe_limit * 2 + 4:
                self.require_human("回查英雄名单时未找到已识别的候选英雄。")
            return self._swipe_heroes(signature, reverse=True)
        # Repeated stable content after a gesture is a boundary candidate, not
        # proof of a successful swipe. Allow two retries before reversing, and
        # never increment the search budget on a mere screenshot observation.
        if signature == self._hero_before_swipe:
            self._hero_stalls += 1
        else:
            self._hero_stalls = 0
        if self._hero_swipes >= swipe_limit or self._hero_stalls >= 2:
            if unrestricted and heroes:
                self._hero_selected = heroes[0].name
                self._hero_selected_cost = heroes[0].cost
                return self.click_action(ClickButton(heroes[0].area, name="SelectExplorationHero"))
            if self._hero_best is None:
                if not self._initial_recruitment:
                    # Unknown target prices cannot justify an early fallback.
                    # Once the bounded search found no eligible listed hero,
                    # however, preserve a usable ticket rather than discarding
                    # it just because the affordable row was outside the list.
                    if affordable:
                        chosen = affordable[0]
                        self._hero_selected, self._hero_selected_cost = chosen.name, chosen.cost
                        logger.info(f"招募名单未找到可负担英雄，改选{chosen.name}（{chosen.cost}点）。")
                        return self.click_action(ClickButton(chosen.area, name="SelectExplorationHero"))
                    if self._hero_fallback_best is not None:
                        cost, name = self._hero_fallback_best
                        self._hero_best = ((len(preferred), cost, name), name)
                        self._hero_returning = True
                        return False
                    return self.skip_recruitment()
                self.require_human("未找到额度内可招募的英雄，请检查优先名单和队伍。")
            self._hero_returning = True
            return False
        return self._swipe_heroes(signature, reverse=False)

    def _swipe_heroes(self, signature, reverse):
        if not self.action_ready():
            return False
        start, end = ((470, 355), (798, 355)) if reverse else ((1000, 355), (672, 355))
        self.device.swipe(start, end, duration=(0.3, 0.4), name="ExplorationHeroList")
        self.interval_reset("ExplorationAction", interval=2)
        self._hero_before_swipe = signature
        self._hero_view = None
        self._hero_swipes += 1
        return True

    def handle_rest_hero(self, vision):
        preferred = self.hero_priorities("")
        tokens = vision.tokens(OCR_REST_HEROES)
        names = [t for t in tokens if len(normalize(t.ocr_text)) >= 2
                 and ((t.box[1] - 90) % 105) < 43]
        if not names:
            return False
        selected = vision.selected_hero()
        if vision.state() == "revive" and selected and vision.bright_text(REST_HERO_CONFIRM):
            return self.click_action(ClickButton(REST_HERO_CONFIRM.area, name="RestHeroConfirm"))
        chosen = min(names, key=lambda t: next(
            (i for i, name in enumerate(preferred) if same_hero(t.ocr_text, name)), len(preferred)))
        if same_hero(selected, chosen.ocr_text) and vision.bright_text(REST_HERO_CONFIRM):
            return self.click_action(ClickButton(REST_HERO_CONFIRM.area, name="RestHeroConfirm"))
        return self.click_action(ClickButton(chosen.box, name="RestHeroSelect"))
