"""Bounded hero-list search; confirmation always uses the selected detail pane."""

from difflib import SequenceMatcher
import json
from pathlib import Path

from module.base.button import ClickButton
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
        self.values = json.loads(path.read_text(encoding="utf-8")) if path and path.exists() else {}

    def learn(self, signature):
        updates = {name: cost for name, cost, *_ in signature if name and isinstance(cost, int) and 0 < cost <= 9}
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
        return bool(preferred) and all(name in self.values and self.values[name] > budget for name in preferred)


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

    def reset_hero_search(self):
        self._hero_view = None
        self._hero_swipes = 0
        self._hero_best = None
        self._hero_returning = False
        self._hero_selected = None
        self._hero_stalls = 0
        self._hero_before_swipe = None
        self._hero_budget = None

    def skip_recruitment(self):
        if self.appear(BACK) and self.click_action(BACK):
            self._recruit_skipped = True
            return True
        return False

    def handle_hero(self, vision):
        if not self.action_ready():
            return False
        quota = vision.quota()
        if quota is None:
            return False
        budget = quota[1] - quota[0] - self._initial_reserved
        title = vision.text(OCR_HERO_TITLE)
        unrestricted = "全职业" in title
        swipe_limit = 30
        preferred = self.hero_priorities(title)
        if budget != self._hero_budget:
            self.reset_hero_search()
            self._hero_budget = budget
        if not self._initial_recruitment and (budget <= 0 or (
                not unrestricted and self.hero_costs.cannot_afford(preferred, budget))):
            return self.skip_recruitment()
        heroes = [hero for hero in vision.heroes() if hero.cost <= budget]
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
                    return self.click_action(HERO_CONFIRM)
                return False
            return self.click_action(ClickButton(matching[0].area, name="SelectExplorationHero"))

        if unrestricted and heroes:
            self._hero_selected = heroes[0].name
            return self.click_action(ClickButton(heroes[0].area, name="SelectExplorationHero"))
        if not self._initial_recruitment:
            if not unrestricted and self.hero_costs.cannot_afford(preferred, budget):
                return self.skip_recruitment()
            if not unrestricted:
                heroes = [h for h in heroes if any(same_hero(h.name, name) for name in preferred)]

        def rank(hero):
            return (next((i for i, name in enumerate(preferred) if same_hero(hero.name, name)),
                         len(preferred)), hero.cost, hero.name)

        if heroes:
            candidate = min(heroes, key=rank)
            if self._hero_best is None or rank(candidate) < self._hero_best[0]:
                self._hero_best = (rank(candidate), candidate.name)
            if rank(candidate)[0] == 0:
                self._hero_selected = candidate.name
                return self.click_action(ClickButton(candidate.area, name="SelectExplorationHero"))
        if self._hero_returning:
            matching = [h for h in heroes if same_hero(h.name, self._hero_best[1])]
            if len(matching) == 1:
                self._hero_selected = matching[0].name
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
            if self._hero_best is None:
                if not self._initial_recruitment:
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
