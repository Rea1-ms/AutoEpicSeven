from module.base.timer import Timer
from module.logger import logger
from module.ocr.ocr import DigitCounter
from tasks.arena.dashboard import ArenaDashboardMixin
from tasks.arena.entry import ArenaEntryMixin
from tasks.arena.assets.assets_arena import (
    AUTO_FIGHT,
    AUTO_FIGHT_EXIST,
    BATTLE_PASS_CHECK,
    BATTLE_PASS_ENTRY,
    BATTLE_PASS_REWARDS,
    BATTLE_START,
    CHALLENGE,
    NPC_OPPONENT,
    AUTO_BATTLE_RESULT_CONFIRM,
    FAST_BATTLE_LOCKED,
    FAST_BATTLE_OFF,
    FAST_BATTLE_ON,
    FAST_BATTLE_RESULT_CONFIRM,
    OPPONENT,
    NPC_COMBAT_ENTRY,
    OCR_FAST_BATTLE_TIMES,
    WEEKLY_BATTLE_REWARDS,
)
from tasks.base.ui import UI


class OcrFastBattleTimes(DigitCounter):
    def after_process(self, result):
        result = super().after_process(result)
        result = result.replace("O", "0").replace("o", "0")
        result = result.replace("I", "1").replace("l", "1")
        result = result.replace("／", "/")
        result = result.replace(" ", "")
        return result


