"""Recognition of the supplied 1280x720 overseas Chinese exploration UI."""

from dataclasses import dataclass

import cv2
import numpy as np

from module.base.button import ClickButton
from module.base.utils import area_center, area_offset, area_limit, point_in_area
from module.logger import logger
from module.ocr.ocr import Ocr
from tasks.dimensional_exploration.assets.assets_dimensional_exploration import (
    ABANDON_CHECK, BUY_CHECK, CHAPTER_CHECK, CORE_ICON, EVENT_CHECK, EVENT_JOURNAL, EVENT_OPTION, EXPLORE_ENTER,
    FAILED_CHECK, FRAGMENT_ICON, HERO_COST_ICON, HERO_PICKER_CHECK, LEAVE_CONFIRM_CHECK,
    LOBBY_CHECK, LOOT_CHECK, MAP_CHECK, NODE_AVAILABLE, NODE_BATTLE, NODE_BOSS,
    NODE_ELITE, NODE_ENTER, NODE_EVENT, NODE_REST, NODE_SHOP, NODE_SUPPLY,
    PREPARE_CHECK, RECRUITMENT_CHECK, REST_CHECK, REVIVE_CHECK, REWARD_CLOSE,
    ROOM_SUPPLY_CHECK, SETTLEMENT_CHECK, SHOP_CHECK, SHOP_NEW, SUPPLY_CHECK, TITLE_CHECK,
    UPGRADE_CHECK, VICTORY_CHECK,
    OCR_LIFE, OCR_QUOTA, OCR_CORE, OCR_FRAGMENT, OCR_DICE, OCR_EVENT_STORY,
    OCR_EVENT_REWARD, OCR_EVENT_OPTION, EVENT_OPTION_CLICK, EVENT_DETAIL_AREA,
    OCR_HERO_NAME, OCR_HERO_COST, HERO_COST_ACTIVE, HERO_ROW_CLICK, OCR_SELECTED_HERO,
    OCR_SHOP_NAME, OCR_SHOP_PRICE, OCR_SHOP_NEW, RESUME_REWARDS_CHECK,
    NODE_TYPE_AREA, NODE_CLICK, OCR_HEROES,
)
from tasks.dimensional_exploration.policy import EventChoice, Offer, normalize, parse_counter, parse_number
from tasks.dungeon.assets.assets_dungeon_state import AUTO_COMBAT_EXIST


@dataclass(frozen=True)
class Node:
    kind: str
    area: tuple[int, int, int, int]
    score: float


@dataclass(frozen=True)
class Resources:
    cores: int
    fragments: int
    life: int
    max_life: int


@dataclass(frozen=True)
class Hero:
    name: str
    cost: int
    area: tuple[int, int, int, int]


def match_in(image, asset, area, threshold=0.85):
    """Locate without mutating global wrapper search or cached offsets."""
    x1, y1, x2, y2 = map(int, area)
    sample = cv2.cvtColor(image[y1:y2, x1:x2], cv2.COLOR_RGB2GRAY)
    best = None
    for button in asset.iter_buttons():
        template = button.image_luma
        h, w = template.shape
        if sample.shape[0] < h or sample.shape[1] < w:
            continue
        scores = cv2.matchTemplate(sample, template, cv2.TM_CCOEFF_NORMED)
        _, score, _, point = cv2.minMaxLoc(scores)
        if score >= threshold and (best is None or score > best[0]):
            x, y = point[0] + x1, point[1] + y1
            best = (score, (x, y, x + w, y + h))
    return best


def multi_match(image, asset, area, threshold=0.87, distance=24):
    x1, y1, x2, y2 = area
    sample = cv2.cvtColor(image[y1:y2, x1:x2], cv2.COLOR_RGB2GRAY)
    candidates = []
    for button in asset.iter_buttons():
        template = button.image_luma
        h, w = template.shape
        scores = cv2.matchTemplate(sample, template, cv2.TM_CCOEFF_NORMED)
        while True:
            _, score, _, (x, y) = cv2.minMaxLoc(scores)
            if score < threshold:
                break
            candidates.append((score, (x + x1, y + y1, x + x1 + w, y + y1 + h)))
            scores[max(0, y - distance):y + distance + 1,
                   max(0, x - distance):x + distance + 1] = 0
    results = []
    for _, box in sorted(candidates, reverse=True):
        if not any(abs(box[0] - old[0]) < distance and abs(box[1] - old[1]) < distance for old in results):
            results.append(box)
    return sorted(results, key=lambda box: (box[0], box[1]))


