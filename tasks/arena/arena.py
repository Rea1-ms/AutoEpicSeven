from module.base.timer import Timer
from module.logger import logger
from module.ocr.ocr import DigitCounter
from tasks.arena.assets.assets_arena import (
    ARENA_CHECK,
    ARENA_COMMON_ENTRY,
    ARENA_ENTRY,
    ARENA_SETTLING,
    AUTO_FIGHT,
    BATTLE_START,
    CHALLENGE,
    NPC_OPPONENT,
    WEEKLY_REWARDS_CHECK,
    WEEKLY_REWARDS_CLAIM,
    WEEKLY_REWARDS_SELECTED,
    AUTO_BATTLE_RESULT_CONFIRM,
    FAST_BATTLE_OFF,
    FAST_BATTLE_ON,
    FAST_BATTLE_RESULT_CONFIRM,
    NPC_COMBAT_ENTRY,
    OCR_FAST_BATTLE_TIMES,
)
from tasks.base.page import page_main
from tasks.base.ui import UI


class OcrFastBattleTimes(DigitCounter):
    def after_process(self, result):
        result = super().after_process(result)
        result = result.replace("O", "0").replace("o", "0")
        result = result.replace("I", "1").replace("l", "1")
        result = result.replace("／", "/")
        result = result.replace(" ", "")
        return result


