"""Ordered per-hero rank goals and a bounded search of the owned picker."""

from dataclasses import dataclass
import re

from module.base.button import ClickButton
from module.logger import logger
from tasks.base.assets.assets_base_page import BACK
from tasks.dimensional_exploration.assets.assets_dimensional_exploration import (
    REST_HERO_CONFIRM, ROSTER_RANK_ICON,
)
from tasks.dimensional_exploration.policy import normalize
from tasks.dimensional_exploration.heroes.recruitment import same_hero


MAX_RANK = 5


@dataclass(frozen=True)
class UpgradeTarget:
    name: str
    rank: int


@dataclass(frozen=True)
class RankedHero:
    name: str
    rank: int
    area: tuple[int, int, int, int]


def parse_upgrade_priority(text: str | None, preferred: tuple[str, ...]) -> tuple[UpgradeTarget, ...]:
    # ConfigUpdater stores blank text inputs as None, including the generated
    # default. This is the same deliberate list-only mode as a GUI empty string;
    # other non-text values remain invalid instead of being silently discarded.
    if text is None:
        return ()
    if not isinstance(text, str):
        raise ValueError("Upgrade priority must use hero name=target rank.")
    allowed = set(preferred)
    result = []
    seen = set()
    for part in re.split(r"[>＞,，;；\n]", text):
        if not part.strip():
            continue
        match = re.fullmatch(r"\s*([^=＝:：]+?)\s*[=＝:：]\s*([1-5])\s*", part)
        if not match:
            raise ValueError(f"Invalid upgrade priority: {part.strip()}; use hero name=target rank, with rank from 1 to 5.")
        name = normalize(match[1])
        if name not in allowed:
            raise ValueError(f"Upgrade priority hero {name} is not in the hero priority roster.")
        if name in seen:
            raise ValueError(f"Duplicate upgrade priority hero: {name}.")
        seen.add(name)
        result.append(UpgradeTarget(name, int(match[2])))
    return tuple(result)


def choose_upgrade(heroes: tuple[RankedHero, ...], targets: tuple[UpgradeTarget, ...],
                   preferred: tuple[str, ...]) -> RankedHero | None:
    # Goals define the first pass, not permanent caps. After every available
    # target reaches its goal, fill recruited-priority heroes to rank 5 in
    # their configured order, then choose an unlisted hero. The caller must
    # establish the complete roster before absence can authorize a fallback.
    for target in targets:
        hero = next((h for h in heroes if same_hero(h.name, target.name)), None)
        if hero is not None and hero.rank < target.rank:
            return hero
    for name in preferred:
        hero = next((h for h in heroes if same_hero(h.name, name) and h.rank < MAX_RANK), None)
        if hero is not None:
            return hero
    return next((h for h in heroes if h.rank < MAX_RANK), None)


