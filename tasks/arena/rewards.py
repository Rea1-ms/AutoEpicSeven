from datetime import datetime, timedelta

from module.base.timer import Timer
from module.logger import logger
from tasks.arena.assets.assets_arena import (
    BATTLE_PASS_CHECK,
    BATTLE_PASS_ENTRY,
    BATTLE_PASS_REWARDS,
    WEEKLY_BATTLE_REWARDS,
)


def next_battle_pass_recheck(recorded_at: datetime) -> datetime:
    """Return the first Thursday 11:00 after the last stored MAX record."""
    days_until_thursday = (3 - recorded_at.weekday()) % 7
    recheck = (recorded_at + timedelta(days=days_until_thursday)).replace(
        hour=11,
        minute=0,
        second=0,
        microsecond=0,
    )
    if recheck <= recorded_at:
        recheck += timedelta(days=7)
    return recheck


class ArenaRewardsMixin:
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

    def _is_weekly_battle_rewards_ready(self) -> bool:
        return WEEKLY_BATTLE_REWARDS.match_color(
            self.device.image,
            threshold=self.ARENA_WEEKLY_BATTLE_REWARDS_COLOR_THRESHOLD,
        )

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

        timeout = Timer(
            self.ARENA_WEEKLY_BATTLE_REWARDS_TIMEOUT_SECONDS, count=18
        ).start()
        confirm_timer = Timer(
            self.ARENA_WEEKLY_BATTLE_REWARDS_CONFIRM_SECONDS, count=2
        ).clear()
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
                    logger.info(
                        f"Arena: claim weekly battle rewards (click {click_count})"
                    )
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
                        logger.warning(
                            "Arena: weekly battle rewards click not consumed, retry"
                        )
                        stage = "detect"
                        timeout.reset()
                        continue

                    logger.warning(
                        "Arena: weekly battle rewards click not consumed "
                        f"after {click_count} clicks "
                        f"(template={WEEKLY_BATTLE_REWARDS.match_template_luma(self.device.image)})"
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

        arena_rank = self.config.stored.ArenaRank
        if arena_rank.value >= arena_rank.FIXED_TOTAL:
            recheck = next_battle_pass_recheck(arena_rank.time)
            if datetime.now() < recheck:
                # Maintenance is not weekly, but season maintenance always
                # happens on Thursday. After MAX is confirmed, checking once
                # after each Thursday 11:00 is enough. If the pass is still
                # MAX, OCR refreshes the record and moves the next check to
                # the following Thursday instead of retrying every day.
                logger.info(
                    "Arena: battle pass already max level, skip reward check "
                    f"until Thursday recheck at {recheck}"
                )
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
                    self.write_resource_bar_status(
                        self._ocr_arena_resource_bar(skip_first_screenshot=True)
                    )
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

                if self.handle_ui_back(
                    BATTLE_PASS_CHECK,
                    interval=self.ARENA_BATTLE_PASS_BACK_INTERVAL_SECONDS,
                ):
                    timeout.reset()
                    continue

                if self.ui_additional():
                    timeout.reset()
                    continue
                continue
