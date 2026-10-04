"""Recognition of the supplied 1280x720 overseas Chinese exploration UI."""

from dataclasses import dataclass
from functools import cached_property

import cv2
import numpy as np

from module.base.button import ClickButton
from module.base.utils import area_center, area_offset, area_limit, point_in_area, color_bar_percentage, color_similar, get_color
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
    SHOP_DISCOUNT, OCR_SHOP_DISCOUNT_PRICE, EVENT_OPTION_UNAVAILABLE, REWARD_LEAVE_CHECK, MANUAL_TARGET,
    ROSTER_CHECK, ROSTER_TITLE, ROSTER_RANK_ICON, ROSTER_HP_BAR, OCR_ROSTER_NAME, OCR_ROSTER_COUNT,
    OCR_UPGRADE_RANK, SACRIFICE_CHECK, SACRIFICE_NORMAL_CORNER, SACRIFICE_SPECIAL_CORNER,
    OCR_SACRIFICE_COUNTER, OCR_SACRIFICE_LOOT_COUNT, OCR_EVENT_LOOT,
)
from tasks.dimensional_exploration.policy import EventChoice, Offer, normalize, parse_counter, parse_number
from tasks.dimensional_exploration.recognition.hero_names import HeroNameOcr
from tasks.dimensional_exploration.assets.assets_dimensional_exploration_navigation import (
    PREVIEW_BATTLE, PREVIEW_BOSS, PREVIEW_ELITE, PREVIEW_EVENT, PREVIEW_REST, PREVIEW_SUPPLY,
)
from tasks.dungeon.assets.assets_dungeon_state import AUTO_COMBAT_EXIST
from tasks.dungeon.execute import detect_auto_combat_state


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


@dataclass(frozen=True)
class HeroHealth:
    name: str
    percent: int


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


def multi_match(image, asset, area, threshold=0.87, distance=24, button_anchor=False):
    x1, y1, x2, y2 = area
    sample = cv2.cvtColor(image[y1:y2, x1:x2], cv2.COLOR_RGB2GRAY)
    candidates = []
    for button in asset.iter_buttons():
        # Event frames can locate a clean upper border while BUTTON stores
        # their equivalent lower option anchor. Translate the canonical search
        # back to the template's native location, then return the canonical
        # anchor so existing OCR/click fields retain their meaning. Matching
        # text inside the old lower crop invents options on unrelated events.
        dx, dy = (button.button[0] - button.area[0], button.button[1] - button.area[1]) if button_anchor else (0, 0)
        search = area_limit(area_offset(area, (-dx, -dy)), (0, 0, image.shape[1], image.shape[0]))
        sx1, sy1, sx2, sy2 = search
        local = sample if search == area else cv2.cvtColor(image[sy1:sy2, sx1:sx2], cv2.COLOR_RGB2GRAY)
        template = button.image_luma
        h, w = template.shape
        if local.shape[0] < h or local.shape[1] < w:
            continue
        scores = cv2.matchTemplate(local, template, cv2.TM_CCOEFF_NORMED)
        while True:
            _, score, _, (x, y) = cv2.minMaxLoc(scores)
            if score < threshold:
                break
            box = (x + sx1, y + sy1, x + sx1 + w, y + sy1 + h)
            if button_anchor:
                box = area_offset(button.button, (box[0] - button.area[0], box[1] - button.area[1]))
            candidates.append((bool(dx or dy), score, box))
            scores[max(0, y - distance):y + distance + 1,
                   max(0, x - distance):x + distance + 1] = 0
    results = []
    # Preserve previously verified direct anchors when a translated border
    # identifies the same option. Border variants fill missing slots rather
    # than shifting old OCR crops by a few pixels and truncating their text.
    for _, _, box in sorted(candidates, key=lambda item: (not item[0], item[1]), reverse=True):
        if not any(abs(box[0] - old[0]) < distance and abs(box[1] - old[1]) < distance for old in results):
            results.append(box)
    return sorted(results, key=lambda box: (box[0], box[1]))


