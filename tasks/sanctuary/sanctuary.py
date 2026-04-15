"""
Epic Seven 圣域模块

最小化验证路线：
    MAIN_GOTO_SANCTUARY -> SANCTUARY_CHECK
    日常 / 周常 / 月常 分开进入与处理
"""
import datetime
import re

from module.base.button import ClickButton
from module.base.timer import Timer
from module.config.utils import get_os_next_reset, get_server_next_monday_update, get_server_next_update
from module.exception import ScriptError
from module.logger import logger
from module.ocr.ocr import Digit, DigitCounter, Duration, Ocr, OcrWhiteLetterOnComplexBackground
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
    OCR_CARE_TIME,
    OCR_FLOWER_TIME,
    OCR_MOROGORA_TIME,
    OCR_PENGUIN_TIME,
)
from tasks.sanctuary.assets.assets_sanctuary_heart_of_eulerbis import (
    ALREADY_STORED,
    PURIFY,
    DEPOSIT_BOX_NOT_FULL,
    LEVEL_UP,
    OCR_HEART_LEVEL,
    OCR_PURIFY_TIMES_FULL,
    OCR_PURIFY_TIMES_NOT_FULL,
    REWARDS_TIER_A,
    REWARDS_TIER_B,
    REWARDS_TIER_S,
    CUSTODY,
)


class OcrPurifyTimes(DigitCounter):
    def after_process(self, result):
        result = super().after_process(result)
        result = result.replace("O", "0").replace("o", "0")
        result = result.replace("I", "1").replace("l", "1")
        result = result.replace("／", "/")
        return result

    def format_result(self, result) -> tuple[int, int, int]:
        # Keep parser quiet when OCR range temporarily captures unrelated texts (e.g. "净化").
        result = super().after_process(result)
        found = re.search(r'(\d+)\s*/\s*(\d+)', result)
        if not found:
            return 0, 0, 0

        current, total = int(found.group(1)), int(found.group(2))
        return current, total - current, total


class OcrRewardTier(Ocr):
    def after_process(self, result):
        result = super().after_process(result)
        result = result.upper().replace(" ", "")
        result = result.replace("Ａ", "A").replace("Ｂ", "B").replace("Ｓ", "S")
        result = result.replace("5", "S").replace("$", "S")
        return result

    def format_result(self, result):
        if "SSS" in result:
            return "SSS"
        if "SS" in result:
            return "SS"
        if "S" in result:
            return "S"
        if "A" in result:
            return "A"
        if "B" in result:
            return "B"
        return ""


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


