from module.base.timer import Timer
from module.logger import logger
from module.ocr.ocr import DigitCounter
from tasks.arena.assets.assets_arena import (
    AUTO_BATTLE_RESULT_CONFIRM,
    AUTO_FIGHT,
    AUTO_FIGHT_EXIST,
    BATTLE_START,
    CHALLENGE,
    FAST_BATTLE_LOCKED,
    FAST_BATTLE_OFF,
    FAST_BATTLE_ON,
    FAST_BATTLE_RESULT_CONFIRM,
    NPC_COMBAT_ENTRY,
    NPC_OPPONENT,
    OCR_FAST_BATTLE_TIMES,
    OPPONENT,
)
from tasks.dungeon.runtime import is_background_repeat_combat_active


class OcrFastBattleTimes(DigitCounter):
    def after_process(self, result):
        result = super().after_process(result)
        result = result.replace("O", "0").replace("o", "0")
        result = result.replace("I", "1").replace("l", "1")
        result = result.replace("／", "/")
        result = result.replace(" ", "")
        return result


class ArenaNpcCombatMixin:
    ARENA_NPC_ROUND_TIMEOUT_SECONDS = 90
    ARENA_NPC_CHALLENGE_LUMA_SIMILARITY = 0.8
    ARENA_NPC_CHALLENGE_COLOR_THRESHOLD = 30
    ARENA_NPC_AUTO_RESULT_INTERVAL_SECONDS = 3
    ARENA_NPC_AUTO_FIGHT_ENTER_SECONDS = 2
    ARENA_NPC_AUTO_FIGHT_CLICK_INTERVAL_SECONDS = 2
    ARENA_NPC_AUTO_FIGHT_CLEAR_CONFIRM_SECONDS = 1.2
    ARENA_NPC_AUTO_FIGHT_MAX_CLICKS = 4
    ARENA_NPC_AUTO_FIGHT_WARN_INTERVAL_SECONDS = 8
    ARENA_NPC_FAST_TOGGLE_INTERVAL_SECONDS = 0.8
    ARENA_NPC_ENTRY_CLICK_INTERVAL_SECONDS = 1.8
    ARENA_NPC_OPPONENT_CLICK_INTERVAL_SECONDS = 1.0
    ARENA_NPC_SEEK_NON_NPC_STABLE_SECONDS = 0.8
    ARENA_NPC_SELECT_LOST_STABLE_SECONDS = 0.8
    ARENA_NPC_GRAY_RETRY_LIMIT = 8
    ARENA_NPC_CHALLENGE_PENDING_SECONDS = 4.5
    ARENA_NPC_BATTLE_START_PENDING_SECONDS = 6

    ARENA_NPC_STAGE_SEEK = "seek_npc_lane"
    ARENA_NPC_STAGE_SELECT = "select_opponent"
    ARENA_NPC_STAGE_PENDING = "challenge_pending"
    ARENA_NPC_STAGE_PREPARE = "battle_prepare"
    ARENA_NPC_STAGE_BATTLE = "battle_running"

    def _is_challenge_ready(self, interval=0) -> bool:
        self.device.stuck_record_add(CHALLENGE)

        if interval and not self.interval_is_reached(CHALLENGE, interval=interval):
            return False

        appear = False
        if CHALLENGE.match_template_luma(
            self.device.image,
            similarity=self.ARENA_NPC_CHALLENGE_LUMA_SIMILARITY,
        ):
            if CHALLENGE.match_color(
                self.device.image, threshold=self.ARENA_NPC_CHALLENGE_COLOR_THRESHOLD
            ):
                appear = True

        if appear and interval:
            self.interval_reset(CHALLENGE, interval=interval)

        return appear

    def _is_challenge_exhausted(self) -> bool:
        if CHALLENGE.match_template_luma(
            self.device.image,
            similarity=self.ARENA_NPC_CHALLENGE_LUMA_SIMILARITY,
        ):
            return not CHALLENGE.match_color(
                self.device.image,
                threshold=self.ARENA_NPC_CHALLENGE_COLOR_THRESHOLD,
            )
        return False

    def _ensure_fast_battle_state(self, enabled: bool) -> bool:
        """
        Returns:
            bool: True when fast-battle state already matches `enabled`.
        """
        if self.appear(FAST_BATTLE_LOCKED):
            # Locked means fast battle is unavailable today and cannot be toggled on.
            return not enabled

        if enabled:
            if self.appear(FAST_BATTLE_ON):
                return True
            if self.appear_then_click(
                FAST_BATTLE_OFF, interval=self.ARENA_NPC_FAST_TOGGLE_INTERVAL_SECONDS
            ):
                logger.info("Arena NPC: enable fast battle")
            return False

        if self.appear(FAST_BATTLE_OFF):
            return True
        if self.appear_then_click(
            FAST_BATTLE_ON, interval=self.ARENA_NPC_FAST_TOGGLE_INTERVAL_SECONDS
        ):
            logger.info("Arena NPC: disable fast battle")
        return False

    def _is_battle_prepare_page(self) -> bool:
        """Battle-prepare page can be identified by its stable controls."""
        return (
            self.appear(BATTLE_START)
            or self.appear(FAST_BATTLE_ON)
            or self.appear(FAST_BATTLE_OFF)
            or self.appear(FAST_BATTLE_LOCKED)
        )

    def _is_npc_combat_context(self) -> bool:
        return (
            self.appear(NPC_OPPONENT)
            or self.appear(CHALLENGE)
            or self._is_battle_prepare_page()
        )

    def _ocr_fast_battle_times(self) -> tuple[int, int, int]:
        ocr = OcrFastBattleTimes(
            OCR_FAST_BATTLE_TIMES, lang="en", name="FastBattleTimes"
        )
        # For fast battle, OCR format is "remaining/total" (e.g. 9/10, 10/10).
        current, remain, total = ocr.ocr_single_line(self.device.image)
        if total:
            logger.attr("FastBattleTimes", f"{current}/{total}")
        else:
            logger.warning(f"Fast battle times OCR invalid: {current}/{total}")
        return current, remain, total

    def _npc_combat_once(
        self, use_fast_battle: bool, skip_first_screenshot=True
    ) -> str:
        """
        Returns:
            str: completed / exhausted / failed
        """
        timeout = Timer(self.ARENA_NPC_ROUND_TIMEOUT_SECONDS, count=360).start()
        stage = self.ARENA_NPC_STAGE_SEEK
        gray_retry = 0
        challenge_pending_timer = Timer(
            self.ARENA_NPC_CHALLENGE_PENDING_SECONDS, count=0
        ).start()
        battle_start_pending_timer = Timer(
            self.ARENA_NPC_BATTLE_START_PENDING_SECONDS, count=0
        ).start()
        battle_start_grace_timer = Timer(2, count=0).start()
        entry_click_timer = Timer(
            self.ARENA_NPC_ENTRY_CLICK_INTERVAL_SECONDS, count=0
        ).clear()
        opponent_click_timer = Timer(
            self.ARENA_NPC_OPPONENT_CLICK_INTERVAL_SECONDS, count=0
        ).clear()
        seek_non_npc_timer = Timer(
            self.ARENA_NPC_SEEK_NON_NPC_STABLE_SECONDS, count=2
        ).clear()
        select_lost_timer = Timer(
            self.ARENA_NPC_SELECT_LOST_STABLE_SECONDS, count=2
        ).clear()
        auto_fight_enter_timer = Timer(
            self.ARENA_NPC_AUTO_FIGHT_ENTER_SECONDS, count=3
        ).start()
        auto_fight_click_interval = Timer(
            self.ARENA_NPC_AUTO_FIGHT_CLICK_INTERVAL_SECONDS, count=4
        ).clear()
        auto_fight_clear_confirm_timer = Timer(
            self.ARENA_NPC_AUTO_FIGHT_CLEAR_CONFIRM_SECONDS, count=2
        ).clear()
        auto_fight_warn_timer = Timer(
            self.ARENA_NPC_AUTO_FIGHT_WARN_INTERVAL_SECONDS, count=0
        ).clear()
        fast_battle_effective = bool(use_fast_battle)
        fast_times_checked = False
        auto_fight_clicks = 0
        auto_fight_checked = False
        battle_result_seen = False
        stage_log_timer = Timer(1.5, count=0).start()
        last_stage = None

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning("Arena NPC round timeout")
                return "failed"

            # End condition: one NPC fight settled and returned to arena home.
            if (
                stage == self.ARENA_NPC_STAGE_BATTLE
                and self._is_arena_combat_home_ready()
            ):
                return "completed"

            if self.handle_network_error():
                timeout.reset()
                continue

            if last_stage != stage or stage_log_timer.reached():
                logger.attr("ArenaNPCStage", stage)
                last_stage = stage
                stage_log_timer.reset()

            if stage == self.ARENA_NPC_STAGE_SEEK:
                if self._is_battle_prepare_page():
                    stage = self.ARENA_NPC_STAGE_PREPARE
                    timeout.reset()
                    continue

                if self.appear(NPC_OPPONENT):
                    seek_non_npc_timer.clear()
                    stage = self.ARENA_NPC_STAGE_SELECT
                    timeout.reset()
                    continue

                if entry_click_timer.reached() and self.appear_then_click(
                    NPC_COMBAT_ENTRY, interval=0
                ):
                    logger.info("Arena NPC: enter NPC combat")
                    entry_click_timer.reset()
                    timeout.reset()
                    continue

                # In real-opponent page, CHALLENGE exists but NPC_OPPONENT does not.
                if self.appear(CHALLENGE):
                    if not seek_non_npc_timer.started():
                        seek_non_npc_timer.start()
                    elif (
                        seek_non_npc_timer.reached()
                        and entry_click_timer.reached()
                        and self.appear_then_click(NPC_COMBAT_ENTRY, interval=0)
                    ):
                        logger.info(
                            "Arena NPC: non-NPC challenge page detected, switch to NPC combat"
                        )
                        entry_click_timer.reset()
                        timeout.reset()
                        continue
                else:
                    seek_non_npc_timer.clear()

                if self.ui_additional():
                    timeout.reset()
                    continue

                continue

            if stage == self.ARENA_NPC_STAGE_SELECT:
                if self._is_battle_prepare_page():
                    select_lost_timer.clear()
                    stage = self.ARENA_NPC_STAGE_PREPARE
                    timeout.reset()
                    continue

                # CHALLENGE is only valid on NPC list page.
                if not self.appear(NPC_OPPONENT):
                    if not select_lost_timer.started():
                        select_lost_timer.start()
                    elif select_lost_timer.reached():
                        select_lost_timer.clear()
                        stage = self.ARENA_NPC_STAGE_SEEK
                    continue
                select_lost_timer.clear()

                if self._is_challenge_ready(interval=1):
                    self.device.click(CHALLENGE)
                    logger.info("Arena NPC: challenge")
                    stage = self.ARENA_NPC_STAGE_PENDING
                    gray_retry = 0
                    challenge_pending_timer.reset()
                    timeout.reset()
                    continue

                if self._is_challenge_exhausted():
                    if opponent_click_timer.reached() and self.appear_then_click(
                        NPC_OPPONENT, interval=0
                    ):
                        gray_retry += 1
                        logger.info(
                            f"Arena NPC: challenge gray, rotate opponent ({gray_retry})"
                        )
                        if gray_retry >= self.ARENA_NPC_GRAY_RETRY_LIMIT:
                            logger.info(
                                "Arena NPC: challenge unavailable after retries"
                            )
                            return "exhausted"
                        opponent_click_timer.reset()
                        timeout.reset()
                        continue

                if self.ui_additional():
                    timeout.reset()
                    continue

                continue

            if stage == self.ARENA_NPC_STAGE_PENDING:
                if self._is_battle_prepare_page():
                    stage = self.ARENA_NPC_STAGE_PREPARE
                    timeout.reset()
                    continue

                if challenge_pending_timer.reached():
                    if self._is_challenge_ready(interval=1):
                        self.device.click(CHALLENGE)
                        logger.info("Arena NPC: challenge retry")
                        challenge_pending_timer.reset()
                        timeout.reset()
                        continue
                    if self.appear(NPC_OPPONENT):
                        stage = self.ARENA_NPC_STAGE_SELECT
                    else:
                        stage = self.ARENA_NPC_STAGE_SEEK
                    logger.info("Arena NPC: challenge pending timeout, retry lane")
                    timeout.reset()
                    continue

                if self.ui_additional():
                    timeout.reset()
                    continue

                continue

            if stage == self.ARENA_NPC_STAGE_PREPARE:
                if not self._is_battle_prepare_page():
                    if self.appear(NPC_OPPONENT):
                        stage = self.ARENA_NPC_STAGE_SELECT
                        continue
                    if self.appear(CHALLENGE):
                        stage = self.ARENA_NPC_STAGE_SEEK
                        continue
                    if self.ui_additional():
                        timeout.reset()
                        continue
                    continue

                if fast_battle_effective and self.appear(FAST_BATTLE_LOCKED):
                    logger.info(
                        "Arena NPC: fast battle locked, fallback to normal battle"
                    )
                    fast_battle_effective = False
                    fast_times_checked = True

                if fast_battle_effective and (not fast_times_checked):
                    if self.appear(FAST_BATTLE_ON) or self.appear(FAST_BATTLE_OFF):
                        remaining, _, total = self._ocr_fast_battle_times()
                        fast_times_checked = True
                        if total > 0 and remaining <= 0:
                            logger.info(
                                "Arena NPC: fast battle exhausted by OCR, fallback to normal battle"
                            )
                            fast_battle_effective = False

                if not self._ensure_fast_battle_state(fast_battle_effective):
                    timeout.reset()
                    continue

                if self.appear_then_click(BATTLE_START, interval=1):
                    stage = self.ARENA_NPC_STAGE_BATTLE
                    gray_retry = 0
                    auto_fight_clicks = 0
                    auto_fight_checked = False
                    battle_result_seen = False
                    battle_start_pending_timer.reset()
                    battle_start_grace_timer.reset()
                    auto_fight_enter_timer.reset()
                    auto_fight_click_interval.clear()
                    auto_fight_clear_confirm_timer.clear()
                    logger.info("Arena NPC: battle start")
                    timeout.reset()
                    continue

                if self.ui_additional():
                    timeout.reset()
                    continue

                continue

            if stage == self.ARENA_NPC_STAGE_BATTLE:
                if self.handle_popup_cancel(interval=1):
                    logger.info(
                        "Arena NPC: popup cancel after battle start, recheck arena flags"
                    )
                    flag_status = self._ocr_arena_flag_status(
                        skip_first_screenshot=False
                    )
                    if flag_status is None:
                        flag_status = self._stored_arena_flag_status()
                    if flag_status is not None and flag_status[0] <= 0:
                        logger.info(
                            "Arena NPC: arena flags exhausted after start popup cancel"
                        )
                        return "exhausted"

                    logger.warning(
                        "Arena NPC: popup cancel after battle start but arena flag is still unknown/non-zero"
                    )
                    stage = self.ARENA_NPC_STAGE_PREPARE
                    timeout.reset()
                    continue

                if fast_battle_effective:
                    if self.appear_then_click(FAST_BATTLE_RESULT_CONFIRM, interval=0.8):
                        logger.info("Arena NPC: fast battle result confirm")
                        battle_result_seen = True
                        timeout.reset()
                        continue

                if self.appear_then_click(
                    AUTO_BATTLE_RESULT_CONFIRM,
                    interval=self.ARENA_NPC_AUTO_RESULT_INTERVAL_SECONDS,
                ):
                    logger.info("Arena NPC: battle result confirm")
                    battle_result_seen = True
                    timeout.reset()
                    continue

                if (not fast_battle_effective) and (not battle_result_seen):
                    # OPPONENT visible => auto fight is OFF.
                    opponent_visible = self.appear(OPPONENT)

                    if opponent_visible:
                        auto_fight_checked = False
                        auto_fight_clear_confirm_timer.clear()
                        if (
                            auto_fight_enter_timer.reached()
                            and auto_fight_click_interval.reached()
                        ):
                            self.device.click_record_remove(AUTO_FIGHT)
                            self.device.click(AUTO_FIGHT)
                            auto_fight_clicks += 1
                            auto_fight_click_interval.reset()
                            logger.info(
                                f"Arena NPC: auto fight toggle ({auto_fight_clicks})"
                            )
                            if (
                                auto_fight_clicks
                                >= self.ARENA_NPC_AUTO_FIGHT_MAX_CLICKS
                            ):
                                if (
                                    not auto_fight_warn_timer.started()
                                    or auto_fight_warn_timer.reached()
                                ):
                                    logger.warning(
                                        f"Arena NPC: OPPONENT still visible after {auto_fight_clicks} auto-fight toggles"
                                    )
                                    auto_fight_warn_timer.reset()
                            timeout.reset()
                            continue
                    elif not auto_fight_checked:
                        auto_fight_warn_timer.clear()
                        if not auto_fight_clear_confirm_timer.started():
                            auto_fight_clear_confirm_timer.start()
                        elif auto_fight_clear_confirm_timer.reached():
                            auto_fight_checked = True
                            logger.info("Arena NPC: auto fight checked by OPPONENT")
                            timeout.reset()
                            continue

                if (
                    battle_start_grace_timer.reached()
                    and battle_start_pending_timer.reached()
                    and self._is_battle_prepare_page()
                ):
                    logger.info("Arena NPC: battle start pending timeout, retry start")
                    stage = self.ARENA_NPC_STAGE_PREPARE
                    timeout.reset()
                    continue

                if self.appear(NPC_OPPONENT) and self.appear(CHALLENGE):
                    stage = self.ARENA_NPC_STAGE_SELECT
                    timeout.reset()
                    continue

                if self.ui_additional():
                    timeout.reset()
                    continue

                if self.appear(AUTO_FIGHT_EXIST):
                    timeout.reset()
                    self.device.stuck_record_clear()
                    continue

                continue

            if self.ui_additional():
                timeout.reset()
                continue

            logger.info("Arena NPC: unknown state, recover to seek")
            stage = self.ARENA_NPC_STAGE_SEEK
            timeout.reset()
            continue

    def _run_npc_combat(self, skip_first_screenshot=True) -> bool:
        self._arena_npc_completed_rounds = 0
        use_fast_battle = getattr(self.config, "Arena_NPCCombatFastBattle", True)
        if use_fast_battle and is_background_repeat_combat_active(self.config):
            logger.info("Arena NPC: background repeat combat active, use normal battle")
            use_fast_battle = False
        flag_status = self._stored_arena_flag_status()
        if flag_status is not None and flag_status[0] <= 0:
            logger.info("Arena NPC: arena flag is already 0, skip combat")
            return True

        if self._arena_burnout_enabled():
            # Burnout mode is resource-bounded, not batch-bounded. The fresh
            # dashboard snapshot normally gives an exact target, including
            # stockpiled flags above the natural recovery cap. If OCR was not
            # available, keep running until the game reports no usable flag.
            target_count = flag_status[0] if flag_status is not None else None
            logger.info(
                f"Arena NPC: burnout target={target_count or 'until exhausted'}"
            )
        else:
            raw_count = getattr(self.config, "Arena_NPCCombatCount", 5)
            try:
                target_count = max(0, int(raw_count))
            except (TypeError, ValueError):
                logger.warning(f"Arena NPC count invalid: {raw_count}, fallback to 5")
                target_count = 5

            if target_count <= 0:
                logger.info("Arena NPC: target count <= 0, skip")
                return True

        logger.info(
            f"Arena NPC: target={target_count or 'until exhausted'}, fast_battle={use_fast_battle}"
        )
        completed = 0
        while target_count is None or completed < target_count:
            # Avoid stale click history across rounds triggering false-positive too-many-click.
            self.device.click_record_clear()
            status = self._npc_combat_once(
                use_fast_battle=use_fast_battle,
                skip_first_screenshot=skip_first_screenshot,
            )
            skip_first_screenshot = True

            if status == "completed":
                completed += 1
                self._arena_npc_completed_rounds = completed
                self._consume_stored_arena_flags(1)
                logger.info(f"Arena NPC round finished: {completed}/{target_count}")
                flag_status = self._stored_arena_flag_status()
                if (
                    flag_status is not None
                    and flag_status[0] <= 0
                    and (target_count is None or completed < target_count)
                ):
                    logger.info(
                        f"Arena NPC stop early: local arena flag depleted ({completed}/{target_count})"
                    )
                    return True
                continue

            if status == "exhausted":
                logger.info(
                    f"Arena NPC stop early: exhausted ({completed}/{target_count})"
                )
                return True

            logger.warning(f"Arena NPC round failed at {completed + 1}/{target_count}")
            return False

        logger.info(f"Arena NPC completed: {completed}/{target_count}")
        return True
