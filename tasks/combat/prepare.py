from module.base.timer import Timer
from module.logger import logger
from module.ocr.ocr import Digit
from tasks.combat.assets.assets_combat_fast_combat import (
    FAST_COMBAT_LOCKED,
    FAST_COMBAT_TIMES_MINUS,
    FAST_COMBAT_TIMES_PLUS,
    OCR_FAST_COMBAT_CURRENT_TIMES,
    OCR_FAST_COMBAT_REMAINING_TIMES,
)
from tasks.combat.assets.assets_combat_repeat_entry import REPEAT_COMBAT_MENU
from tasks.combat.assets.assets_combat_repeat_menu import (
    OCR_REPEAT_COMBAT_TIMES,
    REPEAT_COMBAT_TIMES_MINUS,
    REPEAT_COMBAT_TIMES_PLUS,
)


class CombatPrepareDigit(Digit):
    def after_process(self, result):
        result = result.replace("O", "0").replace("o", "0")
        result = result.replace("I", "1").replace("l", "1").replace("|", "1")
        result = result.replace(" ", "")
        return super().after_process(result)


class CombatPrepare:
    COMBAT_COUNT_TIMEOUT_SECONDS = 18
    COMBAT_COUNT_CLICK_INTERVAL_SECONDS = 0.8
    COMBAT_COUNT_BATCH_CLICK_INTERVAL = (0.2, 0.3)
    COMBAT_COUNT_POST_CLICK_SETTLE_SECONDS = 0.6
    COMBAT_ZERO_CONFIRM_SECONDS = 0.4
    COMBAT_COUNT_STABLE_SECONDS = 2.5
    COMBAT_DEFAULT_FAST_COUNT = 10
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
        value = CombatPrepareDigit(
            OCR_FAST_COMBAT_CURRENT_TIMES,
            lang=self._ocr_lang(),
            name="FastCombatCurrentTimes",
        ).ocr_single_line(self.device.image)
        logger.attr("FastCombatCurrentTimes", value)
        return value

    def _ocr_repeat_combat_times(self) -> int:
        value = CombatPrepareDigit(
            OCR_REPEAT_COMBAT_TIMES,
            lang=self._ocr_lang(),
            name="RepeatCombatTimes",
        ).ocr_single_line(self.device.image)
        logger.attr("RepeatCombatTimes", value)
        return value

    def _is_repeat_count_controls_open(self) -> bool:
        return self.appear(REPEAT_COMBAT_TIMES_PLUS) and self.appear(REPEAT_COMBAT_TIMES_MINUS)

    def _handle_repeat_count_overlay_additional(self) -> bool:
        """
        Repeat-count overlay shares AD_BUFF_X_CLOSE with real buff ads.
        While opening/configuring this overlay, avoid ui_additional() so the
        common X-close handler does not close our own menu.
        """
        if self._handle_combat_network_error(interval=1):
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
    ) -> bool:
        if additional_handler is None:
            additional_handler = self._handle_combat_additional

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
                timeout.reset()
                post_click_settle.clear()
                stable_timer.clear()
                last_value = None
                continue

            if post_click_settle.started() and not post_click_settle.reached():
                continue

            if last_value is not None and not click_interval.reached():
                continue

            current = ocr_getter()
            if current <= 0:
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
            if diff > 0:
                _ = self.appear(plus_button)
                self.device.multi_click(
                    plus_button,
                    n=abs(diff),
                    interval=self.COMBAT_COUNT_BATCH_CLICK_INTERVAL,
                )
            else:
                _ = self.appear(minus_button)
                self.device.multi_click(
                    minus_button,
                    n=abs(diff),
                    interval=self.COMBAT_COUNT_BATCH_CLICK_INTERVAL,
                )

            post_click_settle.reset()
            stable_timer.clear()
            last_value = None
            click_interval.reset()
            timeout.reset()

    def _prepare_fast_combat(self, skip_first_screenshot=True) -> str:
        logger.hr("Combat Prepare Fast", level=2)
        timeout = Timer(self.COMBAT_COUNT_TIMEOUT_SECONDS, count=80).start()
        zero_confirm = Timer(self.COMBAT_ZERO_CONFIRM_SECONDS, count=2).clear()

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning("Combat: prepare fast combat timeout")
                return "failed"

            if not self._is_prepare_page():
                logger.warning("Combat: leave prepare page while preparing fast combat")
                return "failed"

            if self._handle_combat_additional():
                timeout.reset()
                zero_confirm.clear()
                continue

            if self._is_fast_combat_locked():
                logger.info("Combat: fast combat locked during prepare, fallback to repeat combat")
                return "fallback"

            if not self._ensure_fast_combat_state(enabled=True):
                timeout.reset()
                continue

            remaining = self._ocr_fast_combat_remaining_times()
            if remaining <= 0:
                if not zero_confirm.started():
                    zero_confirm.start()
                elif zero_confirm.reached():
                    logger.info("Combat: fast combat remaining times exhausted, fallback to repeat combat")
                    return "fallback"
                continue

            zero_confirm.clear()

            target = min(self._combat_fast_count(), remaining)
            logger.attr("CombatFastCombatTargetCount", target)
            if self._set_prepare_count(
                target,
                self._ocr_fast_combat_current_times,
                FAST_COMBAT_TIMES_PLUS,
                FAST_COMBAT_TIMES_MINUS,
                "FastCombatCurrentTimes",
                skip_first_screenshot=True,
            ):
                return "ready"
            return "failed"

    def _prepare_repeat_combat(self, skip_first_screenshot=True) -> bool:
        logger.hr("Combat Prepare Repeat", level=2)
        timeout = Timer(self.COMBAT_COUNT_TIMEOUT_SECONDS, count=80).start()
        control_pending = Timer(0.8, count=2).clear()

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning("Combat: prepare repeat combat timeout")
                return False

            controls_open = self._is_repeat_count_controls_open()
            controls_pending = control_pending.started() and not control_pending.reached()

            if not (self._is_prepare_page() or controls_open or controls_pending):
                logger.warning("Combat: leave prepare page while preparing repeat combat")
                return False

            if controls_open or controls_pending:
                if self._handle_repeat_count_overlay_additional():
                    timeout.reset()
                    control_pending.clear()
                    continue
            else:
                if self._handle_combat_additional():
                    timeout.reset()
                    control_pending.clear()
                    continue

            if controls_open:
                target = self._combat_repeat_count()
                logger.attr("CombatRepeatCombatTargetCount", target)
                return self._set_prepare_count(
                    target,
                    self._ocr_repeat_combat_times,
                    REPEAT_COMBAT_TIMES_PLUS,
                    REPEAT_COMBAT_TIMES_MINUS,
                    "RepeatCombatTimes",
                    additional_handler=self._handle_repeat_count_overlay_additional,
                    skip_first_screenshot=True,
                )

            if controls_pending:
                continue

            if not self._ensure_fast_combat_state(enabled=False):
                timeout.reset()
                continue

            if not self._ensure_repeat_combat_enabled():
                timeout.reset()
                continue

            if not self.appear(REPEAT_COMBAT_TIMES_PLUS) or not self.appear(REPEAT_COMBAT_TIMES_MINUS):
                if control_pending.started() and not control_pending.reached():
                    continue
                if self.appear_then_click(REPEAT_COMBAT_MENU, interval=1):
                    logger.info("Combat: open repeat combat count controls")
                    control_pending.reset()
                    timeout.reset()
                    continue
