from module.base.timer import Timer
from module.logger import logger
from tasks.base.page import page_knights
from tasks.base.ui import UI
from tasks.knights.assets.assets_knights_main_page import (
    WEEKLY_REWARDS,
)
from tasks.knights.support import KnightsSupportMixin
from tasks.knights.team_battle import KnightsTeamBattleMixin
from tasks.knights.weekly_task import KnightsWeeklyTaskMixin
from tasks.knights.world_boss import KnightsWorldBossMixin


class Knights(
    KnightsWorldBossMixin,
    KnightsTeamBattleMixin,
    KnightsSupportMixin,
    KnightsWeeklyTaskMixin,
    UI,
):
    WEEKLY_REWARDS_COLOR_THRESHOLD = 30
    WEEKLY_REWARDS_CLICK_INTERVAL_SECONDS = 1

    def _enter_knights(self) -> bool:
        if not hasattr(self.device, "image") or self.device.image is None:
            self.device.screenshot()
        self.ui_goto(page_knights)
        return True

    def _settle_knights_home(self, skip_first_screenshot=True) -> bool:
        timeout = Timer(8, count=24).start()
        settle = Timer(1, count=2).start()

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached() or settle.reached():
                return True

            if self.handle_touch_to_close(interval=0.5):
                timeout.reset()
                settle.reset()
                continue
            if self.handle_network_error():
                timeout.reset()
                settle.reset()
                continue

    def _is_weekly_rewards_ready(self, interval=0) -> bool:
        self.device.stuck_record_add(WEEKLY_REWARDS)

        if interval and not self.interval_is_reached(WEEKLY_REWARDS, interval=interval):
            return False

        appear = WEEKLY_REWARDS.match_color(self.device.image, threshold=self.WEEKLY_REWARDS_COLOR_THRESHOLD)

        if appear and interval:
            self.interval_reset(WEEKLY_REWARDS, interval=interval)

        return appear

    def _claim_weekly_rewards(self, skip_first_screenshot=True) -> bool:
        logger.info("Knights: claim weekly rewards")
        timeout = Timer(12, count=36).start()
        no_action_confirm = Timer(2, count=6).start()
        claimed = False

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached() or no_action_confirm.reached():
                return claimed

            if self._is_weekly_rewards_ready(interval=self.WEEKLY_REWARDS_CLICK_INTERVAL_SECONDS):
                self.device.click(WEEKLY_REWARDS)
                claimed = True
                timeout.reset()
                no_action_confirm.reset()
                continue

            if self.handle_touch_to_close(interval=0.5):
                timeout.reset()
                no_action_confirm.reset()
                continue
            if self.handle_network_error():
                timeout.reset()
                no_action_confirm.reset()
                continue

    def run(self) -> bool:
        logger.hr("Knights", level=1)
        self._reset_team_battle_status_runtime()

        if not self.device.app_is_running():
            from tasks.login.login import Login

            Login(self.config, device=self.device).app_start()

        run_weekly_rewards = self.config.Knights_ClaimSigninRateReward
        run_weekly_task = self.config.Knights_WeeklyTask
        run_support = self.config.Knights_Support
        run_support_donate = run_support and any(
            [
                self.config.Knights_SupportLowerLevelFairyFlower,
                self.config.Knights_SupportBeginnerPenguin,
            ]
        )
        run_support_request = run_support
        run_team_battle = self.config.KnightsTeamBattle_TeamBattle
        run_world_boss = self.config.Knights_WorldBoss

        if not any(
            [
                run_weekly_rewards,
                run_weekly_task,
                run_support_donate,
                run_support_request,
                run_team_battle,
                run_world_boss,
            ]
        ):
            logger.warning("Knights: all sub tasks disabled")
            self.config.task_delay(server_update=True)
            return True

        if not self._enter_knights():
            return False

        self._settle_knights_home(skip_first_screenshot=True)

        if run_weekly_rewards:
            self._claim_weekly_rewards(skip_first_screenshot=True)

        success = True
        if run_team_battle:
            success = self.run_team_battle(skip_first_screenshot=True) and success
        if run_world_boss:
            success = self.run_world_boss(skip_first_screenshot=True) and success
        if run_support_donate or run_support_request:
            success = self.run_support(
                skip_first_screenshot=True,
                run_donate=run_support_donate,
                run_request=run_support_request,
            ) and success
            if not run_weekly_task:
                self.ui_goto(page_knights, skip_first_screenshot=True)
        if run_weekly_task:
            success = self.run_weekly_task(skip_first_screenshot=True) and success
            self.ui_goto(page_knights, skip_first_screenshot=True)

        reminder_target = self._get_team_battle_next_delay_target()
        if reminder_target is not None:
            self.config.task_delay(server_update=True, target=reminder_target)
        else:
            self.config.task_delay(server_update=True)
        return success
