from module.base.timer import Timer
from module.logger import logger
from module.ocr.ocr import Digit, DigitCounter
from tasks.dungeon.assets.assets_dungeon_fast_combat import (
    FAST_COMBAT_TIMES_MINUS,
    FAST_COMBAT_TIMES_PLUS,
    OCR_FAST_COMBAT_CURRENT_TIMES,
    OCR_FAST_COMBAT_REMAINING_TIMES,
)


def calculate_fast_combat_target(
    configured: int,
    remaining: int,
    stamina: int,
    stamina_cost: int,
) -> int:
    """Return the largest safe fast-combat count for the current resources."""
    if configured <= 0 or remaining <= 0 or stamina <= 0 or stamina_cost <= 0:
        return 0
    return min(configured, remaining, stamina // stamina_cost)


def calculate_repeat_combat_target(
    configured: int,
    game_maximum: int,
    affordable: int,
    use_max: bool,
    completed: int = 0,
) -> int:
    """Return the pet-repeat counter for the requested actual run count.

    The counter excludes the first combat launched from the prepare page. A
    counter of four therefore produces five actual runs. Keep configured,
    completed, and affordable in actual-run units, and convert only at this
    final UI boundary.
    """
    if game_maximum <= 0 or affordable <= 0:
        return 0
    affordable_repeats = affordable - 1
    desired_repeats = game_maximum if use_max else configured - max(completed, 0) - 1
    if desired_repeats <= 0 or affordable_repeats <= 0:
        return 0
    return min(desired_repeats, game_maximum, affordable_repeats)


class CombatPrepareDigit(Digit):
    def after_process(self, result):
        result = result.replace("O", "0").replace("o", "0")
        result = result.replace("I", "1").replace("l", "1").replace("|", "1")
        result = result.replace(" ", "").replace(",", "").replace("，", "")
        return super().after_process(result)


class CombatPrepareCounter(DigitCounter):
    def after_process(self, result):
        result = result.replace("O", "0").replace("o", "0")
        result = result.replace("I", "1").replace("l", "1").replace("|", "1")
        result = result.replace(" ", "")
        return super().after_process(result)


class CombatPrepare:
    COMBAT_COUNT_TIMEOUT_SECONDS = 18
    COMBAT_FAST_ENABLE_TIMEOUT_SECONDS = 3
    COMBAT_COUNT_CLICK_INTERVAL_SECONDS = 0.8
    COMBAT_COUNT_BATCH_CLICK_INTERVAL = (0.2, 0.3)
    COMBAT_COUNT_BATCH_CLICK_LIMIT = 3
    COMBAT_COUNT_POST_CLICK_SETTLE_SECONDS = 0.6
    COMBAT_ZERO_CONFIRM_SECONDS = 0.4
    COMBAT_COUNT_STABLE_SECONDS = 2.5
    COMBAT_DEFAULT_FAST_COUNT = 10
    # The account may store up to 20 fast-combat charges, but the game only
    # accepts at most 10 in one launch. Never use the stored remainder as the
    # per-launch target directly.
    COMBAT_MAX_FAST_COUNT = 10
    COMBAT_DEFAULT_REPEAT_COUNT = 10
    COMBAT_MAX_REPEAT_COUNT = 30

    def _combat_fast_count(self) -> int:
        return self._sanitize_combat_count(
            getattr(self.config, "Combat_FastCombatCount", self.COMBAT_DEFAULT_FAST_COUNT),
            default=self.COMBAT_DEFAULT_FAST_COUNT,
            max_value=self.COMBAT_MAX_FAST_COUNT,
            name="FastCombatCount",
        )

    def _combat_repeat_count(self) -> int:
        return self._sanitize_combat_count(
            getattr(self.config, "Combat_RepeatCombatCount", self.COMBAT_DEFAULT_REPEAT_COUNT),
            default=self.COMBAT_DEFAULT_REPEAT_COUNT,
            max_value=self.COMBAT_MAX_REPEAT_COUNT,
            name="RepeatCombatCount",
        )

    @staticmethod
    def _sanitize_combat_count(value, default: int, max_value: int, name: str) -> int:
        try:
            value = int(value)
        except (TypeError, ValueError):
            logger.warning(f"Combat: invalid {name}={value}, fallback to {default}")
            return default

        if value <= 0:
            logger.warning(f"Combat: {name} <= 0, clamp to 1")
            return 1

        if value > max_value:
            logger.warning(f"Combat: {name}={value} exceeds max {max_value}, clamp to {max_value}")
            return max_value

        return value

    def _ocr_fast_combat_remaining_times(self) -> int:
        value = CombatPrepareDigit(
            OCR_FAST_COMBAT_REMAINING_TIMES,
            lang=self._ocr_lang(),
            name="FastCombatRemainingTimes",
        ).ocr_single_line(self.device.image)
        logger.attr("FastCombatRemainingTimes", value)
        return value

    def _ocr_fast_combat_current_times(self) -> int:
        current, _, maximum = CombatPrepareCounter(
            OCR_FAST_COMBAT_CURRENT_TIMES,
            lang=self._ocr_lang(),
            name="FastCombatCurrentTimes",
        ).ocr_single_line(self.device.image)
        # This region contains the entire current/maximum counter. Digit OCR
        # turns a missed slash in "1/10" into 110, which must never become a
        # click count. Require a readable separator and a valid per-launch
        # range; do not guess the missing separator or clamp an invalid read.
        if not 1 <= current <= maximum <= self.COMBAT_MAX_FAST_COUNT:
            logger.info(f"Combat: ignore invalid fast combat counter {current}/{maximum}")
            return 0
        logger.attr("FastCombatCurrentTimes", current)
        return current

    def _handle_repeat_count_overlay_additional(self) -> bool:
        """
        Repeat-count overlay shares AD_BUFF_X_CLOSE with real buff ads.
        While opening/configuring this overlay, avoid ui_additional() so the
        common X-close handler does not close our own menu.
        """
        if self._handle_dungeon_network_error(interval=1):
            return True
        if self.handle_ui_recovery():
            return True
        if self.handle_popup_confirm(interval=1):
            return True
        return False

    def _set_prepare_count(
        self,
        target: int,
        ocr_getter,
        plus_button,
        minus_button,
        label: str,
        additional_handler=None,
        skip_first_screenshot=True,
        max_count: int | None = None,
    ) -> bool:
        """Adjust a valid counter in short batches, confirming each with OCR."""
        if additional_handler is None:
            additional_handler = self._handle_dungeon_additional
        if target <= 0 or (max_count is not None and target > max_count):
            logger.warning(f"Combat: invalid {label} target={target}, maximum={max_count}")
            return False

        timeout = Timer(self.COMBAT_COUNT_TIMEOUT_SECONDS, count=80).start()
        click_interval = Timer(self.COMBAT_COUNT_CLICK_INTERVAL_SECONDS, count=0).start()
        post_click_settle = Timer(self.COMBAT_COUNT_POST_CLICK_SETTLE_SECONDS, count=2).clear()
        stable_timer = Timer(self.COMBAT_COUNT_STABLE_SECONDS, count=4).clear()
        last_value = None

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning(f"Combat: set {label} timeout")
                return False

            if additional_handler():
                post_click_settle.clear()
                stable_timer.clear()
                last_value = None
                continue

            if post_click_settle.started() and not post_click_settle.reached():
                continue

            if last_value is not None and not click_interval.reached():
                continue

            current = ocr_getter()
            if current <= 0 or (max_count is not None and current > max_count):
                continue

            if current == target:
                logger.info(f"Combat: {label} ready at {target}")
                return True

            if current != last_value:
                last_value = current
                stable_timer.reset()
            elif stable_timer.reached():
                logger.warning(f"Combat: {label} stuck at {current}, target={target}")
                return False

            if not click_interval.reached():
                continue

            diff = target - current
            button = plus_button if diff > 0 else minus_button
            # A malformed or stale read must not queue a long, unobserved
            # series of taps. Bound each batch and re-read after the control
            # settles. Preserve last_value/stable_timer across our own clicks:
            # clicking is not progress, and a disabled control must time out.
            # The overall deadline also survives popups and retry attempts.
            _ = self.appear(button)
            clicks = min(abs(diff), self.COMBAT_COUNT_BATCH_CLICK_LIMIT)
            logger.info(f"Combat: adjust {label} from {current} toward {target}, clicks={clicks}")
            self.device.multi_click(
                button,
                n=clicks,
                interval=self.COMBAT_COUNT_BATCH_CLICK_INTERVAL,
            )
            post_click_settle.reset()
            click_interval.reset()

    def _prepare_fast_combat(
        self,
        stamina: int,
        use_max=False,
        skip_first_screenshot=True,
        fallback_on_enable_timeout=False,
    ) -> tuple[str, int]:
        """Prepare a stamina-safe fast-combat count.

        Args:
            fallback_on_enable_timeout: Allow Urgent Tasks to continue with a
                foreground battle if the visible fast-mode toggle stays off.
                Other callers retain their existing failure policy.

        Returns:
            tuple[str, int]: Status and prepared count. Count is zero unless
                status is ready.
        """
        logger.hr("Combat Prepare Fast", level=2)
        timeout = Timer(self.COMBAT_COUNT_TIMEOUT_SECONDS, count=80).start()
        zero_confirm = Timer(self.COMBAT_ZERO_CONFIRM_SECONDS, count=2).clear()
        enable_pending = Timer(self.COMBAT_FAST_ENABLE_TIMEOUT_SECONDS, count=0).clear()

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning("Combat: prepare fast combat timeout")
                return "failed", 0

            if self._handle_dungeon_additional():
                timeout.reset()
                zero_confirm.clear()
                continue

            # Toggling fast combat can temporarily hide every prepare marker
            # behind loading or a confirmation popup. Absence does not prove
            # that we left the page. Handle the popup first and wait for a
            # positive prepare/fast-mode marker before reading any counters.
            if not (self._is_prepare_page() or self._is_fast_combat_on()):
                zero_confirm.clear()
                continue

            if self._is_fast_combat_locked():
                logger.info("Combat: fast combat locked during prepare, use another combat mode")
                return "fallback", 0

            # A disabled toggle can still match FAST_COMBAT_OFF without a lock
            # icon. Urgent Tasks may establish a new clear-time record with a
            # foreground run, so bound retries against a positively visible
            # OFF state. Loading/unknown frames alone never imply unavailability,
            # and a late ON state is accepted even after this retry window.
            if (
                fallback_on_enable_timeout
                and enable_pending.started()
                and enable_pending.reached()
                and self._is_fast_combat_off()
                and not self._is_fast_combat_on()
            ):
                logger.info("Combat: fast combat toggle remained off, use another combat mode")
                return "fallback", 0

            if not self._ensure_fast_combat_state(enabled=True):
                if (
                    fallback_on_enable_timeout
                    and not enable_pending.started()
                    and self._is_fast_combat_off()
                ):
                    enable_pending.start()
                continue

            remaining = self._ocr_fast_combat_remaining_times()
            if remaining <= 0:
                if not zero_confirm.started():
                    zero_confirm.start()
                elif zero_confirm.reached():
                    logger.info("Combat: fast combat remaining times exhausted, use another combat mode")
                    return "fallback", 0
                continue

            zero_confirm.clear()

            stamina_cost = self._combat_stage_stamina_cost()
            if stamina_cost is None:
                logger.warning("Combat: fast combat target has no stamina cost")
                return "failed", 0

            configured = self.COMBAT_MAX_FAST_COUNT if use_max else self._combat_fast_count()
            target = calculate_fast_combat_target(
                configured=configured,
                remaining=remaining,
                stamina=stamina,
                stamina_cost=stamina_cost,
            )
            logger.attr("CombatFastCombatStaminaCost", stamina_cost)
            logger.attr("CombatFastCombatTargetCount", target)
            if target <= 0:
                logger.info(
                    f"Combat: insufficient stamina for fast combat "
                    f"({stamina}/{stamina_cost})"
                )
                return "no_stamina", 0

            if self._set_prepare_count(
                target,
                self._ocr_fast_combat_current_times,
                FAST_COMBAT_TIMES_PLUS,
                FAST_COMBAT_TIMES_MINUS,
                "FastCombatCurrentTimes",
                skip_first_screenshot=True,
                max_count=self.COMBAT_MAX_FAST_COUNT,
            ):
                return "ready", target
            return "failed", 0
