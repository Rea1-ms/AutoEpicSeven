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

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # OCR identifies the clickable text, not the selected state. The
            # orange template must match at its current position before the
            # caller can claim anything. A successful click alone is never
            # evidence of navigation, and must remain retryable.
            if self.match_template_color(selected_button):
                logger.info(f"SpecialActivity: selected sidebar event {keyword}")
                return True
            if timeout.reached():
                logger.warning(f"SpecialActivity: sidebar navigation timeout for {keyword}")
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
                previous = signature
                if stable and len(matches) == 1:
                    if self.interval_is_reached(selected_button, interval=2):
                        self.device.click(OcrResultButton(matches[0], matched_keyword=None))
                        self.interval_reset(selected_button, interval=2)
                        previous = None
                        continue
                elif stable and not matches:
                    if self.interval_is_reached(COMMON_ACTIVITY_LIST, interval=2):
                        if scrolls >= self.ACTIVITY_SCROLL_UP_LIMIT + self.ACTIVITY_SCROLL_DOWN_LIMIT:
                            logger.warning(f"SpecialActivity: sidebar search exhausted for {keyword}")
                            return False
                        left, top, right, bottom = COMMON_ACTIVITY_LIST.area
                        x = (left + right) // 2
                        upper = (x, top + 100)
                        lower = (x, bottom - 100)
                        # Search earlier entries first because the panel keeps
                        # its previous scroll position. Then scan later entries
                        # with overlapping swipes. The limits bound a missing
                        # event without interpreting absence as a claimed reward.
                        start, end = ((upper, lower) if scrolls < self.ACTIVITY_SCROLL_UP_LIMIT
                                      else (lower, upper))
                        self.device.swipe(start, end, duration=(0.3, 0.4))
                        self.interval_reset(COMMON_ACTIVITY_LIST, interval=2)
                        scrolls += 1
                        previous = None
                        continue
            else:
                previous = None

            if self.handle_network_error():
                previous = None
                continue
