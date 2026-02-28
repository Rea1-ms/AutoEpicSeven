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
    CLAIM_SETTLE_Y_TOLERANCE = 8

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
        timeout = Timer(20, count=40).start()
        no_action_confirm = Timer(2, count=6).start()
        self.interval_clear(CARE)
        while 1:
            self.device.screenshot()

            if timeout.reached():
                break

            if self.handle_touch_to_close():
                timeout.reset()
                no_action_confirm.reset()
                continue

            if self._care_ready(interval=1):
                self.device.click(CARE)
                self._wait_daily_claim_settle()
                timeout.reset()
                no_action_confirm.reset()
                continue

            matches = CLAIM_REWARDS.match_multi_template(self.device.image, threshold=20)
            if matches:
                # One-by-one claim is more stable than batch clicking.
                target = sorted(matches, key=lambda x: x.area[1])[0]
                self.device.click(target)
                self._wait_daily_claim_settle()
                timeout.reset()
                no_action_confirm.reset()
                continue

            if self.ui_additional():
                timeout.reset()
                no_action_confirm.reset()
                continue
            if self.handle_network_error():
                timeout.reset()
                no_action_confirm.reset()
                continue

            if no_action_confirm.reached():
                break

    def _care_ready(self, interval=1) -> bool:
        """
        CARE uses luma + color double check to avoid overlay/shadow false positives.
        """
        self.device.stuck_record_add(CARE)

        if interval and not self.interval_is_reached(CARE, interval=interval):
            return False

        appear = False
        if CARE.match_template_luma(self.device.image, similarity=0.8):
            if CARE.match_color(self.device.image, threshold=30):
                appear = True

        if appear and interval:
            self.interval_reset(CARE, interval=interval)

        return appear

    def _daily_claim_signature(self) -> tuple[int, int]:
        matches = CLAIM_REWARDS.match_multi_template(self.device.image, threshold=20)
        if not matches:
            return 0, -1
        target = sorted(matches, key=lambda x: x.area[1])[0]
        y_center = int((target.area[1] + target.area[3]) / 2)
        return len(matches), y_center

    def _wait_daily_claim_settle(self) -> bool:
        """
        Wait for claim list to stop moving after click/touch-to-close transitions.
        """
        timeout = Timer(2, count=6).start()
        stable_count = 0
        last_signature = None

        while 1:
            self.device.screenshot()

            if timeout.reached():
                return False

            if self.ui_additional():
                timeout.reset()
                stable_count = 0
                last_signature = None
                continue
            if self.handle_network_error():
                timeout.reset()
                stable_count = 0
                last_signature = None
                continue
            if self.handle_touch_to_close(interval=1):
                timeout.reset()
                stable_count = 0
                last_signature = None
                continue

            signature = self._daily_claim_signature()
            if last_signature is None:
                stable_count = 1
            elif signature[0] == last_signature[0] and (
                    signature[1] < 0 or abs(signature[1] - last_signature[1]) <= self.CLAIM_SETTLE_Y_TOLERANCE):
                stable_count += 1
            else:
                stable_count = 1
            last_signature = signature

            if stable_count >= 2:
                return True

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

            if self.appear_then_click(ALCHEMISTS_TOWER, interval=1):
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
            tier = self.config.Sanctuary_RewardTier
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

        run_daily = self.config.Sanctuary_Daily
        run_weekly = self.config.Sanctuary_Weekly
        run_monthly = self.config.Sanctuary_Monthly

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
