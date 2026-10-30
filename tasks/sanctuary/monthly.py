"""Heart of Eulerbis monthly sanctuary workflow."""
import re

from module.base.button import ClickButton
from module.base.timer import Timer
from module.exception import ScriptError
from module.logger import logger
from module.ocr.ocr import Digit, DigitCounter, Ocr
from tasks.base.assets.assets_base_popup import POPUP_CANCEL, TOUCH_TO_CLOSE
from tasks.sanctuary.assets.assets_sanctuary import HEART_OF_EULERBIS, HEART_OF_EULERBIS_CHECK
from tasks.sanctuary.assets.assets_sanctuary_heart_of_eulerbis import (
    ALREADY_STORED,  # noqa: F401 - retained for historical replay asset imports
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
from tasks.sanctuary.monthly_deposit import deposit_tiers_increased, match_deposit_tiers
from tasks.sanctuary.monthly_reminder import SanctuaryMonthlyReminderMixin


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


class SanctuaryMonthlyMixin(SanctuaryMonthlyReminderMixin):
    """Monthly sanctuary behavior mixed into the public Sanctuary task."""

    MONTHLY_REWARD_TIER_SMART = "Smart"
    REWARD_TIER_ORDER = ["B", "A", "S", "SS", "SSS"]
    HEART_MAX_LEVEL = 11
    MONTHLY_OCR_INTERVAL_SECONDS = 0.8
    MONTHLY_PURIFY_OCR_STABLE_FRAMES = 3
    MONTHLY_DEPOSIT_CHECK_TIMEOUT_SECONDS = 8
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

    def _wait_monthly_custody_settle(
            self,
            tier_ocr: OcrRewardTier,
            deposit_before: tuple[str | None, ...] | None = None,
    ) -> bool:
        """
        Wait for a positive stored state after clicking custody.

        A stable new deposit or the monthly claimed state confirms success.
        The deposit baseline must come from before the click. ALREADY_STORED
        is a brief duplicate-click notice, not a receipt for the first click.
        A missing tier marker can be an animation or an OCR failure; treating
        it as success would release the protected reward for another purify.
        """
        timeout = Timer(5, count=15).start()
        deposit_candidate = None
        deposit_stable_frames = 0
        while 1:
            self.device.screenshot()

            if timeout.reached():
                logger.warning("Monthly custody settle timeout")
                return False

            if self._is_monthly_claimed():
                return True
            if deposit_before is not None:
                deposit_after = match_deposit_tiers(self.device.image)
                if deposit_tiers_increased(deposit_before, deposit_after):
                    if deposit_after == deposit_candidate:
                        deposit_stable_frames += 1
                    else:
                        deposit_candidate = deposit_after
                        deposit_stable_frames = 1
                    # A single new label can be a gold flash or a transient
                    # template match. Require the exact five-slot observation
                    # on consecutive fresh screenshots before releasing purify.
                    if deposit_stable_frames >= 2:
                        logger.info(f"Monthly custody settled: deposit slots {deposit_before} -> {deposit_after}")
                        return True
                else:
                    deposit_candidate = None
                    deposit_stable_frames = 0

            if self.handle_touch_to_close(interval=1):
                deposit_candidate = None
                deposit_stable_frames = 0
                timeout.reset()
                continue
            if self.ui_additional():
                deposit_candidate = None
                deposit_stable_frames = 0
                timeout.reset()
                continue
            if self.handle_network_error():
                deposit_candidate = None
                deposit_stable_frames = 0
                timeout.reset()
                continue

    def _wait_monthly_level_up_settle(self) -> bool:
        """Retry dismissal until the monthly controls return on a fresh frame."""
        timeout = Timer(5, count=15).start()
        while 1:
            self.device.screenshot()

            if timeout.reached():
                logger.warning("Monthly level up settle timeout")
                return False

            # A dismissal request may be dropped. Neither sending it nor an
            # empty animation frame proves that the monthly page is usable.
            if not self.appear(TOUCH_TO_CLOSE) and self.appear(PURIFY):
                return True
            if self.handle_touch_to_close(interval=0.5):
                continue
            if self.ui_additional():
                continue
            if self.handle_network_error():
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

        These legacy names describe resource-counter positions, not deposit
        capacity. Both crops contain balance / single-purify cost; the balance
        may exceed the cost (400/10 is valid). Never infer box capacity here.
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

    def _is_monthly_deposit_box_full(self) -> bool | None:
        """
        Return True for five occupied slots, False for a visible free slot.

        Missing free-slot or tier markers cannot prove capacity. Keep None
        distinct from False so callers never purify on an unverified box.
        """
        if self.appear(DEPOSIT_BOX_NOT_FULL, interval=0):
            return False
        tiers = match_deposit_tiers(self.device.image)
        if tiers != getattr(self, "_monthly_last_deposit_tiers", None):
            logger.attr("MonthlyDepositTiers", f"{sum(tier is not None for tier in tiers)}/5 {tiers}")
            self._monthly_last_deposit_tiers = tiers
        if all(tier is not None for tier in tiers):
            return True
        return None

    def _monthly_deposit_box_ready(self, missing_confirm: Timer) -> bool:
        """Require a positively recognized free slot before purify or custody."""
        if self._is_monthly_deposit_box_full() is False:
            missing_confirm.clear()
            return True

        # Start once per uninterrupted unknown state. Do not reset this deadline
        # on retries or use the OCR layout to bypass it. Explicit popup handling
        # clears the timer because a covered slot is not a capacity observation.
        missing_confirm.start()
        if missing_confirm.reached():
            raise ScriptError(
                "Monthly deposit capacity is unknown: neither a free slot nor "
                "five occupied slots were detected. A deposit tier asset may be missing."
            )
        return False

    def _monthly_purify_smart(self) -> str:
        """
        Store A-tier rewards at the lowest heart level, otherwise use popups.

        Any cancel popup is intentionally treated as the high-value signal in
        this mode. After canceling, purifying must stay blocked until custody
        finishes through the existing custody settle check. This prevents a
        delayed or dropped custody click from destroying the protected item on
        the next refresh. Five recognized deposit tiers confirm a full box;
        only the existing free-slot marker authorizes further purification.
        A level whose maximum reward is A never gets the high-value warning.
        In that case stable level/tier observations also protect the reward,
        and must enter the same custody-pending state before another refresh.
        """
        logger.info("Monthly: smart custody loop")
        timeout = Timer(60, count=120).start()
        purify_ocr_missing_confirm = Timer(8, count=24).start()
        purify_missing_confirm = Timer(8, count=24)
        deposit_missing_confirm = Timer(self.MONTHLY_DEPOSIT_CHECK_TIMEOUT_SECONDS)
        lang = self._ocr_lang()
        times_ocr_full = OcrPurifyTimes(OCR_PURIFY_TIMES_FULL, lang=lang, name="PurifyTimesOCRFull")
        times_ocr_not_full = OcrPurifyTimes(OCR_PURIFY_TIMES_NOT_FULL, lang=lang, name="PurifyTimesOCRNotFull")
        level_ocr = Digit(OCR_HEART_LEVEL, lang=lang, name="HeartLevelOCR")
        tier_ocr = OcrRewardTier(ClickButton(REWARDS_TIER_A.search, name="OCR_REWARD_TIER"), lang=lang,
                                 name="RewardTierOCR")
        times_layout = None
        times_current = 0
        last_times_current = None
        times_ocr_candidate = None
        times_ocr_stable_frames = 0
        custody_pending = False
        custody_before_tiers = None
        custody_before_candidate = None
        custody_before_stable_frames = 0
        custody_confirmed_current = False

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
                custody_confirmed_current = False
                custody_before_candidate = None
                custody_before_stable_frames = 0
                deposit_missing_confirm.clear()
                purify_missing_confirm.clear()
                times_ocr_candidate = None
                times_ocr_stable_frames = 0
                purify_ocr_missing_confirm.reset()
                timeout.reset()
                logger.info("Monthly smart custody: high-value refresh canceled")
                continue

            # POPUP_CANCEL may remain visible for several frames after the
            # click. During the click interval handle_popup_cancel() skips its
            # template check. Keep this transition inside the popup state until
            # the cancel asset is actually gone; the obscured box and buttons
            # are not actionable capacity or location observations.
            if custody_pending and self.appear(POPUP_CANCEL):
                custody_before_candidate = None
                custody_before_stable_frames = 0
                deposit_missing_confirm.clear()
                purify_missing_confirm.clear()
                continue

            if self.handle_touch_to_close(interval=1):
                custody_before_candidate = None
                custody_before_stable_frames = 0
                deposit_missing_confirm.clear()
                purify_missing_confirm.clear()
                times_ocr_candidate = None
                times_ocr_stable_frames = 0
                purify_ocr_missing_confirm.reset()
                timeout.reset()
                continue
            if self.ui_additional():
                custody_before_candidate = None
                custody_before_stable_frames = 0
                deposit_missing_confirm.clear()
                purify_missing_confirm.clear()
                times_ocr_candidate = None
                times_ocr_stable_frames = 0
                purify_ocr_missing_confirm.reset()
                timeout.reset()
                continue
            if self.handle_network_error():
                custody_before_candidate = None
                custody_before_stable_frames = 0
                deposit_missing_confirm.clear()
                purify_missing_confirm.clear()
                times_ocr_candidate = None
                times_ocr_stable_frames = 0
                purify_ocr_missing_confirm.reset()
                timeout.reset()
                continue

            if self._is_monthly_deposit_box_full() is True:
                logger.info("Monthly deposit box full: five occupied slots; organize the box before continuing")
                return self.MONTHLY_STATUS_FULL

            if custody_pending:
                custody_settled = False
                # A successful custody click may clear the current reward and
                # disable its button without ever showing ALREADY_STORED. Keep
                # the first pre-click baseline across failed/late confirmations
                # and examine the receipt before requiring an enabled button.
                # Replacing this baseline on retries would lose a late deposit.
                if custody_before_tiers is not None and deposit_tiers_increased(
                    custody_before_tiers, match_deposit_tiers(self.device.image)
                ):
                    custody_settled = self._wait_monthly_custody_settle(tier_ocr, custody_before_tiers)

                if not custody_settled:
                    if not self._monthly_deposit_box_ready(deposit_missing_confirm):
                        custody_before_candidate = None
                        custody_before_stable_frames = 0
                        continue
                    if not CUSTODY.match_color(self.device.image, threshold=10):
                        custody_before_candidate = None
                        custody_before_stable_frames = 0
                        continue
                    deposit_before_click = match_deposit_tiers(self.device.image)
                    if custody_before_tiers is None:
                        # Stabilize the baseline before clicking as well: a
                        # flickering old label must not disappear for one frame
                        # and then return as an apparent newly stored reward.
                        if deposit_before_click == custody_before_candidate:
                            custody_before_stable_frames += 1
                        else:
                            custody_before_candidate = deposit_before_click
                            custody_before_stable_frames = 1
                        if custody_before_stable_frames < 2:
                            continue
                    if self.appear_then_click(CUSTODY, interval=2):
                        if custody_before_tiers is None:
                            custody_before_tiers = deposit_before_click
                        custody_settled = self._wait_monthly_custody_settle(tier_ocr, custody_before_tiers)
                    else:
                        continue

                if self._is_monthly_claimed():
                    logger.info("Monthly reward claimed after smart custody")
                    return self.MONTHLY_STATUS_CLAIMED
                if not custody_settled:
                    logger.warning("Monthly smart custody not settled, keep refresh blocked")
                    continue
                logger.info("Monthly smart custody stored protected item")
                custody_pending = False
                # The current reward can remain visible, or disappear, after
                # storage. Remember this confirmed transaction until an action
                # creates another reward; otherwise low-level A protection
                # would wait forever on the now-disabled custody button.
                custody_confirmed_current = True
                custody_before_tiers = None
                custody_before_candidate = None
                custody_before_stable_frames = 0
                deposit_missing_confirm.clear()
                purify_missing_confirm.clear()
                # Never reuse resource readings across custody. The next
                # action also requires a new, independent free-slot check.
                times_layout = None
                times_ocr_candidate = None
                times_ocr_stable_frames = 0
                purify_ocr_missing_confirm.reset()
                timeout.reset()
                continue

            read_current, _, read_total, read_layout = self._ocr_purify_times(
                times_ocr_full,
                times_ocr_not_full,
                preferred_layout=times_layout,
            )
            if read_total <= 0 or read_layout not in ("full", "not_full"):
                times_ocr_candidate = None
                times_ocr_stable_frames = 0
                if purify_ocr_missing_confirm.reached():
                    raise ScriptError(
                        "Purify counter not detected for too long during smart custody. "
                        "Likely covered by an unhandled overlay."
                    )
                continue
            # The reward's gold flash temporarily recolors or hides PURIFY,
            # while the resource/cost counter normally remains readable. A
            # single OCR frame is still not sufficient because animations can
            # produce plausible digits. Require the complete value and layout
            # to agree on fresh consecutive screenshots. Every click or overlay
            # clears this candidate so two different UI states cannot combine.
            heart_level = self._ocr_heart_level(level_ocr)
            low_level = heart_level is not None and self._heart_level_max_tier(heart_level) == "A"
            current_tier = self._detect_current_reward_tier(tier_ocr) if low_level else None
            already_stored = low_level and custody_confirmed_current
            custody_enabled = low_level and CUSTODY.match_color(self.device.image, threshold=10)
            if heart_level is None or (low_level and current_tier is None and not already_stored):
                times_ocr_candidate = None
                times_ocr_stable_frames = 0
                if purify_ocr_missing_confirm.reached():
                    raise ScriptError("Heart level or low-level reward tier not detected; smart purify is blocked")
                continue
            purify_ocr_missing_confirm.reset()
            # Include the current level and reward in the existing stable-frame
            # check, not just the resource counter. A flickering A marker must
            # neither trigger custody nor be overwritten on the next frame.
            # Re-reading the level after every action also prevents level-up
            # from leaving the low-level exception enabled on a higher level.
            read_candidate = (read_current, read_total, read_layout, heart_level,
                              current_tier, already_stored, custody_enabled)
            if read_candidate == times_ocr_candidate:
                times_ocr_stable_frames += 1
            else:
                times_ocr_candidate = read_candidate
                times_ocr_stable_frames = 1
            if times_ocr_stable_frames < self.MONTHLY_PURIFY_OCR_STABLE_FRAMES:
                continue

            times_current = read_current
            times_layout = read_layout
            logger.attr("PurifyResource", f"balance={times_current}, cost={read_total}, counter_layout={times_layout}")
            if last_times_current is None:
                last_times_current = times_current
            elif times_current < last_times_current:
                logger.info(f"Monthly smart purify progressed: {last_times_current} -> {times_current}")
                self.device.click_record_clear()
                last_times_current = times_current
            elif times_current > last_times_current:
                last_times_current = times_current

            if low_level and not already_stored and self._tier_reached(current_tier, "A"):
                # Protect an eligible result even if no resources remain for
                # another purification. Once latched, only the ordinary custody
                # confirmation releases it; missing or delayed stored markers
                # must never send this reward back into the refresh branch.
                if not custody_enabled:
                    continue
                custody_pending = True
                times_ocr_candidate = None
                times_ocr_stable_frames = 0
                logger.info(f"Monthly smart custody: protect {current_tier} reward at heart level {heart_level}")
                continue

            if times_current < read_total:
                logger.info("Monthly smart purify exhausted before monthly reward is claimed")
                return self.MONTHLY_STATUS_EXHAUSTED

            if not self._monthly_deposit_box_ready(deposit_missing_confirm):
                times_ocr_candidate = None
                times_ocr_stable_frames = 0
                continue

            if self.appear_then_click(LEVEL_UP, interval=2):
                custody_confirmed_current = False
                self._wait_monthly_level_up_settle()
                times_ocr_candidate = None
                times_ocr_stable_frames = 0
                purify_ocr_missing_confirm.reset()
                purify_missing_confirm.clear()
                timeout.reset()
                continue

            # Both resource layouts allow purify. Locate the button on this
            # screenshot so its horizontal offset follows the current layout.
            # Gold effects may hide it temporarily; wait instead of clicking a
            # stale/default location or declaring the remaining balance exhausted.
            if not PURIFY.match_template_luma(self.device.image):
                purify_missing_confirm.start()
                if purify_missing_confirm.reached():
                    raise ScriptError("Monthly purify button not detected with sufficient resource balance")
                continue
            purify_missing_confirm.clear()

            if self.interval_is_reached(
                    PURIFY,
                    interval=self.MONTHLY_PURIFY_CLICK_INTERVAL_SECONDS,
            ):
                self.device.click(PURIFY)
                custody_confirmed_current = False
                self.interval_reset(
                    PURIFY,
                    interval=self.MONTHLY_PURIFY_CLICK_INTERVAL_SECONDS,
                )
                times_ocr_candidate = None
                times_ocr_stable_frames = 0
                timeout.reset()
                continue

        return self.MONTHLY_STATUS_FAILED

    def _monthly_purify(self) -> str:
        """
        Returns:
            str:
                claimed: monthly reward already claimed
                full: deposit box full before monthly reward is claimed
                exhausted: insufficient purify resources before monthly reward is claimed
                failed: timeout/flow failure
        """
        if self.config.SanctuaryMonthly_RewardTier == self.MONTHLY_REWARD_TIER_SMART:
            return self._monthly_purify_smart()

        logger.info("Monthly: purify loop")
        timeout = Timer(60, count=120).start()
        purify_missing_confirm = Timer(8, count=24).start()
        deposit_missing_confirm = Timer(self.MONTHLY_DEPOSIT_CHECK_TIMEOUT_SECONDS)
        lang = self._ocr_lang()
        times_ocr_full = OcrPurifyTimes(OCR_PURIFY_TIMES_FULL, lang=lang, name="PurifyTimesOCRFull")
        times_ocr_not_full = OcrPurifyTimes(OCR_PURIFY_TIMES_NOT_FULL, lang=lang, name="PurifyTimesOCRNotFull")
        level_ocr = Digit(OCR_HEART_LEVEL, lang=lang, name="HeartLevelOCR")
        tier_ocr = OcrRewardTier(ClickButton(REWARDS_TIER_A.search, name="OCR_REWARD_TIER"), lang=lang,
                                 name="RewardTierOCR")
        heart_level = None
        target_tier = None
        level_up_check_enabled = True
        custody_before_candidate = None
        custody_before_stable_frames = 0
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
                deposit_missing_confirm.clear()
                timeout.reset()
                continue
            if self.ui_additional():
                deposit_missing_confirm.clear()
                timeout.reset()
                continue
            if self.handle_network_error():
                deposit_missing_confirm.clear()
                timeout.reset()
                continue

            if self._is_monthly_claimed():
                logger.info("Monthly reward already claimed")
                return self.MONTHLY_STATUS_CLAIMED

            if self._is_monthly_deposit_box_full() is True:
                logger.info("Monthly deposit box full: five occupied slots; organize the box before continuing")
                return self.MONTHLY_STATUS_FULL

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
                    logger.attr("PurifyResource", f"balance={times_current}, cost={times_total}, counter_layout={times_layout}")
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
                    if times_current < times_total:
                        logger.info("Monthly purify exhausted by OCR counter before monthly reward is claimed")
                        return self.MONTHLY_STATUS_EXHAUSTED
                else:
                    # An invalid read must not reuse a previous balance to
                    # authorize another click or infer exhaustion from color.
                    times_total = 0
                    continue

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

            if not self._monthly_deposit_box_ready(deposit_missing_confirm):
                continue

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
                custody_before_candidate = None
                custody_before_stable_frames = 0
                continue

            current_tier = self._detect_current_reward_tier(tier_ocr)
            if self._tier_reached(current_tier, target_tier):
                if self.appear(CUSTODY, interval=1):
                    if CUSTODY.match_color(self.device.image, threshold=10):
                        deposit_before_click = match_deposit_tiers(self.device.image)
                        if deposit_before_click == custody_before_candidate:
                            custody_before_stable_frames += 1
                        else:
                            custody_before_candidate = deposit_before_click
                            custody_before_stable_frames = 1
                        if custody_before_stable_frames < 2:
                            continue
                        if self.appear_then_click(CUSTODY, interval=2):
                            if not self._wait_monthly_custody_settle(tier_ocr, deposit_before_click):
                                raise ScriptError("Monthly custody was not confirmed; purify remains blocked")
                            if self._is_monthly_claimed():
                                logger.info("Monthly reward claimed after custody")
                                return self.MONTHLY_STATUS_CLAIMED
                            deposit_missing_confirm.clear()
                            timeout.reset()
                            custody_before_candidate = None
                            custody_before_stable_frames = 0
                            continue
                    else:
                        custody_before_candidate = None
                        custody_before_stable_frames = 0
                        logger.info("Monthly custody unavailable (already stored), continue purify")
            else:
                custody_before_candidate = None
                custody_before_stable_frames = 0

            # The current frame already located PURIFY with luma matching and
            # verified balance / cost. Reuse that offset; a colored reward flash
            # must not turn sufficient resources into a false exhausted result.
            if self.interval_is_reached(PURIFY, interval=self.MONTHLY_PURIFY_CLICK_INTERVAL_SECONDS):
                self.device.click(PURIFY)
                self.interval_reset(PURIFY, interval=self.MONTHLY_PURIFY_CLICK_INTERVAL_SECONDS)
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

        self._send_monthly_reward_reminder()
        monthly_status = self._monthly_purify()
        self._monthly_status = monthly_status
        self._back_to_sanctuary()
        if monthly_status == self.MONTHLY_STATUS_FAILED:
            return False
        return True
