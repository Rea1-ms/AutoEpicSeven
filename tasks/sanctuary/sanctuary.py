"""
Epic Seven 圣域模块

最小化验证路线：
    MAIN_GOTO_SANCTUARY -> SANCTUARY_CHECK
    日常 / 周常 / 月常 分开进入与处理
"""
from module.base.timer import Timer
from module.logger import logger
from tasks.base.assets.assets_base_page import MAIN_GOTO_SANCTUARY, SANCTUARY_CHECK
from tasks.base.ui import UI
from tasks.sanctuary.assets.assets_sanctuary import (
    FOREST_OF_ELVES,
    ALCHEMISTS_TOWER,
    HEART_OF_EULERBIS,
    ALCHEMISTS_TOWER_CHECK,
    HEART_OF_EULERBIS_CHECK,
)
from tasks.sanctuary.assets.assets_sanctuary_forest_of_elves import (
    ALTAR_OF_GROWTH,
    CARE,
    CLAIM_REWARDS,
)
from tasks.sanctuary.assets.assets_sanctuary_heart_of_eulerbis import (
    PURIFY,
    DEPOSIT_BOX_NOT_FULL,
    REWARDS_TIER_A,
    REWARDS_TIER_B,
    CUSTODY,
)


class Sanctuary(UI):
    """
    圣域任务
    """

    def _enter_sanctuary(self) -> bool:
        logger.hr("Enter Sanctuary", level=1)
        timeout = Timer(15, count=30).start()
        while 1:
            self.device.screenshot()

            if timeout.reached():
                logger.warning("Enter sanctuary timeout")
                return False

            if self.appear(SANCTUARY_CHECK):
                return True

            if self.appear_then_click(MAIN_GOTO_SANCTUARY, interval=2):
                continue

            if self.ui_additional():
                continue
            if self.handle_network_error():
                continue

    def _back_to_sanctuary(self) -> bool:
        timeout = Timer(10, count=20).start()
        while 1:
            self.device.screenshot()

            if self.appear(SANCTUARY_CHECK):
                return True

            if timeout.reached():
                logger.warning("Back to sanctuary timeout")
                return False

            if self.handle_ui_back(ALTAR_OF_GROWTH, interval=2):
                continue
            if self.handle_ui_back(ALCHEMISTS_TOWER_CHECK, interval=2):
                continue
            if self.handle_ui_back(HEART_OF_EULERBIS_CHECK, interval=2):
                continue

            if self.ui_additional():
                continue
            if self.handle_network_error():
                continue

    # =========================
    # Daily
    # =========================
    def _enter_daily(self) -> bool:
        logger.info("Enter daily: Forest of Elves")
        timeout = Timer(10, count=20).start()
        while 1:
            self.device.screenshot()

            if timeout.reached():
                logger.warning("Enter daily timeout")
                return False

            if self.appear(ALTAR_OF_GROWTH):
                return True

            if self.appear_then_click(FOREST_OF_ELVES, interval=2):
                continue

            if self.ui_additional():
                continue
            if self.handle_network_error():
                continue

    def _daily_claim_rewards(self):
        logger.info("Daily: claim rewards")
        timeout = Timer(10, count=20).start()
        while 1:
            self.device.screenshot()

            if timeout.reached():
                break

            if self.handle_touch_to_close():
                timeout.reset()
                continue

            if self.appear(CARE, interval=1, similarity=0.8):
                self.device.click(CARE)
                # care 按完之后得重新截图，因为可能会多一个红点（魔罗戈拉熟了）
                self.device.screenshot()
                timeout.reset()
                continue

            matches = CLAIM_REWARDS.match_multi_template(self.device.image)
            if matches:
                for btn in matches:
                    self.device.click(btn)
                timeout.reset()
                continue
            break

    def run_daily(self) -> bool:
        if not self._enter_sanctuary():
            return False
        if not self._enter_daily():
            return False
        self._daily_claim_rewards()
        return True

    # =========================
    # Weekly
    # =========================
    def _enter_weekly(self) -> bool:
        logger.info("Enter weekly: Alchemists Tower")
        timeout = Timer(10, count=20).start()
        while 1:
            self.device.screenshot()

            if timeout.reached():
                logger.warning("Enter weekly timeout")
                return False

            if self.appear(ALCHEMISTS_TOWER_CHECK):
                return True

            if self.appear_then_click(ALCHEMISTS_TOWER, interval=2):
                continue

            if self.ui_additional():
                continue
            if self.handle_network_error():
                continue

    def run_weekly(self) -> bool:
        if not self._enter_sanctuary():
            return False
        if not self._back_to_sanctuary():
            return False
        if not self._enter_weekly():
            return False
        # TODO: weekly OCR/logic
        logger.info("Weekly: TODO (OCR)")
        return True

    # =========================
    # Monthly
    # =========================
    def _enter_monthly(self) -> bool:
        logger.info("Enter monthly: Heart of Eulerbis")
        timeout = Timer(10, count=20).start()
        while 1:
            self.device.screenshot()

            if timeout.reached():
                logger.warning("Enter monthly timeout")
                return False

            if self.appear(HEART_OF_EULERBIS_CHECK):
                return True

            if self.appear_then_click(HEART_OF_EULERBIS, interval=2):
                continue

            if self.ui_additional():
                continue
            if self.handle_network_error():
                continue

    def _monthly_purify(self) -> bool:
        """
        Returns:
            bool: True if monthly completed (no PURIFY), False if ended due to full
        """
        logger.info("Monthly: purify loop")
        timeout = Timer(60, count=120).start()
        completed = False

        while 1:
            self.device.screenshot()

            if timeout.reached():
                logger.warning("Monthly purify timeout")
                break

            purify_luma = PURIFY.match_template_luma(self.device.image)
            if not purify_luma:
                completed = True
                break

            # Purify exists but deposit is full -> end without marking complete
            if not self.appear(DEPOSIT_BOX_NOT_FULL, interval=1):
                completed = False
                break

            purify_ready = PURIFY.match_color(self.device.image, threshold=30)
            if purify_ready and self.appear_then_click(PURIFY, interval=2):
                continue

            # Reward tier check
            if self.appear(REWARDS_TIER_A, interval=1):
                if self.appear_then_click(CUSTODY, interval=2):
                    continue
            elif self.appear(REWARDS_TIER_B, interval=1):
                # Below threshold, skip custody
                continue

            if self.ui_additional():
                timeout.reset()
                continue
            if self.handle_network_error():
                timeout.reset()
                continue

        return completed

    def run_monthly(self) -> bool:
        if not self._enter_sanctuary():
            return False
        if not self._back_to_sanctuary():
            return False
        if not self._enter_monthly():
            return False

        completed = self._monthly_purify()
        return completed

    # Default entry
    def run(self) -> bool:
        ok = self.run_daily()
        if not ok:
            return False
        ok = self.run_weekly()
        if not ok:
            return False
        return self.run_monthly()