class UpgradeMixin:
    def reset_upgrade_search(self):
        self._upgrade_count = None
        self._upgrade_inventory = []
        self._upgrade_view = None
        self._upgrade_before_view = None
        self._upgrade_stalls = 0
        self._upgrade_swipes = 0
        self._upgrade_choice = None
        self._upgrade_pending = None
        self._upgrade_exhausted = False

    def observe_upgrade(self, state):
        # Keep the picker and its source rest room together, including a
        # network interruption. Positive room/run exits invalidate all cached
        # ranks and coordinates. No click itself advances a hero's rank.
        if state not in ("upgrade", "rest", "roster", "unknown"):
            self.reset_upgrade_search()

    def validate_upgrade_priority(self):
        try:
            return parse_upgrade_priority(
                getattr(self.config, "DimensionalExploration_UpgradePriority", ""), self.hero_priorities(""))
        except ValueError as error:
            from module.exception import RequestHumanTakeover
            logger.error(str(error))
            # Preflight happens before any screenshot or click; do not use
            # require_human(), which would try saving a device screenshot.
            raise RequestHumanTakeover(str(error)) from error

    def _swipe_upgrade(self, signature, reverse=False):
        if not self.action_ready():
            return False
        if self._upgrade_swipes >= 30:
            self.require_human("Upgrade scan limit reached without a complete roster or the target hero.")
        x1, y1, x2, y2 = ROSTER_RANK_ICON.search
        width, middle = x2 - x1, (y1 + y2) // 2
        start, end = (int(x1 + width * 0.75), middle), (int(x1 + width * 0.4), middle)
        if reverse:
            start, end = end, start
        self.device.swipe(start, end, duration=(0.3, 0.4), name="ExplorationUpgradeList")
        self.interval_reset("ExplorationAction", interval=2)
        self._upgrade_before_view = signature
        self._upgrade_view = None
        self._upgrade_swipes += 1
        return True

    def handle_upgrade(self, vision):
        """Choose only after the complete picker establishes current ranks.

        Pages:
            in: hero upgrade picker
            out: confirmed upgrade result, or the source rest room if all maxed
        """
        targets = self.validate_upgrade_priority()
        roster = vision.upgrade_roster()
        if roster is None:
            self._upgrade_view = None
            return False
        heroes, count = roster
        if count != self._upgrade_count:
            self.reset_upgrade_search()
            self._upgrade_count = count
        signature = tuple((h.name, h.rank, h.area) for h in heroes)
        complete_view = len(heroes) == count
        if not complete_view and self._upgrade_view != signature:
            self._upgrade_view = signature
            return False
        for hero in heroes:
            known = next((i for i, h in enumerate(self._upgrade_inventory) if same_hero(h.name, hero.name)), None)
            if known is None:
                self._upgrade_inventory.append(hero)
            else:
                self._upgrade_inventory[known] = hero
        if len(self._upgrade_inventory) > count:
            self.require_human("Upgrade roster names do not match the total hero count; no hero has been selected.")
        if self._upgrade_before_view is not None:
            self._upgrade_stalls = self._upgrade_stalls + 1 if signature == self._upgrade_before_view else 0
            self._upgrade_before_view = None
        if self._upgrade_pending:
            name, rank = self._upgrade_pending
            hero = next((h for h in heroes if same_hero(h.name, name)), None)
            # An updated rank is evidence that this one upgrade was applied.
            # Wait for the positive result/room exit rather than consuming a
            # second upgrade on a transient, already updated picker frame.
            if hero is None or hero.rank != rank:
                return False
            self._upgrade_choice = name
        elif len(self._upgrade_inventory) != count:
            if self._upgrade_stalls >= 2:
                self.require_human("Upgrade roster boundary reached with missing heroes; priority hero absence cannot be confirmed.")
            return self._swipe_upgrade(signature)
        elif self._upgrade_choice is None:
            chosen = choose_upgrade(tuple(self._upgrade_inventory), targets, self.hero_priorities(""))
            if chosen is None:
                if self.appear(BACK) and self.click_action(BACK):
                    self._upgrade_exhausted = True
                    return True
                return False
            self._upgrade_choice = chosen.name
            logger.attr("ExplorationUpgrade", f"{chosen.name}: current rank {chosen.rank}")
        chosen = next((h for h in heroes if same_hero(h.name, self._upgrade_choice)), None)
        if chosen is None:
            if self._upgrade_stalls >= 2:
                self.require_human("Upgrade target could not be verified during the return scan; no other hero has been clicked.")
            return self._swipe_upgrade(signature, reverse=True)
        if same_hero(vision.selected_hero(), chosen.name) and vision.bright_text(REST_HERO_CONFIRM):
            if self.click_action(ClickButton(REST_HERO_CONFIRM.area, name="RestHeroConfirm")):
                self._upgrade_pending = (chosen.name, chosen.rank)
                return True
            return False
        return self.click_action(ClickButton(chosen.area, name="RestHeroSelect"))
