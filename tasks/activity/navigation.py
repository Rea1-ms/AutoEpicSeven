from module.base.timer import Timer
from module.logger import logger
from module.ocr.ocr import Ocr, OcrResultButton
from tasks.activity.assets.assets_activity_common import COMMON_ACTIVITY_LIST
from tasks.base.page import page_common_activity


class ActivityNavigationMixin:
    ACTIVITY_NAVIGATION_TIMEOUT_SECONDS = 60
    ACTIVITY_SCROLL_UP_LIMIT = 4
    ACTIVITY_SCROLL_DOWN_LIMIT = 8

    @staticmethod
    def _activity_text(text: str) -> str:
        return "".join(text.upper().split())

    @staticmethod
    def _activity_list_stable(previous, current) -> bool:
        if previous is None or len(previous) != len(current):
            return False
        return all(
            old_text == text and max(abs(a - b) for a, b in zip(old_box, box)) <= 3
            for (old_text, old_box), (text, box) in zip(previous, current)
        )

    def _activity_selected(self, selected_button) -> bool:
        # Every event shares the scrollable sidebar. Keep the per-frame search
        # bounds intact: wrappers are global objects reused by later tasks.
        buttons = tuple(selected_button.iter_buttons())
        searches = tuple(button.search for button in buttons)
        try:
            selected_button.load_search(COMMON_ACTIVITY_LIST.area)
            return self.match_template_color(selected_button)
        finally:
            for button, search in zip(buttons, searches):
                button.load_search(search)
                button.clear_offset()

    @staticmethod
    def _activity_scroll_unchanged(before, after) -> bool:
        # OCR may omit rows or slightly change one glyph. Two unchanged row
        # positions are enough to detect a scroll boundary, but an empty or
        # unrelated read must never be mistaken for the end of the list.
        if not before or not after:
            return False
        old_rows = dict(before)
        common = [(old_rows[text], box) for text, box in after if text in old_rows]
        return len(common) >= 2 and all(
            max(abs(a - b) for a, b in zip(old, box)) <= 5
            for old, box in common
        )

    def select_activity(self, keyword, selected_button, skip_first_screenshot=True) -> bool:
        """Select an event by sidebar text and confirm its selected template.

        Pages:
            in: page_common_activity, any event tab
            out: page_common_activity, selected_button
        """
        lang = self.config.Emulator_GameLanguage
        if not lang or lang == "auto":
            lang = "cn"
        ocr = Ocr(COMMON_ACTIVITY_LIST, lang=lang, name="ActivityList")
        keyword = self._activity_text(keyword)
        timeout = Timer(self.ACTIVITY_NAVIGATION_TIMEOUT_SECONDS, count=180).start()
        previous = None
        scrolls = 0
        search_later = True
        before_scroll = None

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # OCR identifies the clickable text, not the selected state. The
            # orange template must match at its current position before the
            # caller can claim anything. A successful click alone is never
            # evidence of navigation, and must remain retryable.
            if self._activity_selected(selected_button):
                logger.info(f"SpecialActivity: selected sidebar event {selected_button.name}")
                return True
            if timeout.reached():
                logger.warning(f"SpecialActivity: sidebar navigation timeout for {selected_button.name}")
                return False

            if self.ui_page_appear(page_common_activity):
                results = ocr.detect_and_ocr(self.device.image)
                matches = [result for result in results
                           if keyword in self._activity_text(result.ocr_text)]
                # Track just the target when visible; other sidebar labels can
                # change during animations. Otherwise track the recognized list
                # before scrolling. Coordinates must agree on fresh screenshots
                # so inertial motion cannot turn an old OCR box into a wrong tap.
                observed = matches if matches else results
                signature = tuple(
                    (self._activity_text(result.ocr_text), tuple(result.box))
                    for result in observed
                )
                stable = bool(signature) and self._activity_list_stable(previous, signature)
                has_previous = previous is not None
                previous = signature
                if stable and len(matches) == 1:
                    if self.interval_is_reached(selected_button, interval=2):
                        self.device.click(OcrResultButton(matches[0], matched_keyword=None))
                        self.interval_reset(selected_button, interval=2)
                        previous = None
                        continue
                elif has_previous and not matches:
                    if self.interval_is_reached(COMMON_ACTIVITY_LIST, interval=2):
                        limit = self.ACTIVITY_SCROLL_DOWN_LIMIT if search_later else self.ACTIVITY_SCROLL_UP_LIMIT
                        if scrolls >= limit or self._activity_scroll_unchanged(before_scroll, signature):
                            if not search_later:
                                logger.warning(f"SpecialActivity: sidebar search exhausted for {selected_button.name}")
                                return False
                            search_later = False
                            scrolls = 0
                            logger.info("SpecialActivity: reverse sidebar search toward earlier entries")
                        left, top, right, bottom = COMMON_ACTIVITY_LIST.area
                        x = (left + right) // 2
                        upper = (x, top + 100)
                        lower = (x, bottom - 100)
                        # New events appear before older ongoing campaigns.
                        # Swipe up first to reveal later entries. Scrolling
                        # needs no stable OCR coordinates; only clicking does.
                        # Reverse at an unchanged viewport or the sweep limit.
                        start, end = (lower, upper) if search_later else (upper, lower)
                        self.device.swipe(start, end, duration=(0.3, 0.4))
                        self.interval_reset(COMMON_ACTIVITY_LIST, interval=2)
                        scrolls += 1
                        before_scroll = signature
                        previous = None
                        continue
            else:
                previous = None

            if self.handle_network_error():
                previous = None
                continue
