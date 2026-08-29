"""Heart of Eulerbis monthly sanctuary workflow."""
import re

from module.base.button import ClickButton
from module.base.timer import Timer
from module.exception import ScriptError
from module.logger import logger
from module.ocr.ocr import Digit, DigitCounter, Ocr
from tasks.sanctuary.assets.assets_sanctuary import HEART_OF_EULERBIS, HEART_OF_EULERBIS_CHECK
from tasks.sanctuary.assets.assets_sanctuary_heart_of_eulerbis import (
    ALREADY_STORED,
    CUSTODY,
    DEPOSIT_BOX_NOT_FULL,
    LEVEL_UP,
    OCR_HEART_LEVEL,
    OCR_PURIFY_TIMES_FULL,
    OCR_PURIFY_TIMES_NOT_FULL,
    PURIFY,
    REWARDS_TIER_A,
    REWARDS_TIER_B,
    REWARDS_TIER_S,
    REWARDS_TIER_SS,
    STATE_MONTHLY_CLAIMED,
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


class SanctuaryMonthlyMixin:
    """Monthly sanctuary behavior mixed into the public Sanctuary task."""

    MONTHLY_REWARD_TIER_SMART = "Smart"
    REWARD_TIER_ORDER = ["B", "A", "S", "SS", "SSS"]
    HEART_MAX_LEVEL = 11
    MONTHLY_OCR_INTERVAL_SECONDS = 0.8
    MONTHLY_PURIFY_CLICK_INTERVAL_SECONDS = 1
    MONTHLY_STATUS_CLAIMED = "claimed"
    MONTHLY_STATUS_FULL = "full"
    MONTHLY_STATUS_EXHAUSTED = "exhausted"
    MONTHLY_STATUS_FAILED = "failed"

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
        if tier in ("A", "B", "S", "SS"):
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
        if self.appear(REWARDS_TIER_SS):
            return "SS"
        if self.appear(REWARDS_TIER_S):
            return "S"
        if self.appear(REWARDS_TIER_A):
            return "A"
        if self.appear(REWARDS_TIER_B):
            return "B"

        tier = tier_ocr.ocr_single_line(self.device.image)
        if tier == "SSS":
            logger.attr("RewardTierOCR", tier)
            raise ScriptError(
                f"Detected reward tier {tier}, but only A/B/S/SS templates are available. "
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
        """Wait until monthly level-up popup is dismissed."""
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

    def _is_monthly_claimed(self) -> bool:
        """
        Check whether the monthly reward has already been claimed.

        This state has the highest priority for monthly scheduling:
        once claimed, future weekly refreshes are irrelevant until next month.
        """
        return self.appear(STATE_MONTHLY_CLAIMED)

    def _is_monthly_deposit_box_full(self) -> bool:
        """
        Detect whether the deposit box is already full.

        We currently only have a stable positive asset for the "not full" state,
        so this helper keeps the transitional negative check in one place.
        Once a dedicated "deposit box full" asset is added, only this method
        needs to change.
        """
        return not self.appear(DEPOSIT_BOX_NOT_FULL)

    def _monthly_purify_smart(self) -> str:
        """
        Purify until the game's high-value confirmation protects a new item.

        Any cancel popup is intentionally treated as the high-value signal in
        this mode. After canceling, purifying must stay blocked until custody
        finishes through the existing custody settle check. This prevents a
        delayed or dropped custody click from destroying the protected item on
        the next refresh. Deposit capacity keeps using the existing last-slot
        check.
        """
        logger.info("Monthly: smart custody loop")
        timeout = Timer(60, count=120).start()
        purify_missing_confirm = Timer(8, count=24).start()
        lang = self._ocr_lang()
        times_ocr_full = OcrPurifyTimes(OCR_PURIFY_TIMES_FULL, lang=lang, name="PurifyTimesOCRFull")
        times_ocr_not_full = OcrPurifyTimes(OCR_PURIFY_TIMES_NOT_FULL, lang=lang, name="PurifyTimesOCRNotFull")
        tier_ocr = OcrRewardTier(ClickButton(REWARDS_TIER_A.search, name="OCR_REWARD_TIER"), lang=lang,
                                 name="RewardTierOCR")
        times_layout = None
        times_current = 0
        last_times_current = None
        custody_pending = False

        while 1:
            self.device.screenshot()

            if timeout.reached():
                logger.warning("Monthly smart custody timeout")
                return self.MONTHLY_STATUS_FAILED

            if self._is_monthly_claimed():
                logger.info("Monthly reward already claimed")
                return self.MONTHLY_STATUS_CLAIMED

            if self.handle_popup_cancel(interval=2):
                custody_pending = True
                purify_missing_confirm.reset()
                timeout.reset()
                logger.info("Monthly smart custody: high-value refresh canceled")
                continue

            if self.handle_touch_to_close(interval=1):
                timeout.reset()
                continue
            if self.ui_additional():
                timeout.reset()
                continue
            if self.handle_network_error():
                timeout.reset()
                continue

            if custody_pending:
                if not PURIFY.match_template_luma(self.device.image):
                    continue

                if self._is_monthly_deposit_box_full():
                    logger.info("Monthly smart custody ended: deposit box full")
                    return self.MONTHLY_STATUS_FULL

                if CUSTODY.match_color(self.device.image, threshold=10):
                    if self.appear_then_click(CUSTODY, interval=2):
                        custody_settled = self._wait_monthly_custody_settle(tier_ocr)
                        if self._is_monthly_claimed():
                            logger.info("Monthly reward claimed after smart custody")
                            return self.MONTHLY_STATUS_CLAIMED
                        if self._is_monthly_deposit_box_full():
                            logger.info("Monthly smart custody ended: deposit box full after custody")
                            return self.MONTHLY_STATUS_FULL
                        if not custody_settled:
                            logger.warning("Monthly smart custody not settled, keep refresh blocked")
                            continue
                        logger.info("Monthly smart custody stored protected item")
                        custody_pending = False
                        timeout.reset()
                        continue

                continue

            read_current, _, read_total, read_layout = self._ocr_purify_times(
                times_ocr_full,
                times_ocr_not_full,
                preferred_layout=times_layout,
            )
            if read_total <= 0:
                continue

            times_current = read_current
            times_layout = read_layout
            logger.attr("PurifyTimes", f"{times_current}/{read_total} ({times_layout})")
            if last_times_current is None:
                last_times_current = times_current
            elif times_current < last_times_current:
                logger.info(f"Monthly smart purify progressed: {last_times_current} -> {times_current}")
                self.device.click_record_clear()
                last_times_current = times_current
            elif times_current > last_times_current:
                last_times_current = times_current

            if times_current <= 0:
                logger.info("Monthly smart purify exhausted before monthly reward is claimed")
                return self.MONTHLY_STATUS_EXHAUSTED

            purify_luma = PURIFY.match_template_luma(self.device.image)
            if not purify_luma:
                if purify_missing_confirm.reached():
                    raise ScriptError(
                        "PURIFY not detected for too long while smart custody has remaining attempts. "
                        "Likely covered by an unhandled overlay."
                    )
                continue
            purify_missing_confirm.reset()

            if not PURIFY.match_template_color(self.device.image):
                logger.info("Monthly smart purify unavailable: PURIFY is gray")
                return self.MONTHLY_STATUS_EXHAUSTED

            if self.appear_then_click(LEVEL_UP, interval=2):
                self._wait_monthly_level_up_settle()
                timeout.reset()
                continue

            if self.appear_then_click(PURIFY, interval=self.MONTHLY_PURIFY_CLICK_INTERVAL_SECONDS):
                # The counter is read on every following frame. The warning
                # does not consume an attempt, while a successful refresh does;
                # re-reading keeps both paths tied to game state.
                timeout.reset()
                continue

        return self.MONTHLY_STATUS_FAILED

    def _monthly_purify(self) -> str:
        """
        Returns:
            str:
                claimed: monthly reward already claimed
                full: deposit box full before monthly reward is claimed
                exhausted: weekly purify times exhausted before monthly reward is claimed
                failed: timeout/flow failure
        """
        if self.config.SanctuaryMonthly_RewardTier == self.MONTHLY_REWARD_TIER_SMART:
            return self._monthly_purify_smart()

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
                return self.MONTHLY_STATUS_FAILED

            if self.handle_touch_to_close(interval=1):
                timeout.reset()
                continue
            if self.ui_additional():
                timeout.reset()
                continue
            if self.handle_network_error():
                timeout.reset()
                continue

            if self._is_monthly_claimed():
                logger.info("Monthly reward already claimed")
                return self.MONTHLY_STATUS_CLAIMED

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
                        logger.info("Monthly purify exhausted by OCR counter before monthly reward is claimed")
                        return self.MONTHLY_STATUS_EXHAUSTED

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
                logger.info("Monthly purify unavailable: PURIFY is gray, treat as weekly times exhausted")
                return self.MONTHLY_STATUS_EXHAUSTED

            # Purify exists and clickable but deposit is full -> end without marking complete
            if self._is_monthly_deposit_box_full():
                logger.info("Monthly purify ended: deposit box full")
                return self.MONTHLY_STATUS_FULL

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
                            if self._is_monthly_claimed():
                                logger.info("Monthly reward claimed after custody")
                                return self.MONTHLY_STATUS_CLAIMED
                            if self._is_monthly_deposit_box_full():
                                logger.info("Monthly purify ended: deposit box full after custody")
                                return self.MONTHLY_STATUS_FULL
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

        return self.MONTHLY_STATUS_FAILED

    def run_monthly(self) -> bool:
        self._monthly_status = self.MONTHLY_STATUS_FAILED
        if not self._enter_sanctuary():
            return False
        if not self._back_to_sanctuary():
            return False
        if not self._enter_monthly():
            return False

        monthly_status = self._monthly_purify()
        self._monthly_status = monthly_status
        self._back_to_sanctuary()
        if monthly_status == self.MONTHLY_STATUS_FAILED:
            return False
        return True
