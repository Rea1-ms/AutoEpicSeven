import re

from module.base.button import ClickButton
from module.base.timer import Timer
from module.logger import logger
from module.ocr.ocr import Ocr
from tasks.activity.assets.assets_activity_carrot_reward import (
    CARROT_REWARD_CHECK,
    CARROT_REWARD_PENDING,
    CARROT_REWARD_POPUP_CHECK,
    CARROT_REWARD_RECEIVED,
    OCR_CARROT_POINTS,
)
from tasks.activity.navigation import ActivityNavigationMixin
from tasks.activity.scheduling import mark_activity_checked
from tasks.base.page import page_common_activity
from tasks.base.ui import UI


class CarrotReward(ActivityNavigationMixin, UI):
    """Claim earned carrot tiers without collecting or spending resources."""

    FULL_PAGE_MARKERS = (CARROT_REWARD_CHECK,)
    CLAIM_FLOW_TIMEOUT_SECONDS = 45
    TIERS = (200, 400, 600, 800)
    REWARD_AREAS = (
        (296, 558, 496, 611), (539, 600, 742, 653),
        (782, 558, 984, 611), (1027, 600, 1231, 653),
    )

    def __init__(self, config, device=None, task=None, activity_id="carrot_reward_2026_11_12"):
        super().__init__(config=config, device=device, task=task)
        self.activity_id = activity_id

    def _reward_state(self, area):
        # Each tier needs its own receipt. Restore shared template searches so
        # a previous tier or another worker cannot change the next observation.
        for button, state in ((CARROT_REWARD_RECEIVED, "received"),
                              (CARROT_REWARD_PENDING, "pending")):
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
        """Claim available tiers and confirm the result before leaving.

        Pages:
            in: any; selected carrot page or its reward popup when navigate=False
            out: page_common_activity, carrot page with reward popup closed
        """
        if navigate:
            self.ui_goto(page_common_activity, skip_first_screenshot=skip_first_screenshot)
            if not self.select_activity("一起收集胡萝卜", CARROT_REWARD_CHECK):
                return False
            skip_first_screenshot = True
        else:
            if not skip_first_screenshot:
                self.device.screenshot()
                skip_first_screenshot = True
            if not (self.match_template_color(CARROT_REWARD_CHECK)
                    or self.match_template_color(CARROT_REWARD_POPUP_CHECK)):
                return False

        lang = self.config.Emulator_GameLanguage or "cn"
        if lang == "auto":
            lang = "cn"
        ocr = Ocr(OCR_CARROT_POINTS, lang=lang, name="CarrotPoints")
        timeout = Timer(self.CLAIM_FLOW_TIMEOUT_SECONDS).start()
        pending_tier = None
        reward_seen = False
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            candidates = []
            if self.match_template_color(CARROT_REWARD_CHECK):
                # A click may claim several tiers at once, but changed cards
                # alone cannot prove the popup was received. Only its title
                # followed by the usable page retires this pending request.
                # Keep that evidence while a close tap is delayed or dropped.
                if reward_seen:
                    pending_tier = None
                    reward_seen = False
                states = [self._reward_state(area) for area in self.REWARD_AREAS]
                if pending_tier is None and all(state == "received" for state in states):
                    mark_activity_checked(self.config, self.activity_id)
                    return True
                if pending_tier is None and "pending" not in states:
                    text = ocr.ocr_single_line(self.device.image)
                    match = re.fullmatch(r"\s*(\d+)\s*", text)
                    points = int(match[1]) if match else None
                    # Zero is valid. A total above 800 is also valid; 800 is a
                    # reward threshold, not a resource capacity. Missing tier
                    # controls are safe only when their thresholds are unmet.
                    if points is not None and 0 <= points < self.TIERS[-1] and all(
                        state == "received" or points < tier
                        for tier, state in zip(self.TIERS, states)
                    ):
                        logger.info("LimitedActivity: carrot tiers unfinished, recheck after battle")
                        return True
                candidates = [index for index, state in enumerate(states) if state == "pending"]
                if pending_tier is not None:
                    candidates = [pending_tier] if pending_tier in candidates else []

            # Check the original deadline before every retry, including when
            # one screenshot takes longer than the action interval. Otherwise
            # each slow frame clicks and continues without ever checking it.
            # Positive completion above still wins on the current screenshot.
            if timeout.reached():
                logger.warning(f"LimitedActivity: carrot reward timeout, pending={pending_tier}, popup={reward_seen}")
                return False
            if candidates:
                index = candidates[0]
                button = ClickButton(self.REWARD_AREAS[index], name=f"CARROT_CLAIM_{self.TIERS[index]}")
                if self.interval_is_reached(button, interval=2):
                    self.device.click(button)
                    self.interval_reset(button, interval=2)
                    # Retry only this still-claimable tier. Do not chase
                    # another tier while the original result is pending.
                    pending_tier = index
                    continue
            if self.handle_network_error():
                continue
            if self.match_template_color(CARROT_REWARD_POPUP_CHECK):
                reward_seen = True
                if self.handle_touch_to_close():
                    continue