class Arena(ArenaEntryMixin, ArenaDashboardMixin, UI):
    """
    Arena task.

    Current scope:
        main page -> arena entry popup -> common arena entry
        handle weekly rewards popup branch before arena main page
    """

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
    ARENA_WEEKLY_BATTLE_REWARDS_COLOR_THRESHOLD = 30
    ARENA_WEEKLY_BATTLE_REWARDS_TIMEOUT_SECONDS = 6
    ARENA_WEEKLY_BATTLE_REWARDS_CONFIRM_SECONDS = 1
    ARENA_WEEKLY_BATTLE_REWARDS_MAX_CLICKS = 2
    ARENA_BATTLE_PASS_TIMEOUT_SECONDS = 18
    ARENA_BATTLE_PASS_BACK_INTERVAL_SECONDS = 1
    ARENA_BATTLE_PASS_SETTLE_SECONDS = 1.2
    ARENA_BATTLE_PASS_SCAN_SECONDS = 1.5
    ARENA_BATTLE_PASS_CLEAR_CONFIRM_SECONDS = 1.8
    ARENA_BATTLE_PASS_SAMPLE_COUNT = 3

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

    def _is_weekly_battle_rewards_ready(self) -> bool:
        return WEEKLY_BATTLE_REWARDS.match_color(
            self.device.image, threshold=self.ARENA_WEEKLY_BATTLE_REWARDS_COLOR_THRESHOLD
        )

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
        return (
            self.appear(BATTLE_START)
            or self.appear(FAST_BATTLE_ON)
            or self.appear(FAST_BATTLE_OFF)
            or self.appear(FAST_BATTLE_LOCKED)
        )

    def _ocr_fast_battle_times(self) -> tuple[int, int, int]:
        ocr = OcrFastBattleTimes(OCR_FAST_BATTLE_TIMES, lang="en", name="FastBattleTimes")
        # For fast battle, OCR format is "remaining/total" (e.g. 9/10, 10/10).
        current, remain, total = ocr.ocr_single_line(self.device.image)
        if total:
            logger.attr("FastBattleTimes", f"{current}/{total}")
        else:
            logger.warning(f"Fast battle times OCR invalid: {current}/{total}")
        return current, remain, total

    def _sample_battle_pass_rewards(
        self,
        duration: float,
        sample_count: int,
        expect_visible: bool,
        require_all: bool,
        skip_first_screenshot=True,
    ) -> bool:
        timer = Timer(duration, count=sample_count).start()
        matched = 0
        sampled = 0

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            visible = self.appear(BATTLE_PASS_REWARDS)
            if visible == expect_visible:
                matched += 1
                if not require_all:
                    return True
            elif require_all:
                return False

            sampled += 1
            if timer.reached():
                if require_all:
                    return sampled >= sample_count and matched >= sample_count
                return False

    def _claim_weekly_battle_rewards(self, skip_first_screenshot=True) -> bool:
        """
        Claim weekly battle rewards from arena page after NPC combat rounds.
        Do not treat one click as success immediately.
        Only return success after reward state is consumed on arena page.
        """
        if not getattr(self.config, "Arena_ClaimWeeklyBattleRewards", True):
            return False

        timeout = Timer(self.ARENA_WEEKLY_BATTLE_REWARDS_TIMEOUT_SECONDS, count=18).start()
        confirm_timer = Timer(self.ARENA_WEEKLY_BATTLE_REWARDS_CONFIRM_SECONDS, count=2).clear()
        stage = "detect"
        click_count = 0

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning("Arena: weekly battle rewards claim timeout")
                return False

            if stage == "detect":
                if not self._is_arena_page_ready(interval=0):
                    if self.ui_additional():
                        timeout.reset()
                        continue
                    continue

                if self._is_weekly_battle_rewards_ready():
                    self.device.click(WEEKLY_BATTLE_REWARDS)
                    click_count += 1
                    confirm_timer.reset()
                    stage = "confirm"
                    logger.info(f"Arena: claim weekly battle rewards (click {click_count})")
                    timeout.reset()
                    continue

                logger.warning(
                    "Arena: weekly battle rewards not detected on arena page "
                    f"(template={WEEKLY_BATTLE_REWARDS.match_template_luma(self.device.image)}, "
                    f"threshold={self.ARENA_WEEKLY_BATTLE_REWARDS_COLOR_THRESHOLD})"
                )
                return False

            if stage == "confirm":
                if self.ui_additional():
                    timeout.reset()
                    continue

                if not self._is_arena_page_ready(interval=0):
                    continue

                if not self._is_weekly_battle_rewards_ready():
                    logger.info("Arena: weekly battle rewards claimed")
                    return True

                if confirm_timer.reached():
                    if click_count < self.ARENA_WEEKLY_BATTLE_REWARDS_MAX_CLICKS:
                        logger.warning("Arena: weekly battle rewards click not consumed, retry")
                        stage = "detect"
                        timeout.reset()
                        continue

                    logger.warning(
                        "Arena: weekly battle rewards click not consumed "
                        f"after {click_count} clicks (template={WEEKLY_BATTLE_REWARDS.match_template_luma(self.device.image)})"
                    )
                    return False

                continue

    def _claim_battle_pass_rewards(self, skip_first_screenshot=True) -> bool:
        """
        Arena battle-pass flow:
            arena page -> BATTLE_PASS_ENTRY -> BATTLE_PASS_CHECK
            wait settle -> OCR level -> multi-frame scan BATTLE_PASS_REWARDS
            click once -> handle touch to close -> multi-frame clear confirm
            click BACK -> return arena page
        """
        if not getattr(self.config, "Arena_ClaimBattlePassRewards", True):
            return False

        timeout = Timer(self.ARENA_BATTLE_PASS_TIMEOUT_SECONDS, count=60).start()
        stage = "enter"
        reward_clicked = False
        level_ocr_done = False
        settle_timer = Timer(self.ARENA_BATTLE_PASS_SETTLE_SECONDS, count=2).clear()

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning("Arena: battle pass flow timeout")
                return reward_clicked

            if stage == "enter":
                if self.appear(BATTLE_PASS_CHECK):
                    logger.info("Arena: battle pass page reached")
                    stage = "settle"
                    settle_timer.reset()
                    timeout.reset()
                    continue

                if self._is_arena_page_ready(interval=0):
                    if self.appear_then_click(BATTLE_PASS_ENTRY, interval=1):
                        logger.info("Arena: enter battle pass")
                        timeout.reset()
                        continue

                if self.handle_touch_to_close(interval=0.5):
                    timeout.reset()
                    continue
                if self.ui_additional():
                    timeout.reset()
                    continue
                continue

            if stage == "settle":
                if not self.appear(BATTLE_PASS_CHECK):
                    if self.handle_touch_to_close(interval=0.5):
                        timeout.reset()
                        continue
                    if self.ui_additional():
                        timeout.reset()
                        continue
                    continue

                if settle_timer.reached():
                    stage = "scan"
                    timeout.reset()
                    continue

                if self.handle_touch_to_close(interval=0.5):
                    timeout.reset()
                    continue
                if self.ui_additional():
                    timeout.reset()
                    continue
                continue

            if stage == "scan":
                if not self.appear(BATTLE_PASS_CHECK):
                    if self.handle_touch_to_close(interval=0.5):
                        timeout.reset()
                        continue
                    if self.ui_additional():
                        timeout.reset()
                        continue
                    continue

                if not level_ocr_done:
                    self._ocr_arena_rank()
                    self.write_resource_bar_status(self._ocr_arena_resource_bar(skip_first_screenshot=True))
                    level_ocr_done = True

                if self._sample_battle_pass_rewards(
                    duration=self.ARENA_BATTLE_PASS_SCAN_SECONDS,
                    sample_count=self.ARENA_BATTLE_PASS_SAMPLE_COUNT,
                    expect_visible=True,
                    require_all=False,
                    skip_first_screenshot=True,
                ):
                    self.device.click(BATTLE_PASS_REWARDS)
                    reward_clicked = True
                    logger.info("Arena: claim battle pass rewards")
                    stage = "close_popup"
                    timeout.reset()
                    continue

                logger.info("Arena: battle pass rewards not found in current window")
                stage = "exit"
                timeout.reset()
                continue

            if stage == "close_popup":
                if self.handle_touch_to_close(interval=0.5):
                    timeout.reset()
                    continue

                if self.appear(BATTLE_PASS_CHECK):
                    stage = "verify_clear"
                    timeout.reset()
                    continue

                if self.ui_additional():
                    timeout.reset()
                    continue
                continue

            if stage == "verify_clear":
                if not self.appear(BATTLE_PASS_CHECK):
                    if self.handle_touch_to_close(interval=0.5):
                        timeout.reset()
                        continue
                    if self.ui_additional():
                        timeout.reset()
                        continue
                    continue

                if self._sample_battle_pass_rewards(
                    duration=self.ARENA_BATTLE_PASS_CLEAR_CONFIRM_SECONDS,
                    sample_count=self.ARENA_BATTLE_PASS_SAMPLE_COUNT,
                    expect_visible=False,
                    require_all=True,
                    skip_first_screenshot=True,
                ):
                    logger.info("Arena: battle pass rewards cleared")
                    stage = "exit"
                    timeout.reset()
                    continue

                logger.info("Arena: battle pass rewards still claimable, retry")
                stage = "scan"
                timeout.reset()
                continue

            if stage == "exit":
                if self._is_arena_page_ready(interval=0):
                    return reward_clicked

                if self.handle_ui_back(BATTLE_PASS_CHECK, interval=self.ARENA_BATTLE_PASS_BACK_INTERVAL_SECONDS):
                    timeout.reset()
                    continue

                if self.ui_additional():
                    timeout.reset()
                    continue
                continue

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
        entry_click_timer = Timer(self.ARENA_NPC_ENTRY_CLICK_INTERVAL_SECONDS, count=0).clear()
        opponent_click_timer = Timer(self.ARENA_NPC_OPPONENT_CLICK_INTERVAL_SECONDS, count=0).clear()
        seek_non_npc_timer = Timer(self.ARENA_NPC_SEEK_NON_NPC_STABLE_SECONDS, count=2).clear()
        select_lost_timer = Timer(self.ARENA_NPC_SELECT_LOST_STABLE_SECONDS, count=2).clear()
        auto_fight_enter_timer = Timer(self.ARENA_NPC_AUTO_FIGHT_ENTER_SECONDS, count=3).start()
        auto_fight_click_interval = Timer(self.ARENA_NPC_AUTO_FIGHT_CLICK_INTERVAL_SECONDS, count=4).clear()
        auto_fight_clear_confirm_timer = Timer(self.ARENA_NPC_AUTO_FIGHT_CLEAR_CONFIRM_SECONDS, count=2).clear()
        auto_fight_warn_timer = Timer(self.ARENA_NPC_AUTO_FIGHT_WARN_INTERVAL_SECONDS, count=0).clear()
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
                    seek_non_npc_timer.clear()
                    stage = self.ARENA_NPC_STAGE_SELECT
                    timeout.reset()
                    continue

                if entry_click_timer.reached() and self.appear_then_click(NPC_COMBAT_ENTRY, interval=0):
                    logger.info("Arena NPC: enter NPC combat")
                    entry_click_timer.reset()
                    timeout.reset()
                    continue

                # In real-opponent page, CHALLENGE exists but NPC_OPPONENT does not.
                if self.appear(CHALLENGE):
                    if not seek_non_npc_timer.started():
                        seek_non_npc_timer.start()
                    elif seek_non_npc_timer.reached() and entry_click_timer.reached() and self.appear_then_click(
                        NPC_COMBAT_ENTRY, interval=0
                    ):
                        logger.info("Arena NPC: non-NPC challenge page detected, switch to NPC combat")
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
                    if opponent_click_timer.reached() and self.appear_then_click(NPC_OPPONENT, interval=0):
                        gray_retry += 1
                        logger.info(f"Arena NPC: challenge gray, rotate opponent ({gray_retry})")
                        if gray_retry >= self.ARENA_NPC_GRAY_RETRY_LIMIT:
                            logger.info("Arena NPC: challenge unavailable after retries")
                            return "exhausted"
                        opponent_click_timer.reset()
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
                    # Some challenge taps are visually accepted but not transitioned.
                    # Retry once in-place before falling back to lane recovery.
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
                    logger.info("Arena NPC: fast battle locked, fallback to normal battle")
                    fast_battle_effective = False
                    fast_times_checked = True

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
                    logger.info("Arena NPC: popup cancel after battle start, recheck arena flags")
                    flag_status = self._ocr_arena_flag_status(skip_first_screenshot=False)
                    if flag_status is None:
                        flag_status = self._stored_arena_flag_status()
                    if flag_status is not None and flag_status[0] <= 0:
                        logger.info("Arena NPC: arena flags exhausted after start popup cancel")
                        return "exhausted"

                    logger.warning("Arena NPC: popup cancel after battle start but arena flag is still unknown/non-zero")
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
                    AUTO_BATTLE_RESULT_CONFIRM, interval=self.ARENA_NPC_AUTO_RESULT_INTERVAL_SECONDS
                ):
                    logger.info("Arena NPC: battle result confirm")
                    battle_result_seen = True
                    timeout.reset()
                    continue

                if (not fast_battle_effective) and (not battle_result_seen):
                    # OPPONENT visible => auto fight is OFF.
                    OPPONENT_visible = self.appear(OPPONENT)

                    if OPPONENT_visible:
                        auto_fight_checked = False
                        auto_fight_clear_confirm_timer.clear()
                        if auto_fight_enter_timer.reached() and auto_fight_click_interval.reached():
                            self.device.click_record_remove(AUTO_FIGHT)
                            self.device.click(AUTO_FIGHT)
                            auto_fight_clicks += 1
                            auto_fight_click_interval.reset()
                            logger.info(f"Arena NPC: auto fight toggle ({auto_fight_clicks})")
                            if auto_fight_clicks >= self.ARENA_NPC_AUTO_FIGHT_MAX_CLICKS:
                                if not auto_fight_warn_timer.started() or auto_fight_warn_timer.reached():
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

                if self.appear(AUTO_FIGHT_EXIST):
                    timeout.reset()
                    self.device.stuck_record_clear()
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

        flag_status = self._stored_arena_flag_status()
        if flag_status is not None and flag_status[0] <= 0:
            logger.info("Arena NPC: arena flag is already 0, skip combat")
            return True

        logger.info(f"Arena NPC: target={target_count}, fast_battle={use_fast_battle}")
        completed = 0
        while completed < target_count:
            # Avoid stale click history across rounds triggering false-positive too-many-click.
            self.device.click_record_clear()
            status = self._npc_combat_once(use_fast_battle=use_fast_battle, skip_first_screenshot=skip_first_screenshot)
            skip_first_screenshot = True

            if status == "completed":
                completed += 1
                self._consume_stored_arena_flags(1)
                logger.info(f"Arena NPC round finished: {completed}/{target_count}")
                flag_status = self._stored_arena_flag_status()
                if flag_status is not None and flag_status[0] <= 0 and completed < target_count:
                    logger.info(f"Arena NPC stop early: local arena flag depleted ({completed}/{target_count})")
                    return True
                continue

            if status == "exhausted":
                logger.info(f"Arena NPC stop early: exhausted ({completed}/{target_count})")
                return True

            logger.warning(f"Arena NPC round failed at {completed + 1}/{target_count}")
            return False

        logger.info(f"Arena NPC completed: {completed}/{target_count}")
        return True

    def run(self) -> bool:
        logger.hr("Arena", level=1)

        if not self.device.app_is_running():
            from tasks.login.login import Login

            Login(self.config, device=self.device).app_start()

        if not hasattr(self.device, "image") or self.device.image is None:
            self.device.screenshot()

        # Fast-path: if task starts inside arena/NPC context, do not force return to main page.
        # This keeps manual "already in arena" starts seamless.
        if getattr(self.config, "Arena_NPCCombat", False):
            if self.appear(NPC_OPPONENT) or self.appear(CHALLENGE) or self._is_battle_prepare_page():
                logger.info("Arena: detected NPC combat context, skip goto main")
                self._update_arena_dashboard_snapshot(skip_first_screenshot=True)
                if not self._run_npc_combat(skip_first_screenshot=True):
                    self.config.task_delay(success=False)
                    return False
                self._claim_weekly_battle_rewards(skip_first_screenshot=True)
                self._claim_battle_pass_rewards(skip_first_screenshot=True)
                self.config.task_call("DataUpdate", force_call=False)
                self.config.task_delay(server_update=True)
                return True

        status = self._enter_arena(skip_first_screenshot=True)

        if status == "settling":
            self.config.task_delay(server_update=True)
            return True

        if status == "entered":
            self._update_arena_dashboard_snapshot(skip_first_screenshot=True)
            if getattr(self.config, "Arena_NPCCombat", False):
                if not self._run_npc_combat(skip_first_screenshot=True):
                    self.config.task_delay(success=False)
                    return False
                self._claim_weekly_battle_rewards(skip_first_screenshot=True)
                self._claim_battle_pass_rewards(skip_first_screenshot=True)

            self.config.task_call("DataUpdate", force_call=False)
            self.config.task_delay(server_update=True)
            return True

        self.config.task_delay(success=False)
        return False
