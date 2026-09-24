"""Read rune quantities from the growth-materials inventory."""

import re
import unicodedata

import cv2
import numpy as np

from module.base.timer import Timer
from module.base.utils import crop
from module.exception import RequestHumanTakeover
from module.logger import logger
from module.ocr.ocr import Ocr
from tasks.base.page import page_inventory, page_main
from tasks.dungeon.rune_balance import RUNE_ELEMENTS, RUNE_TIERS, RuneStock
from tasks.item.assets.assets_item_runes import (
    GROWTH_MATERIALS_ENTRY,
    GROWTH_MATERIALS_SELECTED,
    MATERIALS_ENTRY,
    MATERIALS_SELECTED,
    RUNE_DARK_EPIC,
    RUNE_DARK_GREATER,
    RUNE_DARK_NORMAL,
    RUNE_FIRE_EPIC,
    RUNE_FIRE_GREATER,
    RUNE_FIRE_NORMAL,
    RUNE_FOLLOWING_LEAF,
    RUNE_INVENTORY_LIST,
    RUNE_LIGHT_EPIC,
    RUNE_LIGHT_GREATER,
    RUNE_LIGHT_NORMAL,
    RUNE_NATURE_EPIC,
    RUNE_NATURE_GREATER,
    RUNE_NATURE_NORMAL,
    RUNE_PRECEDING_BLOOM,
    RUNE_WATER_EPIC,
    RUNE_WATER_GREATER,
    RUNE_WATER_NORMAL,
)


RUNE_ICONS = {
    "Dark": (RUNE_DARK_NORMAL, RUNE_DARK_GREATER, RUNE_DARK_EPIC),
    "Light": (RUNE_LIGHT_NORMAL, RUNE_LIGHT_GREATER, RUNE_LIGHT_EPIC),
    "Water": (RUNE_WATER_NORMAL, RUNE_WATER_GREATER, RUNE_WATER_EPIC),
    "Fire": (RUNE_FIRE_NORMAL, RUNE_FIRE_GREATER, RUNE_FIRE_EPIC),
    "Nature": (RUNE_NATURE_NORMAL, RUNE_NATURE_GREATER, RUNE_NATURE_EPIC),
}


def parse_rune_count(text: str) -> int | None:
    text = unicodedata.normalize("NFKC", text).strip()
    if not re.fullmatch(r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)", text):
        return None
    return int(text.replace(",", ""))


class RuneCountOcr(Ocr):
    def pre_process(self, image):
        # Short labels surrounded by the whole card width are misread as a
        # currency by the Chinese model. Locate the white glyphs, retaining
        # enough context for a single '1'; do not strip OCR characters later.
        light = image.min(axis=2) > 155
        neutral = image.max(axis=2).astype(int) - image.min(axis=2) < 65
        points = cv2.findNonZero((light & neutral).astype(np.uint8))
        if points is None:
            return image
        x, y, width, height = cv2.boundingRect(points)
        return image[max(0, y - 2):y + height + 2, max(0, x - 8):x + width + 8]


def _rune_section_bounds(image):
    positions = []
    for icon in (RUNE_PRECEDING_BLOOM, RUNE_FOLLOWING_LEAF):
        icon.load_search(RUNE_INVENTORY_LIST.area)
        icon.clear_offset()
        matches = icon.match_multi_template(image, similarity=0.9, threshold=8)
        if len(matches) != 1:
            return None
        x, y, _, _ = matches[0].area
        positions.append((int(x), int(y)))
    (start_x, start_y), (end_x, end_y) = positions
    row_span = (end_y - start_y) / 133
    column_span = (end_x - start_x) / 99
    if abs(row_span - round(row_span)) > 0.08 or abs(column_span - round(column_span)) > 0.08:
        return None
    slots = round(row_span) * 7 + round(column_span) - 1
    return (positions[0], slots) if 0 <= slots <= 15 else None