def relative_area(region, anchor, observed):
    """Move a source-defined box with its detected anchor, without global offsets."""
    reference = next(anchor.iter_buttons()).area
    offset = (observed[0] - reference[0], observed[1] - reference[1])
    return area_limit(area_offset(region.area, offset), (0, 0, 1280, 720))


class FragmentBalanceOcr(Ocr):
    padding = (2, 2)

    def pre_process(self, image):
        # A lone zero inside the full balance field is read as punctuation by
        # the Chinese model. Bound all neutral white glyphs together, keeping
        # their original pixels and a small margin instead of guessing that
        # punctuation means zero. Never select just one connected component:
        # that would drop digits from multi-digit balances. Blank or unreadable
        # fields must still fail the strict numeric parser, not confirm payment.
        light = image.min(axis=2) > 155
        neutral = image.max(axis=2).astype(int) - image.min(axis=2) < 65
        points = cv2.findNonZero((light & neutral).astype(np.uint8))
        if points is None:
            return image
        x, y, width, height = cv2.boundingRect(points)
        px, py = self.padding
        return image[max(0, y - py):y + height + py, max(0, x - px):x + width + px]


class EventLootCountOcr(FragmentBalanceOcr):
    # The wide owned-loot field adds spurious punctuation around a lone digit.
    # Bound every neutral glyph together and retain enough margin for narrow
    # 4/5 glyphs. This still preserves multiple digits and uses a strict parser;
    # never strip arbitrary OCR punctuation or reinterpret Q as a quantity.
    padding = (6, 4)


