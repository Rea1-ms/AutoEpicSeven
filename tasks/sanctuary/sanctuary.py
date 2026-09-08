"""
Epic Seven 圣域模块

最小化验证路线：
    MAIN_GOTO_SANCTUARY -> SANCTUARY_CHECK
    日常 / 周常 / 月常 分开进入与处理
"""
import datetime
import re

from module.base.timer import Timer
from module.config.utils import get_server_next_monday_update, get_server_next_month_update, get_server_next_update
from module.logger import logger
from module.ocr.ocr import Duration, OcrWhiteLetterOnComplexBackground
from tasks.base.page import page_sanctuary
from tasks.base.ui import UI
from tasks.mission_reward.scheduling import should_schedule_mission_reward
from tasks.sanctuary.assets.assets_sanctuary import (
    ALCHEMISTS_TOWER,
    ALCHEMISTS_TOWER_CHECK,
    FOREST_OF_ELVES,
)
from tasks.sanctuary.assets.assets_sanctuary_forest_of_elves import (
    ALTAR_OF_GROWTH,
    CARE,
    CLAIM_REWARDS,
    OCR_CARE_TIME,
    OCR_FLOWER_TIME,
    OCR_MOROGORA_TIME,
    OCR_PENGUIN_TIME,
)
from tasks.sanctuary.monthly import SanctuaryMonthlyMixin


class OcrForestDuration(OcrWhiteLetterOnComplexBackground, Duration):
    def after_process(self, result):
        result = Duration.after_process(self, result)
        result = result.replace(" ", "")
        result = result.replace("O", "0").replace("o", "0")
        result = result.replace("I", "1").replace("l", "1")

        match = re.search(r"(\d{1,3}:\d{2}:\d{2})", result)
        if match:
            return match.group(1)

        return result

    def format_result(self, result: str) -> datetime.timedelta:
        match = re.match(r"^\s*(\d{1,3})\s*:\s*(\d{2})\s*:\s*(\d{2})\s*$", result)
        if match:
            hours = int(match.group(1))
            minutes = int(match.group(2))
            seconds = int(match.group(3))
            return datetime.timedelta(hours=hours, minutes=minutes, seconds=seconds)

        return Duration.format_result(self, result)


