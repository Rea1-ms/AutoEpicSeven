"""
Epic Seven 圣域模块

最小化验证路线：
    MAIN_GOTO_SANCTUARY -> SANCTUARY_CHECK
    日常 / 周常 / 月常 分开进入与处理
"""
from module.base.timer import Timer
from module.config.utils import get_os_next_reset, get_server_next_monday_update, get_server_next_update
from module.logger import logger
from tasks.base.page import page_sanctuary
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
        if not hasattr(self.device, "image") or self.device.image is None:
            self.device.screenshot()
        self.ui_goto(page_sanctuary)
        return True

    def _back_to_sanctuary(self) -> bool:
        if not hasattr(self.device, "image") or self.device.image is None:
            self.device.screenshot()
        self.ui_goto(page_sanctuary)
        return True

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
                # 领取后常有弹窗，立刻处理一次
                self.device.screenshot()
                if self.handle_touch_to_close():
                    timeout.reset()
                    continue
                timeout.reset()
                continue
            break

    def run_daily(self) -> bool:
        if not self._enter_sanctuary():
            return False
        if not self._enter_daily():
            return False
        self._daily_claim_rewards()
        self._back_to_sanctuary()
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
        self._back_to_sanctuary()
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
            tier = getattr(self.config, "Sanctuary_RewardTier", "A")
            if tier == "A":
                if self.appear(REWARDS_TIER_A, interval=1):
                    if self.appear_then_click(CUSTODY, interval=2):
                        continue
            else:
                if self.appear(REWARDS_TIER_A, interval=1) or self.appear(REWARDS_TIER_B, interval=1):
                    if self.appear_then_click(CUSTODY, interval=2):
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
        self._back_to_sanctuary()
        return completed

    # Default entry
    def run(self) -> bool:
        if not self.device.app_is_running():
            from tasks.login.login import Login
            Login(self.config, device=self.device).app_start()

        run_daily = getattr(self.config, "Sanctuary_Daily", True)
        run_weekly = getattr(self.config, "Sanctuary_Weekly", True)
        run_monthly = getattr(self.config, "Sanctuary_Monthly", True)

        if not any([run_daily, run_weekly, run_monthly]):
            logger.warning("Sanctuary: all sub tasks disabled")
            self.config.task_delay(server_update=True)
            return True

        success = True
        if run_daily:
            success = self.run_daily() and success
        if run_weekly:
            success = self.run_weekly() and success
        if run_monthly:
            success = self.run_monthly() and success

        targets = []
        if run_daily:
            targets.append(get_server_next_update(self.config.Scheduler_ServerUpdate))
        if run_weekly:
            targets.append(get_server_next_monday_update(self.config.Scheduler_ServerUpdate))
        if run_monthly:
            targets.append(get_os_next_reset())
        if targets:
            self.config.task_delay(target=targets)
        return success