def read_rune_inventory_frame(image, *, lang: str) -> dict[str, RuneStock] | None:
    """Read present icons and infer zeros only inside a complete rune section.

    The user confirmed that zero-stock entries disappear in this ordering.
    Seeing both neighboring non-rune materials proves that no rune rows are
    outside the viewport. Otherwise all fifteen icons must be found. A found
    icon with invalid OCR always rejects the frame, even with both boundaries.
    """
    if image.shape[:2] != (720, 1280):
        raise ValueError("Rune inventory requires a 1280x720 screenshot")
    search_area = RUNE_INVENTORY_LIST.area
    ocr = RuneCountOcr(RUNE_INVENTORY_LIST, lang=lang, name="RuneInventoryCount")
    bounds = _rune_section_bounds(image)
    fields = []
    images = []
    occupied = []
    section_slots = set()
    for element, icons in RUNE_ICONS.items():
        for tier, icon in zip(RUNE_TIERS, icons):
            # Search ranges are mutable on shared assets. Apply the common
            # list area at recognition time, including after resource reloads.
            icon.load_search(search_area)
            icon.clear_offset()
            matches = icon.match_multi_template(image, similarity=0.9, threshold=8)
            if not matches and bounds is not None:
                continue
            if len(matches) != 1:
                logger.info(f"Rune inventory: {element}/{tier} matched {len(matches)} icons")
                return None
            x1, y1, x2, _ = (int(value) for value in matches[0].area)
            # All source templates retain the same 62x64 inner-icon geometry.
            # Counts are below the card artwork, never at a fixed grid index:
            # missing materials and inventory sorting can move every item.
            area = (x1 - 5, y1 + 74, x2 + 5, y1 + 99)
            if (
                area[0] < search_area[0]
                or area[1] < search_area[1]
                or area[2] > search_area[2]
                or area[3] > search_area[3]
            ):
                logger.info(f"Rune inventory: clipped count for {element}/{tier}")
                return None
            if bounds is not None:
                (start_x, start_y), slots = bounds
                slot = round((y1 - start_y) / 133) * 7 + round((x1 - start_x) / 99)
                if not 1 <= slot <= slots or slot in section_slots:
                    logger.info("Rune inventory: rune outside the expected material ordering")
                    return None
                section_slots.add(slot)
            if any(abs(x1 - x) < 30 and abs(y1 - y) < 30 for x, y in occupied):
                logger.info("Rune inventory: two rune templates selected the same card")
                return None
            occupied.append((x1, y1))
            fields.append((element, tier))
            images.append(crop(image, area))

    if bounds is not None and len(fields) != bounds[1]:
        # A disappeared zero-stock item makes the later cards shift left.
        # A template miss leaves an occupied, unclassified slot instead. This
        # distinction must be checked before filling any absent type with zero.
        logger.info("Rune inventory: an occupied rune-section card was not recognized")
        return None
    values = {element: {tier: 0 for tier in RUNE_TIERS} for element in RUNE_ELEMENTS}
    readings = ocr.ocr_multi_lines(images) if images else []
    if len(readings) != len(fields):
        return None
    for (element, tier), (text, score) in zip(fields, readings):
        logger.info(f"Rune inventory {element}/{tier}: raw={text!r}, confidence={score:.3f}")
        count = parse_rune_count(text)
        if count is None or not np.isfinite(score) or score < 0.8:
            return None
        values[element][tier] = count
    return {element: RuneStock(**counts) for element, counts in values.items()}


class RuneInventoryMixin:
    def _is_rune_inventory(self) -> bool:
        return (
            self.match_template_luma(MATERIALS_ENTRY)
            and MATERIALS_SELECTED.match_color(self.device.image, threshold=20)
            and self.match_template_luma(GROWTH_MATERIALS_ENTRY)
            and GROWTH_MATERIALS_SELECTED.match_color(self.device.image, threshold=20)
        )

    def _enter_rune_inventory(self, skip_first_screenshot=True) -> None:
        """Open growth materials from a known inventory return origin.

        Pages:
            in: any routable page, without an unsettled battle
            out: page_inventory, growth-materials tab
        """
        self.ui_goto(page_main, skip_first_screenshot=skip_first_screenshot)
        self.ui_goto(page_inventory)
        timeout = Timer(20, count=12).start()
        while 1:
            self.device.screenshot()
            if self._is_rune_inventory():
                return
            if timeout.reached():
                logger.critical("Rune inventory: cannot open growth materials")
                raise RequestHumanTakeover
            if MATERIALS_SELECTED.match_color(self.device.image, threshold=20):
                if self.appear_then_click(GROWTH_MATERIALS_ENTRY, interval=2):
                    continue
            elif self.appear_then_click(MATERIALS_ENTRY, interval=2):
                continue
            if self.ui_additional():
                continue

    def _read_rune_inventory(self, skip_first_screenshot=True) -> dict[str, RuneStock]:
        """Read a complete inventory twice, then return to main.

        Pages:
            in: any routable page, without an unsettled battle
            out: page_main, or growth materials when human review is needed
        """
        self._enter_rune_inventory(skip_first_screenshot=skip_first_screenshot)
        previous = None
        for _ in range(4):
            self.device.screenshot()
            if self._is_rune_inventory():
                current = read_rune_inventory_frame(self.device.image, lang=self._ocr_lang())
                if current is not None and current == previous:
                    self.ui_goto(page_main)
                    return current
                previous = current
            else:
                previous = None
            if self.ui_additional():
                previous = None
        logger.critical(
            "Rune inventory: cannot verify the complete rune section or its quantities. "
            "Show the growth-materials rune section with its neighboring materials; "
            "unreadable quantities are not zero."
        )
        raise RequestHumanTakeover
