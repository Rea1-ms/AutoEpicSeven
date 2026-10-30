"""Shared episode selection for daily material farming and story progression."""

import re

from module.logger import logger
from module.ocr.ocr import OcrWhiteLetterOnComplexBackground
from tasks.dungeon.assets.assets_dungeon_configs_episode_entry import (
    GOTO_EPISODE_1,
    GOTO_EPISODE_1_CHECK,
    GOTO_EPISODE_2,
    GOTO_EPISODE_2_CHECK,
    GOTO_EPISODE_3,
    GOTO_EPISODE_3_CHECK,
    GOTO_EPISODE_4,
    GOTO_EPISODE_4_CHECK,
    GOTO_EPISODE_5,
    GOTO_EPISODE_5_CHECK,
    GOTO_EPISODE_6,
    GOTO_EPISODE_6_CHECK,
)
from tasks.dungeon.assets.assets_dungeon_episode_map import OCR_EPISODE_NUMBER
from tasks.dungeon.assets.assets_dungeon_episode_navigation import (
    EPISODE_CHAPTER_STAR,
    EPISODE_SELECTOR_CHECK,
    EPISODE_SELECTOR_ENTRY,
)

EPISODE_ENTRIES = {
    1: (GOTO_EPISODE_1, GOTO_EPISODE_1_CHECK),
    2: (GOTO_EPISODE_2, GOTO_EPISODE_2_CHECK),
    3: (GOTO_EPISODE_3, GOTO_EPISODE_3_CHECK),
    4: (GOTO_EPISODE_4, GOTO_EPISODE_4_CHECK),
    5: (GOTO_EPISODE_5, GOTO_EPISODE_5_CHECK),
    6: (GOTO_EPISODE_6, GOTO_EPISODE_6_CHECK),
}


class EpisodeSelectionMixin:
    def _is_episode_selector(self):
        return self.match_template_color(EPISODE_SELECTOR_CHECK, threshold=25)

    def _selector_episode(self):
        # The left labels are all visible at once, and the gold pin can still
        # mark the previous map. Only the right-panel title identifies which
        # episode owns the currently displayed chapter cards.
        for number, (_, check) in EPISODE_ENTRIES.items():
            if self.match_template_color(check, threshold=25):
                return number
        return None

    def _chapter_entries(self):
        # A star in the card's top-left corner is an entry even at 0 / total.
        # Locked cards have no star. Return this frame's matched coordinates;
        # the wrapper's original crop location is never a fallback target.
        entries = EPISODE_CHAPTER_STAR.match_multi_template(self.device.image)
        return sorted(entries, key=lambda item: (item.area[1], item.area[0]))

    def _ocr_episode_number(self):
        text = OcrWhiteLetterOnComplexBackground(
            OCR_EPISODE_NUMBER, lang=self._ocr_lang(),
        ).ocr_single_line(self.device.image)
        logger.attr('EpisodeNumber', text)
        matched = re.fullmatch(r'Episode\s*([1-6])', text.strip(), re.IGNORECASE)
        return int(matched[1]) if matched else None

    def _handle_episode_switch(self):
        """Open the selector from the map using its visible blue control."""
        if self.match_template_color(EPISODE_SELECTOR_ENTRY, threshold=25):
            return self._click_episode_navigation(EPISODE_SELECTOR_ENTRY)
        return False

    def _click_episode_navigation(self, button):
        if self.interval_is_reached(button, interval=2):
            self.device.click(button)
            self.interval_reset(button, interval=2)
            return True
        return False
