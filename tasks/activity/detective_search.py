import re

from module.base.button import ClickButton
from module.base.timer import Timer
from module.logger import logger
from module.ocr.ocr import Ocr
from tasks.activity.assets.assets_activity_detective_search import (
    DETECTIVE_REWARD_PENDING,
    DETECTIVE_REWARD_POPUP_CHECK,
    DETECTIVE_REWARD_RECEIVED,
    DETECTIVE_SEARCH_CHECK,
    OCR_DETECTIVE_POINTS,
)
from tasks.activity.navigation import ActivityNavigationMixin
from tasks.activity.scheduling import mark_activity_checked
from tasks.base.page import page_common_activity
from tasks.base.ui import UI


class DetectiveSearch(ActivityNavigationMixin, UI):
    """Claim earned point tiers; never start battles or spend event resources."""

    CLAIM_FLOW_TIMEOUT_SECONDS = 45
    TIERS = (180, 360, 540, 720)
    REWARD_AREAS = (
        (310, 559, 486, 610), (552, 600, 730, 652),
        (796, 559, 972, 610), (1040, 600, 1218, 652),
    )

    def __init__(self, config, device=None, task=None, activity_id="detective_search_2026_10_29"):
        super().__init__(config=config, device=device, task=task)
        self.activity_id = activity_id

    def _reward_state(self, area):
        # Restrict each template to one tier. Four individually confirmed
        # receipts are required; a lower tier's check cannot finish the day.
        for button, state in ((DETECTIVE_REWARD_RECEIVED, "received"),
                              (DETECTIVE_REWARD_PENDING, "pending")):
            searches = tuple(frame.search for frame in button.iter_buttons())
            try:
                button.load_search(area)
                if self.match_template_color(button):
                    return state
            finally:
                for frame, search in zip(button.iter_buttons(), searches):
                    frame.load_search(search)
                    frame.clear_offset()
        return None

    def run_claim(self, skip_first_screenshot=True, *, navigate=True) -> bool:
        """Collect available tiers, preserving incomplete days for rechecks.

        Pages:
            in: any; detective page selected when navigate=False
            out: page_common_activity, detective page, reward popup closed
        """
        if navigate:
            self.ui_goto(page_common_activity, skip_first_screenshot=skip_first_screenshot)
            if not self.select_activity("名侦探的每日搜查", DETECTIVE_SEARCH_CHECK):
                return False
            skip_first_screenshot = True
        else:
            if not skip_first_screenshot:
                self.device.screenshot()
                skip_first_screenshot = True
            if not self.match_template_color(DETECTIVE_SEARCH_CHECK):
                return False

        lang = self.config.Emulator_GameLanguage or "cn"
        if lang == "auto":
            lang = "cn"
        ocr = Ocr(OCR_DETECTIVE_POINTS, lang=lang, name="DetectivePoints")
        timeout = Timer(self.CLAIM_FLOW_TIMEOUT_SECONDS).start()
        pending_tier = None
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()
            if self.match_template_color(DETECTIVE_SEARCH_CHECK):
                states = [self._reward_state(area) for area in self.REWARD_AREAS]
                if pending_tier is None and all(state == "received" for state in states):
                    mark_activity_checked(self.config, self.activity_id)
                    return True
                if pending_tier is None and "pending" not in states:
                    text = ocr.ocr_single_line(self.device.image)
                    match = re.fullmatch(r"\s*(\d+)\s*/\s*720\s*", text)
                    points = int(match[1]) if match else None
                    if points is not None and 0 <= points < 720 and all(
                        state == "received" or points < tier
                        for tier, state in zip(self.TIERS, states)
                    ):
                        logger.info("LimitedActivity: detective points unfinished, recheck after battle")
                        return True
                candidates = [index for index, state in enumerate(states) if state == "pending"]
                if pending_tier is not None:
                    candidates = [pending_tier] if pending_tier in candidates else []
                if candidates:
                    index = candidates[0]
                    button = ClickButton(self.REWARD_AREAS[index], name=f"DETECTIVE_CLAIM_{self.TIERS[index]}")
                    if self.interval_is_reached(button, interval=2):
                        self.device.click(button)
                        self.interval_reset(button, interval=2)
                        # A changed card can precede the reward popup. Keep the
                        # original tier pending until that popup is dismissed;
                        # only the same still-claimable tier may retry safely.
                        pending_tier = index
                        continue
            if timeout.reached():
                logger.warning("LimitedActivity: detective reward timeout")
                return False
            if self.handle_network_error():
                continue
            if self.match_template_color(DETECTIVE_REWARD_POPUP_CHECK) and self.handle_touch_to_close():
                pending_tier = None
