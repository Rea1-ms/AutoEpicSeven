"""Recognition of the supplied 1280x720 overseas Chinese exploration UI."""

from dataclasses import dataclass

import cv2
import numpy as np

from module.base.button import ClickButton
from module.ocr.ocr import Ocr
from tasks.dimensional_exploration.assets.assets_dimensional_exploration import (
    ABANDON_CHECK, BUY_CHECK, CHAPTER_CHECK, CORE_ICON, EVENT_CHECK, EVENT_JOURNAL, EVENT_OPTION, EXPLORE_ENTER,
    FAILED_CHECK, FRAGMENT_ICON, HERO_COST_ICON, HERO_PICKER_CHECK, LEAVE_CONFIRM_CHECK,
    LOBBY_CHECK, LOOT_CHECK, MAP_CHECK, NODE_AVAILABLE, NODE_BATTLE, NODE_BOSS,
    NODE_ELITE, NODE_ENTER, NODE_EVENT, NODE_REST, NODE_SHOP, NODE_SUPPLY,
    PREPARE_CHECK, RECRUITMENT_CHECK, REST_CHECK, REVIVE_CHECK, REWARD_CLOSE,
    ROOM_SUPPLY_CHECK, SETTLEMENT_CHECK, SHOP_CHECK, SUPPLY_CHECK, TITLE_CHECK,
    UPGRADE_CHECK, VICTORY_CHECK,
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


class ExplorationVision:
    STATES = (
        ("settlement", SETTLEMENT_CHECK), ("reward", REWARD_CLOSE), ("abandon", ABANDON_CHECK),
        ("buy", BUY_CHECK), ("leave_confirm", LEAVE_CONFIRM_CHECK),
        ("loot", LOOT_CHECK), ("failed", FAILED_CHECK),
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
        return Ocr(ClickButton(area, name=name), lang="cn").ocr_single_line(self.image)

    def tokens(self, area, name="ExplorationOptions"):
        return Ocr(ClickButton(area, name=name), lang="cn").detect_and_ocr(self.image)

    def number(self, area, maximum=99999):
        return parse_number(self.text(area), maximum)

    def resources(self):
        core = match_in(self.image, CORE_ICON, CORE_ICON.search)
        fragment = match_in(self.image, FRAGMENT_ICON, FRAGMENT_ICON.search)
        life = parse_counter(self.text((1043, 17, 1092, 51)), maximum=9)
        if core is None or fragment is None or life is None:
            return None
        cores = self.number((core[1][2], 17, fragment[1][0] - 2, 51))
        fragments = self.number((fragment[1][2], 17, 974, 51))
        if cores is None or fragments is None:
            return None
        return Resources(cores, fragments, *life)

    def quota(self):
        return parse_counter(self.text((1160, 17, 1227, 51)), maximum=99)

    def nodes(self):
        nodes = []
        for x1, y1, x2, y2 in multi_match(self.image, NODE_AVAILABLE, NODE_AVAILABLE.search):
            x = (x1 + x2) // 2
            area = (max(0, x - 44), y2, min(1280, x + 44), min(600, y2 + 91))
            candidates = []
            for kind, asset in self.NODE_ASSETS.items():
                found = match_in(self.image, asset, area, threshold=0.58)
                if found:
                    candidates.append((found[0], kind))
            candidates.sort(reverse=True)
            if candidates and (len(candidates) == 1 or candidates[0][0] - candidates[1][0] >= 0.045):
                score, kind = candidates[0]
                nodes.append(Node(kind, (x - 18, y2 + 22, x + 18, y2 + 52), score))
            else:
                # Do not rank a partial frontier. The unreadable arrow might
                # be the higher-priority event/shop; skipping it would silently
                # change the requested route. Re-read the complete next frame.
                return []
        return nodes

    def offers(self):
        offers = []
        for index in range(8):
            col, row = index % 4, index // 4
            x, y = 394 + col * 191, 93 + row * 251
            name = self.text((x + 8, y + 14, x + 173, y + 55))
            price = self.text((x + 40, y + 193, x + 163, y + 232))
            offers.append(Offer(index, name, parse_number(price), "购买完毕" in normalize(price)))
        return offers

    def event_choices(self):
        rectangles = multi_match(self.image, EVENT_OPTION, EVENT_OPTION.search, threshold=0.78, distance=60)
        result = []
        for index, rectangle in enumerate(rectangles):
            x, y = rectangle[:2]
            area = (max(0, x), 554, min(1280, x + 366), 689)
            text = "".join(token.ocr_text for token in self.tokens(area))
            journal = match_in(self.image, EVENT_JOURNAL, (x, 554, x + 45, 602), threshold=0.8)
            result.append((EventChoice(index, text, journal is not None), area))
        return result

    def heroes(self):
        """Read complete columns only, including cost and disabled-color state."""
        heroes = []
        self.hero_signature = []
        for x, y, right, bottom in multi_match(self.image, HERO_COST_ICON, HERO_COST_ICON.search,
                                               threshold=0.8, distance=35):
            left, top = x - 231, y - 46
            if left < 342 or right + 36 > 1280:
                continue
            tokens = self.tokens((left + 43, top, right + 20, top + 38), name="HeroName")
            names = [t.ocr_text for t in tokens if len(t.ocr_text) >= 2]
            name = normalize(names[0]) if names else ""
            cost = self.number((right - 1, y - 2, right + 36, bottom + 1), maximum=9)
            # Include unaffordable rows in the scroll fingerprint. Two pages
            # with no affordable heroes are not necessarily the same page.
            self.hero_signature.append((name, cost, left, top))
            pixels = self.image[y:bottom, right:right + 28]
            bright = np.count_nonzero(np.min(pixels, axis=2) > 175)
            if cost and name and bright >= 10:
                heroes.append(Hero(name, cost, (left + 55, top + 6, x - 30, top + 67)))
        return heroes

    def selected_hero(self):
        return normalize(self.text((50, 78, 306, 112)))

    def bright_text(self, area, minimum=18):
        x1, y1, x2, y2 = area
        return np.count_nonzero(np.min(self.image[y1:y2, x1:x2], axis=2) > 190) >= minimum

    def gold_border(self, area):
        x1, y1, x2, y2 = area
        pixels = self.image[y1:y2, x1:x2].astype(int)
        mask = ((pixels[:, :, 0] > 185) & (pixels[:, :, 1] > 140)
                & (pixels[:, :, 0] - pixels[:, :, 2] > 30)
                & (pixels[:, :, 1] - pixels[:, :, 2] > 15))
        return np.count_nonzero(mask) >= 40