class ExplorationVision:
    STATES = (
        ("sacrifice", SACRIFICE_CHECK),
        ("reward_leave", REWARD_LEAVE_CHECK),
        ("settlement", SETTLEMENT_CHECK), ("reward", REWARD_CLOSE), ("abandon", ABANDON_CHECK),
        ("buy", BUY_CHECK), ("leave_confirm", LEAVE_CONFIRM_CHECK),
        ("loot", LOOT_CHECK), ("failed", FAILED_CHECK),
        ("upgrade", UPGRADE_CHECK), ("revive", REVIVE_CHECK),
        ("hero", HERO_PICKER_CHECK), ("roster", ROSTER_CHECK), ("prepare", PREPARE_CHECK),
        ("victory", VICTORY_CHECK), ("resume_rewards", RESUME_REWARDS_CHECK), ("rest", REST_CHECK),
        ("shop", SHOP_CHECK), ("supply_room", ROOM_SUPPLY_CHECK),
        ("event", EVENT_CHECK), ("recruitment", RECRUITMENT_CHECK),
        ("start_supply", SUPPLY_CHECK), ("title", TITLE_CHECK), ("chapter", CHAPTER_CHECK),
        ("lobby", LOBBY_CHECK), ("preview", NODE_ENTER), ("map", MAP_CHECK),
    )
    NODE_ASSETS = {
        "event": NODE_EVENT, "shop": NODE_SHOP, "supply": NODE_SUPPLY,
        "battle": NODE_BATTLE, "elite": NODE_ELITE, "rest": NODE_REST, "boss": NODE_BOSS,
    }
    PREVIEW_ASSETS = {
        "battle": PREVIEW_BATTLE, "elite": PREVIEW_ELITE, "boss": PREVIEW_BOSS,
        "event": PREVIEW_EVENT, "supply": PREVIEW_SUPPLY, "rest": PREVIEW_REST,
    }

    def __init__(self, image, hero_name_ocr=None):
        self.image = image
        self.hero_name_ocr = hero_name_ocr if hero_name_ocr is not None else HeroNameOcr()

    def state(self):
        for state, asset in self.STATES:
            if asset.match_template(self.image, similarity=0.87):
                # The start-party page's Enter button exists in disabled form
                # too; the separate Back button keeps it distinct from title.
                if state == "recruitment" and not EXPLORE_ENTER.match_template(self.image):
                    continue
                if state == "resume_rewards" and not RESUME_REWARDS_CHECK.match_color(self.image, threshold=30):
                    continue
                if state == "roster" and not ROSTER_TITLE.match_template(self.image, similarity=0.87):
                    continue
                return state
        if (self.auto_combat is not None or AUTO_COMBAT_EXIST.match_template(self.image)
                or MANUAL_TARGET.match_template(self.image)):
            return "battle"
        return "unknown"

    @cached_property
    def auto_combat(self):
        # This object owns one immutable screenshot; a new loop frame creates
        # a new instance, so HUD observations never survive across images.
        return detect_auto_combat_state(lambda asset: asset.match_template(self.image))

    def text(self, area, name="ExplorationText"):
        region = area if hasattr(area, "area") else ClickButton(area, name=name)
        return Ocr(region, lang="cn", name=getattr(region, "name", name)).ocr_single_line(self.image)

    def tokens(self, area, name="ExplorationOptions"):
        region = area if hasattr(area, "area") else ClickButton(area, name=name)
        if name in ("HeroName", "UpgradeName", "RosterName"):
            return self.hero_name_ocr.read(self.image, region.area, name)
        return Ocr(region, lang="cn", name=getattr(region, "name", name)).detect_and_ocr(self.image)

    def number(self, area, maximum=99999):
        if getattr(area, "name", None) == OCR_EVENT_LOOT.name:
            return parse_number(EventLootCountOcr(area, lang="cn").ocr_single_line(self.image), maximum)
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
        fragment_region = ClickButton(
            (fragment[1][2], fragment_area[1], fragment_area[2], fragment_area[3]), name="OCR_FRAGMENT")
        fragments = parse_number(FragmentBalanceOcr(fragment_region, lang="cn").ocr_single_line(self.image))
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

    def preview_kind(self):
        matches = [kind for kind, asset in self.PREVIEW_ASSETS.items()
                   if asset.match_template(self.image, similarity=0.9)]
        return matches[0] if len(matches) == 1 else None

    def offers(self):
        offers = []
        # The uncollected marker is an orange N icon, not the word NEW.
        # Its left edge overhangs the card, so match the full shop first and
        # bind each marker's center to a card. Cropping it to the old OCR
        # region truncates the icon; OCR of that region only reads the name.
        badges = [area_center(box) for box in multi_match(self.image, SHOP_NEW, SHOP_NEW.search)]
        discounts = multi_match(self.image, SHOP_DISCOUNT, SHOP_DISCOUNT.search)
        regions = zip(OCR_SHOP_NAME.iter_buttons(), OCR_SHOP_PRICE.iter_buttons(), OCR_SHOP_NEW.iter_buttons())
        for index, (name_area, price_area, badge_area) in enumerate(regions):
            name = self.text(name_area)
            price = self.text(price_area)
            badge = any(point_in_area(center, badge_area.area, threshold=0) for center in badges)
            # SALE is paired with its own card before reading the payable
            # amount to its lower right. Parsing all digits from a struck-out
            # price would silently turn "149 -> 108" into a different amount.
            sale = [box for box in discounts if point_in_area(area_center(box), badge_area.area, threshold=0)]
            if len(sale) == 1 and "购买完毕" not in normalize(price):
                price = self.text(relative_area(OCR_SHOP_DISCOUNT_PRICE, SHOP_DISCOUNT, sale[0]))
            offers.append(Offer(index, name, parse_number(price), "购买完毕" in normalize(price), badge))
        logger.attr("ExplorationShopNew", [(o.index + 1, o.name) for o in offers if o.new])
        return offers

    def event_choices(self):
        rectangles = multi_match(self.image, EVENT_OPTION, EVENT_OPTION.search, threshold=0.78, distance=60,
                                 button_anchor=True)
        disabled = [area_center(box) for box in multi_match(
            self.image, EVENT_OPTION_UNAVAILABLE, EVENT_OPTION_UNAVAILABLE.search)]
        result = []
        for index, rectangle in enumerate(rectangles):
            area = relative_area(OCR_EVENT_OPTION, EVENT_OPTION, rectangle)
            text = "".join(token.ocr_text for token in self.tokens(area))
            journal = match_in(self.image, EVENT_JOURNAL,
                               relative_area(EVENT_DETAIL_AREA, EVENT_OPTION, rectangle), threshold=0.8)
            # Keep clicks below the magnifier. Opening a detail preview is not
            # the same action as selecting the event branch or gaining its loot.
            click_area = relative_area(EVENT_OPTION_CLICK, EVENT_OPTION, rectangle)
            available = not any(point_in_area(center, area, threshold=20) for center in disabled)
            result.append((EventChoice(index, text, journal is not None, available), click_area))
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

    def heroes(self, stop_when=None):
        """Read stable rows until an eligible best candidate makes later OCR unnecessary."""
        heroes = []
        self.hero_signature = []
        for x, y, right, bottom in multi_match(self.image, HERO_COST_ICON, HERO_COST_ICON.search,
                                               threshold=0.8, distance=35):
            rectangle = (x, y, right, bottom)
            reference = next(HERO_COST_ICON.iter_buttons()).area
            offset = (x - reference[0], y - reference[1])
            area = area_offset(OCR_HERO_NAME.area, offset)
            cost_area = area_offset(OCR_HERO_COST.area, offset)
            # Horizontal scrolling can clip the card's decorative left edge
            # while leaving its name, cost and click target fully visible.
            # Validate the actual fields against the fixed list viewport,
            # before any screen clamping: rejecting the whole card drops an
            # entire readable left column; clamping a cost can invent a price.
            if area_limit(area, OCR_HEROES.area) != area or area_limit(cost_area, OCR_HEROES.area) != cost_area:
                continue
            tokens = self.tokens(area, name="HeroName")
            names = [t.ocr_text for t in tokens if len(t.ocr_text) >= 2]
            name = normalize(names[0]) if names else ""
            cost = self.number(cost_area, maximum=9)
            # Include unaffordable rows in the scroll fingerprint. Two pages
            # with no affordable heroes are not necessarily the same page.
            self.hero_signature.append((name, cost, x, y))
            x1, y1, x2, y2 = relative_area(HERO_COST_ACTIVE, HERO_COST_ICON, rectangle)
            pixels = self.image[y1:y2, x1:x2]
            bright = np.count_nonzero(np.min(pixels, axis=2) > 175)
            if cost and name and bright >= 10:
                hero = Hero(name, cost, relative_area(HERO_ROW_CLICK, HERO_COST_ICON, rectangle))
                heroes.append(hero)
                if stop_when is not None and stop_when(hero):
                    break
        return heroes

    def selected_hero(self):
        return normalize(self.text(OCR_SELECTED_HERO))

    def roster_health(self):
        """Read owned heroes with ALAS's color-bar reader, never the death allowance."""
        count = self.number(OCR_ROSTER_COUNT, maximum=99)
        rectangles = multi_match(self.image, ROSTER_RANK_ICON, ROSTER_RANK_ICON.search,
                                 threshold=0.87, distance=25)
        if not count or len(rectangles) != count:
            return None
        heroes = []
        for rectangle in rectangles:
            tokens = self.tokens(relative_area(OCR_ROSTER_NAME, ROSTER_RANK_ICON, rectangle), name="RosterName")
            name = normalize(tokens[0].ocr_text) if len(tokens) == 1 else ""
            area = relative_area(ROSTER_HP_BAR, ROSTER_RANK_ICON, rectangle)
            x1, y1, x2, y2 = area
            pixels = self.image[y1:y2, x1:x2].astype(int)
            green = np.max(np.abs(pixels - (139, 196, 25)), axis=2) < 45
            # The unfilled part is translucent: portrait pixels slightly tint
            # its dark grey. Validate that whole muted field instead of one
            # fixed RGB triplet; black missing pixels and bright overlays fail.
            empty = ((pixels.min(axis=2) > 15) & (pixels.max(axis=2) < 110)
                     & (pixels.max(axis=2) - pixels.min(axis=2) < 35))
            # A missing/occluded bar must not be interpreted as a dead hero.
            # Require the measured field to contain the actual green/grey bar
            # even when its green fill is zero. Incomplete rosters are retried.
            if not name or np.mean(green | empty) < 0.85:
                return None
            percent = round(100 * color_bar_percentage(self.image, area, (139, 196, 25), threshold=35))
            heroes.append(HeroHealth(name, percent))
        if len({h.name for h in heroes}) != count:
            return None
        return tuple(heroes)

    def upgrade_roster(self):
        from tasks.dimensional_exploration.heroes.upgrade import MAX_RANK, RankedHero
        if self.state() != "upgrade":
            return None
        count = self.number(OCR_ROSTER_COUNT, maximum=99)
        rectangles = multi_match(self.image, ROSTER_RANK_ICON, ROSTER_RANK_ICON.search,
                                 threshold=0.87, distance=25)
        if not count or not rectangles or len(rectangles) > count:
            return None
        heroes = []
        reference = next(ROSTER_RANK_ICON.iter_buttons()).area
        for rectangle in rectangles:
            offset = (rectangle[0] - reference[0], rectangle[1] - reference[1])
            name_area = area_offset(OCR_ROSTER_NAME.area, offset)
            rank_area = area_offset(OCR_UPGRADE_RANK.area, offset)
            # Clamping a half-visible name can make an incomplete list appear
            # complete and falsely declare a priority hero absent. Validate the
            # original fields inside the viewport; omit clipped rows so the
            # picker keeps scanning instead of ranking their partial names.
            if (area_limit(name_area, ROSTER_RANK_ICON.search) != name_area
                    or area_limit(rank_area, ROSTER_RANK_ICON.search) != rank_area):
                continue
            name_tokens = self.tokens(name_area, name="UpgradeName")
            if len(name_tokens) != 1:
                return None
            name = normalize(name_tokens[0].ocr_text)
            rank = self.number(rank_area, maximum=MAX_RANK)
            if not name or rank is None or not 1 <= rank <= MAX_RANK:
                return None
            heroes.append(RankedHero(name, rank, tuple(name_tokens[0].box)))
        if not heroes or len({h.name for h in heroes}) != len(heroes):
            return None
        return tuple(heroes), count

    def bright_text(self, area, minimum=18):
        area = area.area if hasattr(area, "area") else area
        x1, y1, x2, y2 = area
        return np.count_nonzero(np.min(self.image[y1:y2, x1:x2], axis=2) > 190) >= minimum

    def sacrifice_counter(self):
        return parse_counter(self.text(OCR_SACRIFICE_COUNTER), maximum=99)

    def sacrifice_loot_count(self):
        return self.number(OCR_SACRIFICE_LOOT_COUNT, maximum=99)

    def sacrifice_items(self):
        result = []
        for asset in (SACRIFICE_NORMAL_CORNER, SACRIFICE_SPECIAL_CORNER):
            reference = next(asset.iter_buttons())
            items = []
            for rectangle in multi_match(self.image, asset, asset.search, threshold=0.87, distance=25):
                # The same corner shape exists on normal and special items.
                # Locate its shape first, then require its native border color.
                if not color_similar(get_color(self.image, rectangle), reference.color, threshold=20):
                    continue
                offset = (rectangle[0] - reference.area[0], rectangle[1] - reference.area[1])
                area = area_offset(reference.button, offset)
                if area_limit(area, asset.search) == area:
                    items.append(area)
            result.append(tuple(sorted(items, key=lambda area: (area[1], area[0]))))
        return tuple(result)

    def gold_border(self, area):
        area = area.area if hasattr(area, "area") else area
        x1, y1, x2, y2 = area
        pixels = self.image[y1:y2, x1:x2].astype(int)
        mask = ((pixels[:, :, 0] > 185) & (pixels[:, :, 1] > 160)
                & (pixels[:, :, 1] >= pixels[:, :, 2] - 12))
        return np.count_nonzero(mask) >= 40