class Arena(UI):
    """
    Arena task.

    Current scope:
        main page -> arena entry popup -> common arena entry
        handle weekly rewards popup branch before arena main page
    """

    ARENA_ENTRY_TIMEOUT_SECONDS = 45
    ARENA_ENTRY_RETRY_SECONDS = 1.2
    ARENA_CHECK_LUMA_SIMILARITY = 0.8
    ARENA_CHECK_COLOR_THRESHOLD = 5
    ARENA_NPC_ROUND_TIMEOUT_SECONDS = 90
    ARENA_NPC_CHALLENGE_LUMA_SIMILARITY = 0.8
    ARENA_NPC_CHALLENGE_COLOR_THRESHOLD = 30
    ARENA_NPC_AUTO_RESULT_INTERVAL_SECONDS = 3
    ARENA_NPC_FAST_TOGGLE_INTERVAL_SECONDS = 0.8
    ARENA_NPC_GRAY_RETRY_LIMIT = 8
    ARENA_NPC_CHALLENGE_PENDING_SECONDS = 4.5
    ARENA_NPC_BATTLE_START_PENDING_SECONDS = 6

    ARENA_NPC_STAGE_SEEK = "seek_npc_lane"
    ARENA_NPC_STAGE_SELECT = "select_opponent"
    ARENA_NPC_STAGE_PENDING = "challenge_pending"
    ARENA_NPC_STAGE_PREPARE = "battle_prepare"
    ARENA_NPC_STAGE_BATTLE = "battle_running"

    def _is_arena_page_ready(self, interval=0) -> bool:
        """
        ARENA_CHECK uses luma + color double check:
            avoid false-positive when weekly rewards popup overlays arena page.
        """
        self.device.stuck_record_add(ARENA_CHECK)

        if interval and not self.interval_is_reached(ARENA_CHECK, interval=interval):
            return False

        appear = False
        if ARENA_CHECK.match_template_luma(self.device.image, similarity=self.ARENA_CHECK_LUMA_SIMILARITY):
            if ARENA_CHECK.match_color(self.device.image, threshold=self.ARENA_CHECK_COLOR_THRESHOLD):
                appear = True

        if appear and interval:
            self.interval_reset(ARENA_CHECK, interval=interval)

        return appear

    def _handle_weekly_rewards_popup(self) -> bool:
        """
        Handle weekly rewards popup branch during arena entry.

        Returns:
            bool: True if an action is taken.
        """
        # Step 1: detect weekly rewards layer by selected marker.
        # Do not use interval here, otherwise the following click on the same
        # asset can be blocked by interval timer.
        if not self.appear(WEEKLY_REWARDS_SELECTED):
            self._arena_weekly_selected_clicked = False
            return False

        logger.info("Arena: weekly rewards popup detected")

        # Step 2: click selected entry once, then wait check marker.
        if not getattr(self, "_arena_weekly_selected_clicked", False):
            if self.appear_then_click(WEEKLY_REWARDS_SELECTED, interval=1):
                self._arena_weekly_selected_clicked = True
                logger.info("Arena: weekly rewards selected")
                return True
            return False

        # Step 3: verify selected state by WEEKLY_REWARDS_CHECK, then claim.
        if not self.appear(WEEKLY_REWARDS_CHECK):
            return False

        if not self.config.Arena_ClaimWeeklyRewards:
            logger.info("Arena: weekly rewards claim disabled by config")
            return True

        if self.appear_then_click(WEEKLY_REWARDS_CLAIM, interval=1):
            logger.info("Arena: weekly rewards claimed")
            return True

        return False

    def _is_challenge_ready(self, interval=0) -> bool:
        self.device.stuck_record_add(CHALLENGE)

        if interval and not self.interval_is_reached(CHALLENGE, interval=interval):
            return False

        appear = False
        if CHALLENGE.match_template_luma(self.device.image, similarity=self.ARENA_NPC_CHALLENGE_LUMA_SIMILARITY):
            if CHALLENGE.match_color(self.device.image, threshold=self.ARENA_NPC_CHALLENGE_COLOR_THRESHOLD):
                appear = True

        if appear and interval:
            self.interval_reset(CHALLENGE, interval=interval)

        return appear

    def _is_challenge_exhausted(self) -> bool:
        if CHALLENGE.match_template_luma(self.device.image, similarity=self.ARENA_NPC_CHALLENGE_LUMA_SIMILARITY):
            return not CHALLENGE.match_color(self.device.image, threshold=self.ARENA_NPC_CHALLENGE_COLOR_THRESHOLD)
        return False

    def _ensure_fast_battle_state(self, enabled: bool) -> bool:
        """
        Returns:
            bool: True when fast-battle state already matches `enabled`.
        """
        if enabled:
            if self.appear(FAST_BATTLE_ON):
                return True
            if self.appear_then_click(FAST_BATTLE_OFF, interval=self.ARENA_NPC_FAST_TOGGLE_INTERVAL_SECONDS):
                logger.info("Arena NPC: enable fast battle")
            return False

        if self.appear(FAST_BATTLE_OFF):
            return True
        if self.appear_then_click(FAST_BATTLE_ON, interval=self.ARENA_NPC_FAST_TOGGLE_INTERVAL_SECONDS):
            logger.info("Arena NPC: disable fast battle")
        return False

    def _is_battle_prepare_page(self) -> bool:
        """
        Battle-prepare page can be identified by either start button or fast-battle toggle.
        """
        return self.appear(BATTLE_START) or self.appear(FAST_BATTLE_ON) or self.appear(FAST_BATTLE_OFF)

    def _ocr_fast_battle_times(self) -> tuple[int, int, int]:
        ocr = OcrFastBattleTimes(OCR_FAST_BATTLE_TIMES, lang="en", name="FastBattleTimes")
        # For fast battle, OCR format is "remaining/total" (e.g. 9/10, 10/10).
        current, remain, total = ocr.ocr_single_line(self.device.image)
        if total:
            logger.attr("FastBattleTimes", f"{current}/{total}")
        else:
            logger.warning(f"Fast battle times OCR invalid: {current}/{total}")
        return current, remain, total

    def _npc_combat_once(self, use_fast_battle: bool, skip_first_screenshot=True) -> str:
        """
        Returns:
            str: completed / exhausted / failed
        """
        timeout = Timer(self.ARENA_NPC_ROUND_TIMEOUT_SECONDS, count=360).start()
        stage = self.ARENA_NPC_STAGE_SEEK
        gray_retry = 0
        challenge_pending_timer = Timer(self.ARENA_NPC_CHALLENGE_PENDING_SECONDS, count=0).start()
        battle_start_pending_timer = Timer(self.ARENA_NPC_BATTLE_START_PENDING_SECONDS, count=0).start()
        battle_start_grace_timer = Timer(2, count=0).start()
        fast_battle_effective = bool(use_fast_battle)
        fast_times_checked = False
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
            if stage == self.ARENA_NPC_STAGE_BATTLE and self._is_arena_page_ready(interval=0):
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
                    stage = self.ARENA_NPC_STAGE_SELECT
                    timeout.reset()
                    continue

                if self.appear_then_click(NPC_COMBAT_ENTRY, interval=1):
                    logger.info("Arena NPC: enter NPC combat")
                    timeout.reset()
                    continue

                # In real-opponent page, CHALLENGE exists but NPC_OPPONENT does not.
                if self.appear(CHALLENGE) and self.appear_then_click(NPC_COMBAT_ENTRY, interval=0.8):
                    logger.info("Arena NPC: non-NPC challenge page detected, switch to NPC combat")
                    timeout.reset()
                    continue

                if self.ui_additional():
                    timeout.reset()
                    continue

                continue

            if stage == self.ARENA_NPC_STAGE_SELECT:
                if self._is_battle_prepare_page():
                    stage = self.ARENA_NPC_STAGE_PREPARE
                    timeout.reset()
                    continue

                # CHALLENGE is only valid on NPC list page.
                if not self.appear(NPC_OPPONENT):
                    stage = self.ARENA_NPC_STAGE_SEEK
                    continue

                if self._is_challenge_ready(interval=1):
                    self.device.click(CHALLENGE)
                    logger.info("Arena NPC: challenge")
                    stage = self.ARENA_NPC_STAGE_PENDING
                    gray_retry = 0
                    challenge_pending_timer.reset()
                    timeout.reset()
                    continue

                if self._is_challenge_exhausted():
                    gray_retry += 1
                    if gray_retry >= self.ARENA_NPC_GRAY_RETRY_LIMIT:
                        logger.info("Arena NPC: challenge unavailable after retries")
                        return "exhausted"
                    logger.info(f"Arena NPC: challenge gray, rotate opponent ({gray_retry})")
                    if self.appear_then_click(NPC_OPPONENT, interval=0.5):
                        timeout.reset()
                        continue

                if self.appear_then_click(NPC_OPPONENT, interval=1):
                    logger.info("Arena NPC: select opponent")
                    gray_retry = 0
                    timeout.reset()
                    continue

                if self.appear_then_click(NPC_COMBAT_ENTRY, interval=1.5):
                    logger.info("Arena NPC: re-enter NPC combat lane")
                    stage = self.ARENA_NPC_STAGE_SEEK
                    timeout.reset()
                    continue

                if self.ui_additional():
                    timeout.reset()
                    continue

                continue

            # After CHALLENGE click, wait for battle prepare page.
            if stage == self.ARENA_NPC_STAGE_PENDING:
                if self._is_battle_prepare_page():
                    stage = self.ARENA_NPC_STAGE_PREPARE
                    timeout.reset()
                    continue

                if challenge_pending_timer.reached():
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

                # User enabled fast battle, but daily quota may be exhausted.
                if fast_battle_effective and (not fast_times_checked):
                    if self.appear(FAST_BATTLE_ON) or self.appear(FAST_BATTLE_OFF):
                        remaining, _, total = self._ocr_fast_battle_times()
                        fast_times_checked = True
                        if total > 0 and remaining <= 0:
                            logger.info("Arena NPC: fast battle exhausted by OCR, fallback to normal battle")
                            fast_battle_effective = False

                if not self._ensure_fast_battle_state(fast_battle_effective):
                    timeout.reset()
                    continue

                if self.appear_then_click(BATTLE_START, interval=1):
                    stage = self.ARENA_NPC_STAGE_BATTLE
                    gray_retry = 0
                    battle_start_pending_timer.reset()
                    battle_start_grace_timer.reset()
                    logger.info("Arena NPC: battle start")
                    timeout.reset()
                    continue

                if self.ui_additional():
                    timeout.reset()
                    continue

                continue

            if stage == self.ARENA_NPC_STAGE_BATTLE:
                if fast_battle_effective:
                    if self.appear_then_click(FAST_BATTLE_RESULT_CONFIRM, interval=0.8):
                        logger.info("Arena NPC: fast battle result confirm")
                        timeout.reset()
                        continue
                else:
                    if self.appear_then_click(AUTO_FIGHT, interval=1):
                        timeout.reset()
                        continue

                if self.appear_then_click(
                    AUTO_BATTLE_RESULT_CONFIRM, interval=self.ARENA_NPC_AUTO_RESULT_INTERVAL_SECONDS
                ):
                    logger.info("Arena NPC: battle result confirm")
                    timeout.reset()
                    continue

                # Keep battle stage stable for a short grace period after start click.
                # This avoids immediate fallback causing repeated BATTLE_START clicks.
                if (
                    battle_start_grace_timer.reached()
                    and battle_start_pending_timer.reached()
                    and self._is_battle_prepare_page()
                ):
                    logger.info("Arena NPC: battle start pending timeout, retry start")
                    stage = self.ARENA_NPC_STAGE_PREPARE
                    timeout.reset()
                    continue

                # If unexpectedly back to NPC list page, recover state.
                if self.appear(NPC_OPPONENT) and self.appear(CHALLENGE):
                    stage = self.ARENA_NPC_STAGE_SELECT
                    timeout.reset()
                    continue

                if self.ui_additional():
                    timeout.reset()
                    continue

                continue

            if self.ui_additional():
                timeout.reset()
                continue

            # Unknown visual state, recover by returning to seek state.
            logger.info("Arena NPC: unknown state, recover to seek")
            stage = self.ARENA_NPC_STAGE_SEEK
            timeout.reset()
            continue

    def _run_npc_combat(self, skip_first_screenshot=True) -> bool:
        use_fast_battle = getattr(self.config, "Arena_NPCCombatFastBattle", True)
        raw_count = getattr(self.config, "Arena_NPCCombatCount", 5)
        try:
            target_count = max(0, int(raw_count))
        except (TypeError, ValueError):
            logger.warning(f"Arena NPC count invalid: {raw_count}, fallback to 5")
            target_count = 5

        if target_count <= 0:
            logger.info("Arena NPC: target count <= 0, skip")
            return True

        logger.info(f"Arena NPC: target={target_count}, fast_battle={use_fast_battle}")
        completed = 0
        while completed < target_count:
            status = self._npc_combat_once(use_fast_battle=use_fast_battle, skip_first_screenshot=skip_first_screenshot)
            skip_first_screenshot = True

            if status == "completed":
                completed += 1
                logger.info(f"Arena NPC round finished: {completed}/{target_count}")
                continue

            if status == "exhausted":
                logger.info(f"Arena NPC stop early: exhausted ({completed}/{target_count})")
                return True

            logger.warning(f"Arena NPC round failed at {completed + 1}/{target_count}")
            return False

        logger.info(f"Arena NPC completed: {completed}/{target_count}")
        return True

    def _enter_arena(self, skip_first_screenshot=True) -> str:
        logger.info("Arena: enter")
        timeout = Timer(self.ARENA_ENTRY_TIMEOUT_SECONDS, count=180).start()
        entry_retry = Timer(self.ARENA_ENTRY_RETRY_SECONDS, count=0).start()
        self._arena_weekly_selected_clicked = False

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning("Arena entry timeout")
                return "failed"

            # End condition: arena page reached.
            if self._is_arena_page_ready(interval=1):
                logger.info("Arena page reached")
                return "entered"

            # Arena maintenance/settling period.
            # Close by AD_BUFF_X_CLOSE and finish this task early.
            if self.appear(ARENA_SETTLING, interval=1):
                if self.handle_ad_buff_x_close(interval=0.5):
                    logger.info("Arena is in settling period, skip until next server update")
                    return "settling"
                continue

            # Weekly rewards branch: selected -> check -> claim.
            if self._handle_weekly_rewards_popup():
                timeout.reset()
                continue

            # Popup branch: choose common arena entry.
            if self.appear_then_click(ARENA_COMMON_ENTRY, interval=1):
                logger.info("Arena popup: choose common arena")
                timeout.reset()
                continue

            # IMPORTANT:
            # Do not call ui_additional() here, otherwise AD_BUFF_X_CLOSE may
            # close the arena mode popup before selecting common arena.
            if self.handle_touch_to_close(interval=0.5):
                timeout.reset()
                continue
            if self.handle_network_error():
                timeout.reset()
                continue

            # Step 1: click ARENA_ENTRY on main page.
            if self.appear(page_main.check_button) and entry_retry.reached():
                self.device.click(ARENA_ENTRY)
                entry_retry.reset()
                timeout.reset()
                logger.info("Arena: main page -> arena entry")
                continue

    def run(self) -> bool:
        logger.hr("Arena", level=1)

        if not self.device.app_is_running():
            from tasks.login.login import Login

            Login(self.config, device=self.device).app_start()

        if not hasattr(self.device, "image") or self.device.image is None:
            self.device.screenshot()

        self.ui_goto_main()
        status = self._enter_arena(skip_first_screenshot=True)

        if status == "settling":
            self.config.task_delay(server_update=True)
            return True

        if status == "entered":
            if getattr(self.config, "Arena_NPCCombat", False):
                if not self._run_npc_combat(skip_first_screenshot=True):
                    self.config.task_delay(success=False)
                    return False

            self.config.task_delay(server_update=True)
            return True

        self.config.task_delay(success=False)
        return False
