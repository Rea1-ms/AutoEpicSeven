"""Bounded hero-list search; confirmation always uses the selected detail pane."""

from difflib import SequenceMatcher

from module.base.button import ClickButton
from tasks.dimensional_exploration.assets.assets_dimensional_exploration import HERO_CONFIRM
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

    def handle_hero(self, vision):
        if not self.action_ready():
            return False
        quota = vision.quota()
        if quota is None:
            return False
        budget = quota[1] - quota[0] - self._initial_reserved
        title = vision.text((69, 17, 345, 51))
        swipe_limit = 70 if "全职业" in title else 30
        preferred = self.hero_priorities(title)
        heroes = [hero for hero in vision.heroes() if hero.cost <= budget]
        signature = tuple(vision.hero_signature)
        if signature != self._hero_view:
            self._hero_view = signature
            return False
        if self._hero_selected is not None:
            matching = [h for h in heroes if same_hero(h.name, self._hero_selected)]
            if len(matching) != 1:
                self.require_human("已选英雄不在当前可招募名单中，请检查招募额度或名单。")
            if same_hero(vision.selected_hero(), self._hero_selected):
                if vision.bright_text((1040, 646, 1136, 677)):
                    return self.click_action(HERO_CONFIRM)
                return False
            return self.click_action(ClickButton(matching[0].area, name="SelectExplorationHero"))

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
        tokens = vision.tokens((390, 86, 965, 589), name="RestHeroNames")
        names = [t for t in tokens if len(normalize(t.ocr_text)) >= 2
                 and ((t.box[1] - 90) % 105) < 43]
        if not names:
            return False
        selected = vision.selected_hero()
        if vision.state() == "revive" and selected and vision.bright_text((1030, 648, 1140, 677)):
            return self.click_action(ClickButton((997, 636, 1190, 682), name="RestHeroConfirm"))
        chosen = min(names, key=lambda t: next(
            (i for i, name in enumerate(preferred) if same_hero(t.ocr_text, name)), len(preferred)))
        if same_hero(selected, chosen.ocr_text) and vision.bright_text((1030, 648, 1140, 677)):
            return self.click_action(ClickButton((997, 636, 1190, 682), name="RestHeroConfirm"))
        return self.click_action(ClickButton(chosen.box, name="RestHeroSelect"))
