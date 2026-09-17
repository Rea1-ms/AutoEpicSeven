from module.base.timer import Timer
from module.exception import RequestHumanTakeover
from module.logger import logger
from tasks.dungeon.assets.assets_dungeon_action import (
    AUTO_COMBAT,
    AUTO_COMBAT_ENEMY_SELECT,
    COMBAT_RESULT_CONFIRM,
    COMBAT_RESULT_LEAVE,
    COMBAT_START,
)
from tasks.dungeon.assets.assets_dungeon_fast_combat import (
    FAST_COMBAT_LOCKED,
    FAST_COMBAT_OFF,
    FAST_COMBAT_ON,
    FAST_COMBAT_RESULT_CLOSE,
    FAST_COMBAT_WINDOW,
)
from tasks.dungeon.assets.assets_dungeon_state import (
    AUTO_COMBAT_EXIST,
    AUTO_COMBAT_SKILL_CLOSED,
    AUTO_COMBAT_SKILL_OPENED,
    COMBAT_RESULT_CLEAR,
    COMBAT_RESULT_FAILED,
    ENEMY_NUM_EXIST,
)


class CombatExecuteMixin:
    AUTO_COMBAT_ENTER_SECONDS = 2
    AUTO_COMBAT_CLICK_INTERVAL_SECONDS = 2
    AUTO_COMBAT_UNKNOWN_WARN_SECONDS = 15

    def _is_fast_combat_locked(self) -> bool:
        return self.match_template_luma(FAST_COMBAT_LOCKED, similarity=self.COMBAT_CHECK_SIMILARITY)

    def _is_fast_combat_on(self) -> bool:
        return self.match_template_luma(FAST_COMBAT_ON, similarity=self.COMBAT_CHECK_SIMILARITY)

    def _is_fast_combat_off(self) -> bool:
        if self._is_fast_combat_locked():
            return True
        return self.match_template_luma(FAST_COMBAT_OFF, similarity=self.COMBAT_CHECK_SIMILARITY)

    def _is_repeat_combat_running(self) -> bool:
        if self._is_repeat_combat_over() or self._is_repeat_result_window():
            return False
        return self._has_repeat_combat_check()

    def _has_background_repeat_combat_check(self) -> bool:
        """
        Return whether the top-right background repeat marker is visible.

        This is a thin semantic wrapper around `_has_repeat_combat_check()`.
        The actual asset is the same, but callers that run before a new dungeon
        starts read much clearer when they talk about "background repeat
        combat" explicitly instead of the generic in-combat helper name.
        """
        return self._has_repeat_combat_check()

    def _detect_background_repeat_combat_state(self) -> str | None:
        """
        Detect whether an old background repeat-combat session still exists.

        Startup pre-check needs to care about more than just the "still
        running" marker:
        - `REPEAT_COMBAT_CHECK` means the old session is still running
        - `REPEAT_COMBAT_OVER` means the old session has already finished and
          is waiting for us to open the result
        - `SETTLEMENT_WINDOW_CHECK` means the result window is already open and still
          needs cleanup before a new dungeon run may start

        Treat all three as "there is already an old background combat state on
        screen", then let the dedicated watch/result logic finish the cleanup.
        """
        if self._is_repeat_result_window() or self._is_repeat_combat_over():
            return "result"
        if self._has_background_repeat_combat_check():
            return "running"
        return None

    def _is_background_repeat_combat_running(self) -> bool:
        """
        Detect whether a previous dungeon session is still running in background.

        The signal is intentionally conservative:
        - if the finish prompt is already visible, the background run is no
          longer considered "running"
        - if the result window is already visible, the background run is no
          longer considered "running"
        - otherwise, the top-right repeat marker means the old session is still
          alive and should be adopted before launching a new dungeon flow
        """
        return self._detect_background_repeat_combat_state() == "running"

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

    def _detect_auto_combat_state(self) -> bool | None:
        """Return the positively identified auto-combat state.

        ``True`` is deliberately based on several intermittent battle HUD
        elements instead of the auto button itself.  During skill animations
        every HUD element may disappear, so no match is an unknown state, not
        proof that auto combat is disabled.  ``False`` is only returned when
        an enemy-number target is visible during the player's actionable turn.
        """
        if (
            self.appear(AUTO_COMBAT_ENEMY_SELECT)
            or self.appear(AUTO_COMBAT_SKILL_CLOSED)
            or self.appear(AUTO_COMBAT_SKILL_OPENED)
        ):
            return True
        if self.appear(ENEMY_NUM_EXIST):
            return False
        return None

    def _run_normal_combat(
        self,
        completion_check,
        skip_first_screenshot=True,
    ) -> bool:
        """Run one foreground combat and return on a caller-owned page.

        Args:
            completion_check: Side-effect-free predicate for the page reached
                after leaving the battle result.

        Pages:
            in: combat prepare
            out: page recognized by ``completion_check``

        The only known ambiguous case is a manual battle against one elite
        enemy: it has neither an enemy-number marker nor a distinct auto-off
        asset.  The loop therefore keeps observing instead of blindly toggling
        AUTO_COMBAT, which could disable an already-running automatic battle
        during an ultimate animation.
        """
        logger.info("Combat: run normal foreground combat")
        stage = "prepare"
        start_pending = Timer(self.COMBAT_START_PENDING_SECONDS, count=0).clear()
        auto_enter = Timer(self.AUTO_COMBAT_ENTER_SECONDS, count=3).clear()
        auto_warning = Timer(
            self.AUTO_COMBAT_UNKNOWN_WARN_SECONDS,
            count=0,
        ).clear()
        battle_result_seen = False

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if battle_result_seen and completion_check():
                logger.info("Combat: normal foreground combat completed")
                return True

            self._raise_if_package_full()

            if self.appear(COMBAT_RESULT_FAILED):
                message = (
                    "Combat: foreground battle failed. "
                    "Please adjust the team before retrying."
                )
                logger.critical(message)
                raise RequestHumanTakeover(message)

            if self.appear(COMBAT_RESULT_CLEAR):
                battle_result_seen = True
                if self.interval_is_reached(
                    COMBAT_RESULT_CLEAR,
                    interval=self.COMBAT_RESULT_INTERVAL_SECONDS,
                ):
                    logger.info("Combat: advance clear result")
                    self.device.click(COMBAT_RESULT_CLEAR)
                    self.interval_reset(
                        COMBAT_RESULT_CLEAR,
                        interval=self.COMBAT_RESULT_INTERVAL_SECONDS,
                    )
                continue

            if self.appear_then_click(
                COMBAT_RESULT_CONFIRM,
                interval=self.COMBAT_RESULT_INTERVAL_SECONDS,
            ):
                battle_result_seen = True
                logger.info("Combat: confirm foreground battle rewards")
                continue

            if self.appear_then_click(
                COMBAT_RESULT_LEAVE,
                interval=self.COMBAT_RESULT_INTERVAL_SECONDS,
            ):
                battle_result_seen = True
                logger.info("Combat: leave foreground battle")
                continue

            if stage == "prepare" and self._is_prepare_page():
                if not self._ensure_fast_combat_state(enabled=False):
                    continue
                if self.appear_then_click(
                    COMBAT_START,
                    interval=self.COMBAT_START_INTERVAL_SECONDS,
                ):
                    logger.info("Combat: start normal foreground battle")
                    stage = "battle"
                    start_pending.reset()
                    auto_enter.reset()
                    auto_warning.reset()
                    continue

            if stage == "battle":
                auto_state = self._detect_auto_combat_state()
                if auto_state is True:
                    auto_warning.reset()
                    self.device.stuck_record_clear()
                    continue
                if auto_state is False:
                    auto_warning.reset()
                    self.device.stuck_record_clear()
                    if auto_enter.reached() and self.interval_is_reached(
                        AUTO_COMBAT,
                        interval=self.AUTO_COMBAT_CLICK_INTERVAL_SECONDS,
                    ):
                        logger.info("Combat: enable automatic combat")
                        self.device.click_record_remove(AUTO_COMBAT)
                        self.device.click(AUTO_COMBAT)
                        self.interval_reset(
                            AUTO_COMBAT,
                            interval=self.AUTO_COMBAT_CLICK_INTERVAL_SECONDS,
                        )
                    continue

                if self.appear(AUTO_COMBAT_EXIST):
                    self.device.stuck_record_clear()

                if auto_warning.reached():
                    logger.warning(
                        "Combat: automatic-combat state is still ambiguous; "
                        "waiting for a positive HUD state"
                    )
                    auto_warning.reset()

                if start_pending.reached() and self._is_prepare_page():
                    logger.info("Combat: normal battle start pending timeout, retry")
                    stage = "prepare"
                    continue

            if self._handle_dungeon_additional():
                continue

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

            if self._handle_dungeon_additional():
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
