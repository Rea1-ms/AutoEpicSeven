from module.base.timer import Timer
from module.logger import logger
from tasks.base.page import page_knights
from tasks.base.ui import UI
from tasks.knights.assets.assets_knights import SIGNIN_RATE_REWARD
from tasks.knights.donate import KnightsDonateMixin
from tasks.knights.expedition import KnightsExpeditionMixin
from tasks.knights.support import KnightsSupportMixin
from tasks.knights.weekly_task import KnightsWeeklyTaskMixin
from tasks.knights.world_boss import KnightsWorldBossMixin


class Knights(
    KnightsWorldBossMixin,
    KnightsExpeditionMixin,
    KnightsSupportMixin,
    KnightsDonateMixin,
    KnightsWeeklyTaskMixin,
    UI,
):
    """
    Epic Seven 骑士团任务
    """

    SIGNIN_RATE_REWARD_LUMA_SIMILARITY = 0.8
    SIGNIN_RATE_REWARD_COLOR_THRESHOLD = 30

    def _enter_knights(self) -> bool:
        if not hasattr(self.device, "image") or self.device.image is None:
            self.device.screenshot()
        self.ui_goto(page_knights)
        return True

    def _is_signin_reward_ready(self, interval=0) -> bool:
        """
        SIGNIN_RATE_REWARD uses luma + color double check.
        """
        self.device.stuck_record_add(SIGNIN_RATE_REWARD)

        if interval and not self.interval_is_reached(SIGNIN_RATE_REWARD, interval=interval):
            return False

        appear = False
        if SIGNIN_RATE_REWARD.match_template_luma(self.device.image, similarity=self.SIGNIN_RATE_REWARD_LUMA_SIMILARITY):
            if SIGNIN_RATE_REWARD.match_color(self.device.image, threshold=self.SIGNIN_RATE_REWARD_COLOR_THRESHOLD):
                appear = True

        if appear and interval:
            self.interval_reset(SIGNIN_RATE_REWARD, interval=interval)

        return appear

    def _claim_signin_reward(self, skip_first_screenshot=True) -> bool:
        logger.info("Knights: claim signin rate reward")
        timeout = Timer(12, count=36).start()
        no_action_confirm = Timer(2, count=6).start()
        claimed = False

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                return claimed

            if self._is_signin_reward_ready(interval=1):
                self.device.click(SIGNIN_RATE_REWARD)
                claimed = True
                timeout.reset()
                no_action_confirm.reset()
                continue

            if self.handle_touch_to_close(interval=1):
                timeout.reset()
                no_action_confirm.reset()
                continue
            if self.ui_additional():
                timeout.reset()
                no_action_confirm.reset()
                continue
            if self.handle_network_error():
                timeout.reset()
                no_action_confirm.reset()
                continue

            if no_action_confirm.reached():
                return claimed

    def run(self) -> bool:
        logger.hr("Knights", level=1)

        if not self.device.app_is_running():
            from tasks.login.login import Login

            Login(self.config, device=self.device).app_start()

        run_signin = self.config.KnightsBasic_ClaimSigninRateReward
        run_weekly_task = self.config.KnightsBasic_WeeklyTask
        run_donate = self.config.KnightsDonate_Donate
        run_support = self.config.KnightsDonate_Support
        run_expedition = self.config.KnightsExpedition_Expedition
        run_world_boss = self.config.KnightsExpedition_WorldBoss

        if not any([run_signin, run_weekly_task, run_donate, run_support, run_expedition, run_world_boss]):
            logger.warning("Knights: all sub tasks disabled")
            self.config.task_delay(server_update=True)
            return True

        if not self._enter_knights():
            return False

        if run_signin:
            self._claim_signin_reward(skip_first_screenshot=True)

        success = True
        if run_expedition:
            success = self.run_expedition(skip_first_screenshot=True) and success
        if run_world_boss:
            success = self.run_world_boss(skip_first_screenshot=True) and success
        if run_donate:
            success = self.run_donate(skip_first_screenshot=True) and success
        if run_support:
            success = self.run_support(skip_first_screenshot=True) and success
        if run_weekly_task:
            success = self.run_weekly_task(skip_first_screenshot=True) and success

        self.config.task_delay(server_update=True)
        return success
