from module.base.timer import Timer
from module.logger import logger
from tasks.combat.assets.assets_combat_action import COMBAT_START
from tasks.combat.assets.assets_combat_fast_combat import (
    FAST_COMBAT_LOCKED,
    FAST_COMBAT_OFF,
    FAST_COMBAT_ON,
    FAST_COMBAT_RESULT_CLOSE,
    FAST_COMBAT_WINDOW,
)
from tasks.combat.assets.assets_combat_repeat_entry import (
    REPEAT_COMBAT_OFF,
    REPEAT_COMBAT_ON,
)
from tasks.combat.assets.assets_combat_repeat_result import (
    REPEAT_COMBAT_CHECK,
    REPEAT_COMBAT_OVER,
)
from tasks.combat.assets.assets_combat_repeat_status_bar import MINIMIZE, WINDOW


class CombatExecuteMixin:
    def _is_fast_combat_locked(self) -> bool:
        return self.match_template_luma(FAST_COMBAT_LOCKED, similarity=self.COMBAT_CHECK_SIMILARITY)

    def _is_fast_combat_on(self) -> bool:
        return self.match_template_luma(FAST_COMBAT_ON, similarity=self.COMBAT_CHECK_SIMILARITY)

    def _is_fast_combat_off(self) -> bool:
        if self._is_fast_combat_locked():
            return True
        return self.match_template_luma(FAST_COMBAT_OFF, similarity=self.COMBAT_CHECK_SIMILARITY)

    def _is_repeat_combat_on(self) -> bool:
        return self.match_color(REPEAT_COMBAT_ON, threshold=self.COMBAT_STATE_COLOR_THRESHOLD)

    def _is_repeat_result_window(self) -> bool:
        return self.match_template_luma(WINDOW, similarity=self.COMBAT_CHECK_SIMILARITY)

    def _is_repeat_combat_over(self) -> bool:
        return self.match_template_luma(REPEAT_COMBAT_OVER, similarity=self.COMBAT_CHECK_SIMILARITY)

    def _has_repeat_combat_check(self) -> bool:
        return self.match_template_luma(REPEAT_COMBAT_CHECK, similarity=self.COMBAT_CHECK_SIMILARITY)

    def _is_repeat_combat_running(self) -> bool:
        if self._is_repeat_combat_over() or self._is_repeat_result_window():
            return False
        return self._has_repeat_combat_check()

    def _ensure_fast_combat_state(self, enabled: bool) -> bool:
        if not self._combat_supports_fast_combat():
            return not enabled

        if self._is_fast_combat_locked():
            return not enabled

        if enabled:
            if self._is_fast_combat_on():
                return True
            if self._is_fast_combat_off() and self.interval_is_reached(
                FAST_COMBAT_OFF, interval=self.COMBAT_TOGGLE_INTERVAL_SECONDS
            ):
                logger.info("Combat: enable fast combat")
                self.device.click(FAST_COMBAT_OFF)
                self.interval_reset(FAST_COMBAT_OFF, interval=self.COMBAT_TOGGLE_INTERVAL_SECONDS)
            return False

        if self._is_fast_combat_off():
            return True
        if self._is_fast_combat_on() and self.interval_is_reached(
            FAST_COMBAT_OFF, interval=self.COMBAT_TOGGLE_INTERVAL_SECONDS
        ):
            logger.info("Combat: disable fast combat")
            self.device.click(FAST_COMBAT_OFF)
            self.interval_reset(FAST_COMBAT_OFF, interval=self.COMBAT_TOGGLE_INTERVAL_SECONDS)
        return False

    def _ensure_repeat_combat_enabled(self) -> bool:
        if self._is_repeat_combat_on():
            return True
        if self.appear_then_click(REPEAT_COMBAT_OFF, interval=1):
            logger.info("Combat: enable repeat combat")
            return False
        return False

    def _run_fast_combat(self, skip_first_screenshot=True) -> bool:
        logger.info("Combat: run fast combat")
        timeout = Timer(self.COMBAT_RUN_TIMEOUT_SECONDS, count=240).start()
        stage = "prepare"
        start_pending = Timer(self.COMBAT_START_PENDING_SECONDS, count=0).clear()
        prepare_confirm = Timer(0.4, count=2).clear()

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning("Combat: fast combat timeout")
                return False

            self._raise_if_package_full()

            if self._handle_combat_additional():
                timeout.reset()
                continue

            if stage == "prepare":
                if not self._ensure_fast_combat_state(enabled=True):
                    timeout.reset()
                    continue

                if self.appear_then_click(COMBAT_START, interval=self.COMBAT_START_INTERVAL_SECONDS):
                    logger.info("Combat: start fast combat")
                    stage = "pending"
                    start_pending.reset()
                    timeout.reset()
                    continue
                continue

            if stage == "pending":
                if self.appear_then_click(FAST_COMBAT_RESULT_CLOSE, interval=self.COMBAT_RESULT_INTERVAL_SECONDS):
                    stage = "result"
                    timeout.reset()
                    continue

                if self.appear(FAST_COMBAT_WINDOW) or self.appear(FAST_COMBAT_RESULT_CLOSE):
                    stage = "result"
                    timeout.reset()
                    continue

                if start_pending.reached() and self._is_prepare_page():
                    logger.info("Combat: fast combat start pending timeout, retry")
                    stage = "prepare"
                    timeout.reset()
                    continue
                continue

            if stage == "result":
                if self.appear_then_click(FAST_COMBAT_RESULT_CLOSE, interval=self.COMBAT_RESULT_INTERVAL_SECONDS):
                    timeout.reset()
                    continue

                if self.appear(FAST_COMBAT_WINDOW):
                    timeout.reset()
                    continue

                if self._is_prepare_page():
                    if not prepare_confirm.started():
                        prepare_confirm.start()
                    elif prepare_confirm.reached():
                        logger.info("Combat: fast combat finished")
                        return True
                else:
                    prepare_confirm.clear()
                continue

    def _run_repeat_combat(self, skip_first_screenshot=True) -> bool:
        logger.info("Combat: run repeat combat")
        timeout = Timer(self.COMBAT_RUN_TIMEOUT_SECONDS, count=240).start()
        stage = "prepare"
        start_pending = Timer(self.COMBAT_START_PENDING_SECONDS, count=0).clear()
        main_confirm = Timer(0.4, count=2).clear()

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning("Combat: repeat combat timeout")
                return False

            self._raise_if_package_full()

            if self._handle_combat_additional():
                timeout.reset()
                continue

            if stage == "prepare":
                if not self._ensure_fast_combat_state(enabled=False):
                    timeout.reset()
                    continue

                if not self._ensure_repeat_combat_enabled():
                    timeout.reset()
                    continue

                if self.appear_then_click(COMBAT_START, interval=self.COMBAT_START_INTERVAL_SECONDS):
                    logger.info("Combat: start repeat combat")
                    stage = "pending"
                    start_pending.reset()
                    timeout.reset()
                    continue
                continue

            if stage == "pending":
                if self.appear_then_click(MINIMIZE, interval=1):
                    stage = "background"
                    logger.info("Combat: minimize repeat combat")
                    timeout.reset()
                    continue

                if start_pending.reached() and self._is_prepare_page():
                    logger.info("Combat: repeat combat start pending timeout, retry")
                    stage = "prepare"
                    timeout.reset()
                    continue
                continue

            if stage == "background":
                if self.appear_then_click(MINIMIZE, interval=1):
                    timeout.reset()
                    continue
                if self.is_in_main(interval=0) and self._is_repeat_combat_running():
                    if not main_confirm.started():
                        main_confirm.start()
                    elif main_confirm.reached():
                        logger.info("Combat: repeat combat running in background")
                        return True
                else:
                    main_confirm.clear()
                continue
