import module.config.server as server
from module.base.timer import Timer
from module.logger import logger
from tasks.activity.assets.assets_activity_special_26_9_17 import (
    CHUN_ALL_TASK_DONE,
    CHUN_GATE_CHECK,
    CHUN_GATE_SELECTED,
    CHUN_TASK_REWARD_NONE,
    CHUN_TASK_REWARD_PENDING,
)
from tasks.activity.navigation import ActivityNavigationMixin
from tasks.activity.scheduling import mark_activity_checked
from tasks.base.page import page_common_activity, page_main
from tasks.base.ui import UI


class KoharuRaffle(ActivityNavigationMixin, UI):
    """Claim available Koharu task rewards without following task shortcuts."""

    CLAIM_FLOW_TIMEOUT_SECONDS = 30

    def __init__(self, config, device=None, task=None, activity_id="koharu_raffle_2026_09_17"):
        super().__init__(config=config, device=device, task=task)
        self.activity_id = activity_id

    def run_claim(self, skip_first_screenshot=True) -> bool:
        """Collect the first available task until no claim remains.

        Pages:
            in: page_main, any
            out: page_common_activity, Koharu task list with no available reward
        """
        self.ui_goto(page_common_activity, skip_first_screenshot=skip_first_screenshot)
        if not self.select_activity("收集抽奖券", CHUN_GATE_SELECTED):
            return False

        timeout = Timer(self.CLAIM_FLOW_TIMEOUT_SECONDS, count=60).start()
        scroll_to_top = True
        awaiting_reward_popup = False

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            page_ready = self.match_template_color(CHUN_GATE_CHECK)
            # Claimed tasks move below unfinished tasks, as in the summer
            # event. Only a check at the FIRST row proves all tasks are done.
            # "Go now" is merely an empty claim queue: never lock the event
            # for today, since a later Arena/Combat run can unlock rewards.
            # A requested claim may update the list before its popup appears;
            # wait for that popup to close before trusting the refreshed row.
            if page_ready and not scroll_to_top and not awaiting_reward_popup:
                if self.match_template_color(CHUN_ALL_TASK_DONE):
                    mark_activity_checked(self.config, self.activity_id)
                    logger.info("SpecialActivity: Koharu task rewards completed today")
                    return True
                if self.match_template_color(CHUN_TASK_REWARD_NONE):
                    logger.info("SpecialActivity: no Koharu task reward available, recheck after battle")
                    return True

            if timeout.reached():
                logger.warning("SpecialActivity: Koharu task reward timeout")
                return False

            if page_ready and scroll_to_top:
                # Re-entering an event can retain its previous scroll offset.
                # The five-row list fits after one downward swipe. Keep the
                # gesture inside task text, away from sidebar and claim buttons,
                # then recognize the first-row state on a fresh screenshot.
                self.device.swipe((520, 250), (520, 620), duration=(0.3, 0.4))
                scroll_to_top = False
                continue

            if page_ready and not awaiting_reward_popup:
                if self.match_template_color(CHUN_TASK_REWARD_PENDING, interval=2):
                    self.device.click(CHUN_TASK_REWARD_PENDING)
                    awaiting_reward_popup = True
                    continue

            # Network dialogs also use shared close controls. Handle them first
            # so their dismissal cannot stand in for a reward confirmation.
            if self.handle_network_error():
                continue
            if self.handle_ad_buff_x_close():
                awaiting_reward_popup = False
                continue

            # A dropped tap must remain retryable while the same unclaimed
            # row is positively visible. The overall timer is not reset by
            # clicks or popups, so a stuck claim cannot extend this flow forever.
            if page_ready and awaiting_reward_popup:
                if self.match_template_color(CHUN_TASK_REWARD_PENDING, interval=2):
                    self.device.click(CHUN_TASK_REWARD_PENDING)
                    continue

    def run(self) -> bool:
        """Run this overseas-only task claim and return to the main page.

        Pages:
            in: page_main, any
            out: page_main
        """
        if not server.is_oversea_server(self.config.Emulator_PackageName) or server.lang != "global_cn":
            logger.info("SpecialActivity: Koharu raffle unsupported on this server/language")
            self.config.task_delay(server_update=True)
            return True
        if not self.config.SpecialActivity_GetKoharuRaffleReward:
            logger.info("SpecialActivity: Koharu task reward disabled")
            self.config.task_delay(server_update=True)
            return True

        logger.hr("SpecialActivity: Koharu Raffle", level=1)
        if not self.device.app_is_running():
            from tasks.login.login import Login

            Login(self.config, device=self.device).app_start()

        success = self.run_claim()
        if success:
            self.ui_goto(page_main, skip_first_screenshot=True)
            self.config.task_delay(server_update=True)
        else:
            self.config.task_delay(success=False)
        return success
