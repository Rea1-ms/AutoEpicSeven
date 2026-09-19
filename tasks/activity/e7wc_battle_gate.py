import cv2
import numpy as np

import module.config.server as server
from module.base.timer import Timer
from module.base.utils import crop
from module.logger import logger
from tasks.activity.assets.assets_activity_special_26_9_12 import (
    E7WC_BATTLE_GATE_CHECK,
    E7WC_BATTLE_GATE_SELECTED,
    E7WC_LEFT_REWARD_AVAILABLE,
    E7WC_REWARD_RECEIVED,
    E7WC_RIGHT_REWARD_AVAILABLE,
)
from tasks.activity.navigation import ActivityNavigationMixin
from tasks.activity.scheduling import mark_activity_checked
from tasks.base.page import page_common_activity, page_main
from tasks.base.ui import UI


class E7wcBattleGate(ActivityNavigationMixin, UI):
    """Claim the overseas E7WC Battle Gate daily rewards together."""

    CLAIM_FLOW_TIMEOUT_SECONDS = 30

    def __init__(self, config, device=None, task=None, activity_id="e7wc_battle_gate_2026_09_10"):
        super().__init__(config=config, device=device, task=task)
        self.activity_id = activity_id

    @staticmethod
    def _checkmark_mask(image):
        # The same lime check overlays different item artwork. Extract its
        # shape from both the screenshot and the single source template;
        # comparing entire RGB rectangles would include unrelated backgrounds.
        red, green, blue = cv2.split(image.astype(np.int16))
        return ((green - red > 35) & (green - blue > 80) & (green > 120)).astype(np.uint8) * 255

    def reward_received(self, reward) -> bool:
        template = self._checkmark_mask(E7WC_REWARD_RECEIVED.matched_button.image)
        image = self._checkmark_mask(crop(self.device.image, reward.area, copy=False))
        result = cv2.matchTemplate(image, template, cv2.TM_CCOEFF_NORMED)
        return cv2.minMaxLoc(result)[1] >= 0.85

    def run_claim(self, skip_first_screenshot=True) -> bool:
        """Claim one icon, dismiss its popup, and verify the received checks.

        Pages:
            in: page_main, any
            out: page_common_activity, both daily rewards received
        """
        self.ui_goto(page_common_activity, skip_first_screenshot=skip_first_screenshot)
        # The final character in the sidebar is read as several different
        # glyphs by OCR. The stable prefix locates the row; the orange selected
        # template still has to confirm navigation before any reward click.
        if not self.select_activity("激战门", E7WC_BATTLE_GATE_SELECTED):
            return False

        timeout = Timer(self.CLAIM_FLOW_TIMEOUT_SECONDS, count=60).start()
        claim_requested = False
        popup_closed = False

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            page_ready = self.match_template_color(E7WC_BATTLE_GATE_CHECK)
            left_received = page_ready and self.reward_received(E7WC_LEFT_REWARD_AVAILABLE)
            right_received = page_ready and self.reward_received(E7WC_RIGHT_REWARD_AVAILABLE)
            # One click claims both items. If we clicked, wait for the reward
            # popup to be dismissed before accepting the refreshed page. A
            # popup alone is not proof of receipt; existing checks can complete
            # immediately when this run did not initiate a claim.
            if left_received and right_received and (not claim_requested or popup_closed):
                mark_activity_checked(self.config, self.activity_id)
                logger.info("SpecialActivity: E7WC Battle Gate rewards received")
                return True
            if timeout.reached():
                logger.warning("SpecialActivity: E7WC Battle Gate claim timeout")
                return False

            # Only click the leaf. Never click the second item while the first
            # request is opening its popup. A missed tap may retry this same
            # icon at the normal interval while the unclaimed page is visible.
            if page_ready and not left_received:
                if self.match_template_color(E7WC_LEFT_REWARD_AVAILABLE, interval=2):
                    self.device.click(E7WC_LEFT_REWARD_AVAILABLE)
                    claim_requested = True
                    popup_closed = False
                    continue

            if claim_requested and self.handle_touch_to_close(interval=2):
                popup_closed = True
                continue
            if self.handle_network_error():
                continue

    def run(self) -> bool:
        """Run this overseas-only claim and return to the main page.

        Pages:
            in: page_main, any
            out: page_main
        """
        if not server.is_oversea_server(self.config.Emulator_PackageName) or server.lang != "global_cn":
            logger.info("SpecialActivity: E7WC Battle Gate unsupported on this server/language")
            self.config.task_delay(server_update=True)
            return True
        if not self.config.SpecialActivity_GetE7wcBattleGateReward:
            logger.info("SpecialActivity: E7WC Battle Gate reward disabled")
            self.config.task_delay(server_update=True)
            return True

        logger.hr("SpecialActivity: E7WC Battle Gate", level=1)
        if not self.device.app_is_running():
            from tasks.login.login import Login

            Login(self.config, device=self.device).app_start()

        success = self.run_claim()
        if success:
            self.config.task_delay(server_update=True)
        else:
            self.config.task_delay(success=False)
        return success