class Sanctuary(SanctuaryMonthlyMixin, UI):
    """
    圣域任务
    """
    CLAIM_SETTLE_Y_TOLERANCE = 8
    DAILY_FOREST_DELAY_BUFFER_SECONDS = 10
    DAILY_FOREST_MAX_SECONDS = 168 * 60 * 60 + 59 * 60 + 59
    DAILY_FOREST_ZERO_SKIP_TIMERS = {"Care"}

    @staticmethod
    def _should_schedule_mission_reward_after_daily(claimed_any: bool) -> bool:
        return bool(claimed_any)

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

    def _ocr_lang(self) -> str:
        lang = getattr(self.config, "Emulator_GameLanguage", "cn")
        if lang in ("auto", "", None):
            return "cn"
        if lang in ("cn", "global_cn", "zh", "zh_cn"):
            return "cn"
        if lang in ("en", "global_en", "en_us"):
            return "en"
        return "cn"

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

    def _daily_claim_rewards(self) -> bool:
        logger.info("Daily: claim rewards")
        timeout = Timer(20, count=40).start()
        no_action_confirm = Timer(2, count=6).start()
        claimed_any = False
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
                claimed_any = True
                self._wait_daily_claim_settle()
                timeout.reset()
                no_action_confirm.reset()
                continue

            matches = CLAIM_REWARDS.match_multi_template(self.device.image, threshold=20)
            if matches:
                # One-by-one claim is more stable than batch clicking.
                target = sorted(matches, key=lambda x: x.area[1])[0]
                self.device.click(target)
                claimed_any = True
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

        return claimed_any

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

    @staticmethod
    def _is_valid_daily_forest_duration(remain: datetime.timedelta) -> bool:
        seconds = int(remain.total_seconds())
        return 0 < seconds <= Sanctuary.DAILY_FOREST_MAX_SECONDS

    def _ocr_daily_forest_remaining(self) -> dict[str, datetime.timedelta]:
        lang = self._ocr_lang()
        buttons = (
            ("Penguin", OCR_PENGUIN_TIME),
            ("Flower", OCR_FLOWER_TIME),
            ("Morogora", OCR_MOROGORA_TIME),
            ("Care", OCR_CARE_TIME),
        )
        results: dict[str, datetime.timedelta] = {}

        for name, button in buttons:
            remain = OcrForestDuration(button, lang=lang, name=f"Forest{name}Duration").ocr_single_line(
                self.device.image
            )
            if name in self.DAILY_FOREST_ZERO_SKIP_TIMERS and int(remain.total_seconds()) == 0:
                logger.info(f"Daily forest duration skipped: {name}=0:00:00 (weekly care exhausted)")
                continue
            if self._is_valid_daily_forest_duration(remain):
                logger.attr(f"Forest{name}Remain", str(remain))
                results[name] = remain
            else:
                logger.warning(f"Daily forest duration OCR invalid: {name}={remain}")

        return results

    def _daily_next_run_target(self) -> datetime.datetime | None:
        remains = self._ocr_daily_forest_remaining()
        return self._daily_next_run_target_from_remains(remains)

    def _daily_next_run_target_from_remains(
            self,
            remains: dict[str, datetime.timedelta],
    ) -> datetime.datetime | None:
        if not remains:
            logger.warning("Daily forest duration OCR failed for all four areas")
            return None

        name, remain = min(remains.items(), key=lambda item: item[1])
        target = datetime.datetime.now() + remain + datetime.timedelta(seconds=self.DAILY_FOREST_DELAY_BUFFER_SECONDS)
        logger.info(
            f"Daily forest delay to next ready slot: {name}={remain}, "
            f"buffer={self.DAILY_FOREST_DELAY_BUFFER_SECONDS}s, target={target.replace(microsecond=0)}"
        )
        return target.replace(microsecond=0)

    def run_daily(self) -> bool:
        self._daily_delay_target = None
        self._daily_claimed_any = False
        if not self._enter_sanctuary():
            return False
        if not self._enter_daily():
            return False
        self._daily_claimed_any = self._daily_claim_rewards()
        self._daily_delay_target = self._daily_next_run_target()
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

    def _ensure_app_running(self):
        if not self.device.app_is_running():
            from tasks.login.login import Login
            Login(self.config, device=self.device).app_start()

    def run_daily_task(self) -> bool:
        self._ensure_app_running()
        success = self.run_daily()
        if success and self._should_schedule_mission_reward_after_daily(
            getattr(self, "_daily_claimed_any", False)
        ) and should_schedule_mission_reward(self.config):
            self.config.task_call("MissionReward", force_call=False)
        target = getattr(self, "_daily_delay_target", None)
        if success and target is not None:
            self.config.task_delay(target=target)
        else:
            self.config.task_delay(target=get_server_next_update(self.config.Scheduler_ServerUpdate))
        return success

    def run_weekly_task(self) -> bool:
        self._ensure_app_running()
        success = self.run_weekly()
        self.config.task_delay(target=get_server_next_monday_update(self.config.Scheduler_ServerUpdate))
        return success

    def run_monthly_task(self) -> bool:
        self._ensure_app_running()
        success = self.run_monthly()

        monthly_status = getattr(self, "_monthly_status", self.MONTHLY_STATUS_FAILED)
        if monthly_status == self.MONTHLY_STATUS_CLAIMED:
            self.config.task_delay(target=get_server_next_month_update(self.config.Scheduler_ServerUpdate))
        elif monthly_status in (self.MONTHLY_STATUS_FULL, self.MONTHLY_STATUS_EXHAUSTED):
            self.config.task_delay(target=get_server_next_monday_update(self.config.Scheduler_ServerUpdate))
        else:
            self.config.task_delay(target=get_server_next_update(self.config.Scheduler_ServerUpdate))
        return success