def relative_area(region, anchor, observed):
    """Move a source-defined box with its detected anchor, without global offsets."""
    reference = next(anchor.iter_buttons()).area
    offset = (observed[0] - reference[0], observed[1] - reference[1])
    return area_limit(area_offset(region.area, offset), (0, 0, 1280, 720))


class ExplorationVision:
    STATES = (
        ("settlement", SETTLEMENT_CHECK), ("reward", REWARD_CLOSE), ("abandon", ABANDON_CHECK),
        ("buy", BUY_CHECK), ("leave_confirm", LEAVE_CONFIRM_CHECK),
        ("loot", LOOT_CHECK), ("resume_rewards", RESUME_REWARDS_CHECK), ("failed", FAILED_CHECK),
        ("upgrade", UPGRADE_CHECK), ("revive", REVIVE_CHECK),
        ("hero", HERO_PICKER_CHECK), ("prepare", PREPARE_CHECK),
        ("victory", VICTORY_CHECK), ("rest", REST_CHECK),
        ("shop", SHOP_CHECK), ("supply_room", ROOM_SUPPLY_CHECK),
        ("event", EVENT_CHECK), ("recruitment", RECRUITMENT_CHECK),
        ("start_supply", SUPPLY_CHECK), ("title", TITLE_CHECK), ("chapter", CHAPTER_CHECK),
        ("lobby", LOBBY_CHECK), ("preview", NODE_ENTER), ("map", MAP_CHECK),
        ("battle", AUTO_COMBAT_EXIST),
    )
    NODE_ASSETS = {
        "event": NODE_EVENT, "shop": NODE_SHOP, "supply": NODE_SUPPLY,
        "battle": NODE_BATTLE, "elite": NODE_ELITE, "rest": NODE_REST, "boss": NODE_BOSS,
    }

    def __init__(self, image):
        self.image = image

    def state(self):
        for state, asset in self.STATES:
            if asset.match_template(self.image, similarity=0.87):
                # The start-party page's Enter button exists in disabled form
                # too; the separate Back button keeps it distinct from title.
                if state == "recruitment" and not EXPLORE_ENTER.match_template(self.image):
                    continue
                return state
        return "unknown"

    def text(self, area, name="ExplorationText"):
        region = area if hasattr(area, "area") else ClickButton(area, name=name)
        return Ocr(region, lang="cn", name=getattr(region, "name", name)).ocr_single_line(self.image)

    def tokens(self, area, name="ExplorationOptions"):
        region = area if hasattr(area, "area") else ClickButton(area, name=name)
        return Ocr(region, lang="cn", name=getattr(region, "name", name)).detect_and_ocr(self.image)

    def number(self, area, maximum=99999):
        return parse_number(self.text(area), maximum)

    def resources(self):
        core = match_in(self.image, CORE_ICON, CORE_ICON.search)
        fragment = match_in(self.image, FRAGMENT_ICON, FRAGMENT_ICON.search)
        life = parse_counter(self.text(OCR_LIFE), maximum=9)
        if core is None or fragment is None or life is None:
            return None
        core_area = relative_area(OCR_CORE, CORE_ICON, core[1])
        cores = self.number((core_area[0], core_area[1], min(core_area[2], fragment[1][0]), core_area[3]))
        fragment_area = OCR_FRAGMENT.area
        fragments = self.number((fragment[1][2], fragment_area[1], fragment_area[2], fragment_area[3]))
        if cores is None or fragments is None:
            return None
        return Resources(cores, fragments, *life)

    def quota(self):
        return parse_counter(self.text(OCR_QUOTA), maximum=99)

    def nodes(self):
        nodes = []
        for x1, y1, x2, y2 in multi_match(self.image, NODE_AVAILABLE, NODE_AVAILABLE.search):
            rectangle = (x1, y1, x2, y2)
            area = relative_area(NODE_TYPE_AREA, NODE_AVAILABLE, rectangle)
            candidates = []
            for kind, asset in self.NODE_ASSETS.items():
                found = match_in(self.image, asset, area, threshold=0.58)
                if found:
                    candidates.append((found[0], kind))
            candidates.sort(reverse=True)
            if candidates and (len(candidates) == 1 or candidates[0][0] - candidates[1][0] >= 0.045):
                score, kind = candidates[0]
                nodes.append(Node(kind, relative_area(NODE_CLICK, NODE_AVAILABLE, rectangle), score))
            else:
                # Do not rank a partial frontier. The unreadable arrow might
                # be the higher-priority event/shop; skipping it would silently
                # change the requested route. Re-read the complete next frame.
                return []
        return nodes

    def offers(self):
        offers = []
        # The uncollected marker is an orange N icon, not the word NEW.
        # Its left edge overhangs the card, so match the full shop first and
        # bind each marker's center to a card. Cropping it to the old OCR
        # region truncates the icon; OCR of that region only reads the name.
        badges = [area_center(box) for box in multi_match(self.image, SHOP_NEW, SHOP_NEW.search)]
        regions = zip(OCR_SHOP_NAME.iter_buttons(), OCR_SHOP_PRICE.iter_buttons(), OCR_SHOP_NEW.iter_buttons())
        for index, (name_area, price_area, badge_area) in enumerate(regions):
            name = self.text(name_area)
            price = self.text(price_area)
            badge = any(point_in_area(center, badge_area.area, threshold=0) for center in badges)
            offers.append(Offer(index, name, parse_number(price), "购买完毕" in normalize(price), badge))
        logger.attr("ExplorationShopNew", [(o.index + 1, o.name) for o in offers if o.new])
        return offers

    def event_choices(self):
        rectangles = multi_match(self.image, EVENT_OPTION, EVENT_OPTION.search, threshold=0.78, distance=60)
        result = []
        for index, rectangle in enumerate(rectangles):
            area = relative_area(OCR_EVENT_OPTION, EVENT_OPTION, rectangle)
            text = "".join(token.ocr_text for token in self.tokens(area))
            journal = match_in(self.image, EVENT_JOURNAL,
                               relative_area(EVENT_DETAIL_AREA, EVENT_OPTION, rectangle), threshold=0.8)
            # Keep clicks below the magnifier. Opening a detail preview is not
            # the same action as selecting the event branch or gaining its loot.
            click_area = relative_area(EVENT_OPTION_CLICK, EVENT_OPTION, rectangle)
            result.append((EventChoice(index, text, journal is not None), click_area))
        return result

    def event_story(self):
        return "".join(t.ocr_text for t in self.tokens(OCR_EVENT_STORY))

    def event_reward(self):
        return self.text(OCR_EVENT_REWARD)

    def event_dice(self):
        core = match_in(self.image, CORE_ICON, CORE_ICON.search)
        if core is None:
            return None
        # The wider crop also contains the die's printed number, which can be
        # read as part of the balance (e.g. 2 -> 82). Unknown/empty OCR remains
        # None, not a guessed zero, so a dice-cost branch cannot spend it.
        return self.number(relative_area(OCR_DICE, CORE_ICON, core[1]))

    def heroes(self):
        """Read complete columns only, including cost and disabled-color state."""
        heroes = []
        self.hero_signature = []
        for x, y, right, bottom in multi_match(self.image, HERO_COST_ICON, HERO_COST_ICON.search,
                                               threshold=0.8, distance=35):
            rectangle = (x, y, right, bottom)
            area = relative_area(OCR_HERO_NAME, HERO_COST_ICON, rectangle)
            cost_area = relative_area(OCR_HERO_COST, HERO_COST_ICON, rectangle)
            reference = next(HERO_COST_ICON.iter_buttons()).area
            left, top = OCR_HEROES.area[0] + x - reference[0], area[1]
            if left < OCR_HEROES.area[0] or cost_area[2] >= self.image.shape[1]:
                continue
            tokens = self.tokens(area, name="HeroName")
            names = [t.ocr_text for t in tokens if len(t.ocr_text) >= 2]
            name = normalize(names[0]) if names else ""
            cost = self.number(cost_area, maximum=9)
            # Include unaffordable rows in the scroll fingerprint. Two pages
            # with no affordable heroes are not necessarily the same page.
            self.hero_signature.append((name, cost, left, top))
            x1, y1, x2, y2 = relative_area(HERO_COST_ACTIVE, HERO_COST_ICON, rectangle)
            pixels = self.image[y1:y2, x1:x2]
            bright = np.count_nonzero(np.min(pixels, axis=2) > 175)
            if cost and name and bright >= 10:
                heroes.append(Hero(name, cost, relative_area(HERO_ROW_CLICK, HERO_COST_ICON, rectangle)))
        return heroes

    def selected_hero(self):
        return normalize(self.text(OCR_SELECTED_HERO))

    def bright_text(self, area, minimum=18):
        area = area.area if hasattr(area, "area") else area
        x1, y1, x2, y2 = area
        return np.count_nonzero(np.min(self.image[y1:y2, x1:x2], axis=2) > 190) >= minimum

    def gold_border(self, area):
        area = area.area if hasattr(area, "area") else area
        x1, y1, x2, y2 = area
        pixels = self.image[y1:y2, x1:x2].astype(int)
        mask = ((pixels[:, :, 0] > 185) & (pixels[:, :, 1] > 160)
                & (pixels[:, :, 1] >= pixels[:, :, 2] - 12))
        return np.count_nonzero(mask) >= 40