class Sanctuary(UI):
    """
    圣域任务
    """
    CLAIM_SETTLE_Y_TOLERANCE = 8
    REWARD_TIER_ORDER = ["B", "A", "S", "SS", "SSS"]
    HEART_MAX_LEVEL = 11
    MONTHLY_OCR_INTERVAL_SECONDS = 0.8
    MONTHLY_PURIFY_CLICK_INTERVAL_SECONDS = 1
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

    def _ocr_lang(self) -> str:
        lang = getattr(self.config, "Emulator_GameLanguage", "cn")
        if lang in ("auto", "", None):
            return "cn"
        if lang in ("cn", "global_cn", "zh", "zh_cn"):
            return "cn"
        if lang in ("en", "global_en", "en_us"):
            return "en"
        return "cn"

    def _heart_level_max_tier(self, level: int) -> str:
        if level <= 1:
            return "A"
        if level <= 3:
            return "S"
        if level <= 5:
            return "SS"
        return "SSS"

    def _heart_level_is_max(self, level: int | None) -> bool:
        return level is not None and level >= self.HEART_MAX_LEVEL

    def _resolve_monthly_target_tier(self, heart_level: int | None) -> str:
        tier = self.config.SanctuaryMonthly_RewardTier
        if tier in ("A", "B", "S"):
            return tier

        if heart_level is None:
            logger.warning("Heart level OCR failed, fallback monthly reward tier to A")
            return "A"

        max_tier = self._heart_level_max_tier(heart_level)
        max_index = self.REWARD_TIER_ORDER.index(max_tier)
        if tier == "MaxMinus1":
            target_index = max(max_index - 1, 0)
        else:
            target_index = max(max_index - 2, 0)

        return self.REWARD_TIER_ORDER[target_index]

    def _tier_reached(self, current: str | None, target: str) -> bool:
        if current not in self.REWARD_TIER_ORDER:
            return False
        return self.REWARD_TIER_ORDER.index(current) >= self.REWARD_TIER_ORDER.index(target)

    def _sync_monthly_target_tier_after_level_up(
            self,
            heart_level: int | None,
            target_tier: str | None,
    ) -> tuple[int | None, str | None]:
        """
        Update cached heart level after a successful LEVEL_UP click.

        When reward tier uses relative modes (MaxMinus1/2), the effective
        custody target must move together with the new heart level.
        """
        if heart_level is None:
            logger.info("Monthly heart level-up detected, re-read heart level on next loop")
            return None, None

        next_level = min(heart_level + 1, self.HEART_MAX_LEVEL)
        if next_level != heart_level:
            logger.info(f"Heart level up: {heart_level} -> {next_level}")
        heart_level = next_level

        if self.config.SanctuaryMonthly_RewardTier in ("MaxMinus1", "MaxMinus2"):
            next_target_tier = self._resolve_monthly_target_tier(heart_level)
            if next_target_tier != target_tier:
                logger.info(
                    f"Monthly reward target tier synced after level up: {target_tier} -> {next_target_tier}"
                )
            target_tier = next_target_tier

        return heart_level, target_tier

    def _ocr_heart_level(self, level_ocr: Digit) -> int | None:
        level = level_ocr.ocr_single_line(self.device.image)
        if 1 <= level <= 20:
            logger.attr("HeartLevel", str(level))
            return level

        logger.warning(f"Heart level OCR invalid: level={level}")
        return None

    def _detect_current_reward_tier(self, tier_ocr: OcrRewardTier) -> str | None:
        if self.appear(REWARDS_TIER_S):
            return "S"
        if self.appear(REWARDS_TIER_A):
            return "A"
        if self.appear(REWARDS_TIER_B):
            return "B"

        tier = tier_ocr.ocr_single_line(self.device.image)
        if tier in ("SS", "SSS"):
            logger.attr("RewardTierOCR", tier)
            raise ScriptError(
                f"Detected reward tier {tier}, but only A/B/S templates are available. "
                f"Please capture and add REWARDS_TIER_{tier} assets first."
            )
        if tier in self.REWARD_TIER_ORDER:
            logger.attr("RewardTierOCR", tier)

        return None

    def _wait_monthly_custody_settle(self, tier_ocr: OcrRewardTier) -> bool:
        """
        Custody completion check:
            reward tier marker disappears.
        """
        timeout = Timer(5, count=15).start()
        while 1:
            self.device.screenshot()

            if timeout.reached():
                logger.warning("Monthly custody settle timeout")
                return False

            if self.handle_touch_to_close(interval=1):
                timeout.reset()
                continue
            if self.ui_additional():
                timeout.reset()
                continue
            if self.handle_network_error():
                timeout.reset()
                continue

            if self._detect_current_reward_tier(tier_ocr) is None:
                return True

    def _wait_monthly_level_up_settle(self) -> bool:
        """
        Wait until monthly level-up popup is dismissed.
        """
        timeout = Timer(5, count=15).start()
        while 1:
            self.device.screenshot()

            if timeout.reached():
                logger.warning("Monthly level up settle timeout")
                return False

            if self.handle_touch_to_close(interval=0.5):
                return True
            if self.ui_additional():
                timeout.reset()
                continue
            if self.handle_network_error():
                timeout.reset()
                continue

    def _ocr_purify_times(
            self,
            ocr_full: OcrPurifyTimes,
            ocr_not_full: OcrPurifyTimes,
            preferred_layout: str | None,
    ) -> tuple[int, int, int, str | None]:
        """
        Read purify counter from two possible layouts.

        Layouts:
            - full: OCR_PURIFY_TIMES_FULL
            - not_full: OCR_PURIFY_TIMES_NOT_FULL
        """
        def read(layout: str):
            if layout == "full":
                return ocr_full.ocr_single_line(self.device.image)
            return ocr_not_full.ocr_single_line(self.device.image)

        order = []
        if preferred_layout in ("full", "not_full"):
            order.append(preferred_layout)
            order.append("not_full" if preferred_layout == "full" else "full")
        else:
            order = ["not_full", "full"]

        best = (0, 0, 0, None)
        for layout in order:
            current, remain, total = read(layout)
            if total > 0:
                return current, remain, total, layout
            best = (current, remain, total, None)

        return best

    def _monthly_purify(self) -> str:
        """
        Returns:
            str:
                completed: monthly done (exhausted)
                full: deposit box full, retry next day
                failed: timeout/flow failure
        """
        logger.info("Monthly: purify loop")
        timeout = Timer(60, count=120).start()
        purify_missing_confirm = Timer(8, count=24).start()
        lang = self._ocr_lang()
        times_ocr_full = OcrPurifyTimes(OCR_PURIFY_TIMES_FULL, lang=lang, name="PurifyTimesOCRFull")
        times_ocr_not_full = OcrPurifyTimes(OCR_PURIFY_TIMES_NOT_FULL, lang=lang, name="PurifyTimesOCRNotFull")
        level_ocr = Digit(OCR_HEART_LEVEL, lang=lang, name="HeartLevelOCR")
        tier_ocr = OcrRewardTier(ClickButton(REWARDS_TIER_A.search, name="OCR_REWARD_TIER"), lang=lang,
                                 name="RewardTierOCR")
        heart_level = None
        target_tier = None
        level_up_check_enabled = True
        already_stored_clear_confirm = 0
        times_layout = None
        times_current = 0
        times_total = 0
        last_times_current = None
        times_ocr_timer = Timer(self.MONTHLY_OCR_INTERVAL_SECONDS, count=0).start()

        while 1:
            self.device.screenshot()

            if timeout.reached():
                logger.warning("Monthly purify timeout")
                return "failed"

            if self.handle_touch_to_close(interval=1):
                timeout.reset()
                continue
            if self.ui_additional():
                timeout.reset()
                continue
            if self.handle_network_error():
                timeout.reset()
                continue

            if times_total <= 0 or times_ocr_timer.reached():
                read_current, _, read_total, read_layout = self._ocr_purify_times(
                    times_ocr_full,
                    times_ocr_not_full,
                    preferred_layout=times_layout,
                )
                times_ocr_timer.reset()
                if read_total > 0:
                    times_current = read_current
                    times_total = read_total
                    times_layout = read_layout
                    logger.attr("PurifyTimes", f"{times_current}/{times_total} ({times_layout})")
                    if last_times_current is None:
                        last_times_current = times_current
                    elif times_current < last_times_current:
                        logger.info(f"Monthly purify progressed: {last_times_current} -> {times_current}")
                        # Continuous PURIFY clicks are expected while counter is decreasing.
                        self.device.click_record_clear()
                        last_times_current = times_current
                    elif times_current > last_times_current:
                        # OCR jitter or layout switch, accept new baseline.
                        last_times_current = times_current
                    if times_current <= 0:
                        logger.info("Monthly purify exhausted by OCR counter")
                        return "completed"

            purify_luma = PURIFY.match_template_luma(self.device.image)
            if not purify_luma:
                # PURIFY may be temporarily blocked by reward/weekly overlay. Don't mark completed directly.
                if purify_missing_confirm.reached():
                    raise ScriptError(
                        "PURIFY not detected for too long while counter is not exhausted. "
                        "Likely covered by overlay. Please capture exhausted-state asset/check."
                    )
                continue
            purify_missing_confirm.reset()

            purify_ready = PURIFY.match_template_color(self.device.image)
            if not purify_ready:
                logger.info("Monthly purify unavailable: PURIFY is gray, treat as exhausted")
                return "completed"

            # Purify exists and clickable but deposit is full -> end without marking complete
            if not self.appear(DEPOSIT_BOX_NOT_FULL):
                logger.info("Monthly purify ended: deposit box full")
                return "full"

            if target_tier is None:
                heart_level = self._ocr_heart_level(level_ocr)
                target_tier = self._resolve_monthly_target_tier(heart_level)
                logger.info(f"Monthly reward target tier: {target_tier} (heart_level={heart_level})")
                if self._heart_level_is_max(heart_level):
                    level_up_check_enabled = False
                    logger.info("Monthly heart already max level, skip future LEVEL_UP scans")

            if level_up_check_enabled and self.appear_then_click(LEVEL_UP, interval=2):
                if self._wait_monthly_level_up_settle():
                    heart_level, target_tier = self._sync_monthly_target_tier_after_level_up(
                        heart_level=heart_level,
                        target_tier=target_tier,
                    )
                    if self._heart_level_is_max(heart_level):
                        level_up_check_enabled = False
                        logger.info("Monthly heart reached max level, disable further LEVEL_UP scans")
                else:
                    heart_level = None
                    target_tier = None
                    level_up_check_enabled = True
                times_ocr_timer.clear()
                timeout.reset()
                already_stored_clear_confirm = 0
                continue

            current_tier = self._detect_current_reward_tier(tier_ocr)
            if self._tier_reached(current_tier, target_tier):
                if self.appear(ALREADY_STORED, similarity=0.8):
                    already_stored_clear_confirm = 0
                    logger.info("Monthly already-stored indicator detected, wait before custody check")
                    timeout.reset()
                    continue

                already_stored_clear_confirm += 1
                if already_stored_clear_confirm < 2:
                    # ALREADY_STORED is flickery; require a short stable-missing window.
                    continue

                if self.appear(CUSTODY, interval=1):
                    if CUSTODY.match_color(self.device.image, threshold=10):
                        if self.appear_then_click(CUSTODY, interval=2):
                            self._wait_monthly_custody_settle(tier_ocr)
                            if not self.appear(DEPOSIT_BOX_NOT_FULL):
                                logger.info("Monthly purify ended: deposit box full after custody")
                                return "full"
                            timeout.reset()
                            already_stored_clear_confirm = 0
                            continue
                    else:
                        logger.info("Monthly custody unavailable (already stored), continue purify")
            else:
                already_stored_clear_confirm = 0

            if self.appear_then_click(PURIFY, interval=self.MONTHLY_PURIFY_CLICK_INTERVAL_SECONDS):
                times_ocr_timer.clear()
                timeout.reset()
                continue

        return "failed"

    def run_monthly(self) -> bool:
        self._monthly_status = "failed"
        if not self._enter_sanctuary():
            return False
        if not self._back_to_sanctuary():
            return False
        if not self._enter_monthly():
            return False

        monthly_status = self._monthly_purify()
        self._monthly_status = monthly_status
        self._back_to_sanctuary()
        if monthly_status == "failed":
            return False
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
        ):
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

        monthly_status = getattr(self, "_monthly_status", "failed")
        if monthly_status == "completed":
            self.config.task_delay(target=get_os_next_reset())
        elif monthly_status == "full":
            # Deposit box full: retry with next daily sanctuary cycle.
            self.config.task_delay(target=get_server_next_update(self.config.Scheduler_ServerUpdate))
        else:
            # Failed monthly flow should retry next day.
            self.config.task_delay(target=get_server_next_update(self.config.Scheduler_ServerUpdate))
        return success
