from module.base.timer import Timer
from module.config import server
from module.logger import logger
from tasks.activity.assets.assets_activity_special_26_10_15 import (
    MEIRIN_ALL_TASK_DONE,
    MEIRIN_GATE_CHECK,
    MEIRIN_GATE_SELECTED,
    MEIRIN_REWARD_POPUP_CHECK,
    MEIRIN_TASK_REWARD_NONE,
    MEIRIN_TASK_REWARD_PENDING,
)
from tasks.activity.navigation import ActivityNavigationMixin
from tasks.activity.scheduling import mark_activity_checked
from tasks.base.page import page_common_activity
from tasks.base.ui import UI


class MeirinReward(ActivityNavigationMixin, UI):
    """Claim completed Meirin tasks and leave unfinished tasks for later battles."""

    CLAIM_FLOW_TIMEOUT_SECONDS = 30

    def __init__(self, config, device=None, task=None, activity_id="meirin_reward_2026_10_08"):
        super().__init__(config=config, device=device, task=task)
        self.activity_id = activity_id

    def run_claim(self, skip_first_screenshot=True, *, navigate=True) -> bool:
        """Collect available rewards from the first task row.

        Pages:
            in: page_main, any; selected event when navigate=False
            out: page_common_activity, Meirin task list with no available reward
        """
        if navigate:
            self.ui_goto(page_common_activity, skip_first_screenshot=skip_first_screenshot)
            if not self.select_activity("与梅铃一起吧", MEIRIN_GATE_SELECTED):
                return False
        else:
            if not skip_first_screenshot:
                self.device.screenshot()
                skip_first_screenshot = True
            if not self._activity_selected(MEIRIN_GATE_SELECTED):
                return False

        timeout = Timer(self.CLAIM_FLOW_TIMEOUT_SECONDS, count=60).start()
        scroll_to_top = True
        awaiting_reward_popup = False

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            page_ready = self.match_template_color(MEIRIN_GATE_CHECK)
            # Claimed rows move below unfinished tasks. A lower checkmark is
            # not completion: only the first row, after restoring the list to
            # its top, can prove that today's tasks are all done. "Go now"
            # leaves the event eligible for another check after a battle.
            # The list may update before the reward popup is displayed. Keep
            # the pending claim until the popup closes, or the new first row
            # could cause an early return with the reward still on screen.
            if page_ready and not scroll_to_top and not awaiting_reward_popup:
                if self.match_template_color(MEIRIN_ALL_TASK_DONE):
                    mark_activity_checked(self.config, self.activity_id)
                    logger.info("LimitedActivity: Meirin task rewards completed today")
                    return True
                if self.match_template_color(MEIRIN_TASK_REWARD_NONE):
                    logger.info("LimitedActivity: no Meirin reward available, recheck after battle")
                    return True

            if timeout.reached():
                logger.warning("LimitedActivity: Meirin task reward timeout")
                return False

            if page_ready and scroll_to_top:
                # The event may retain an earlier scroll offset. Its five-row
                # list fits after one downward swipe inside the task text;
                # evaluate the first row only on the next fresh screenshot.
                self.device.swipe((520, 250), (520, 620), duration=(0.3, 0.4))
                scroll_to_top = False
                continue

            if page_ready and not awaiting_reward_popup and self.match_template_color(
                MEIRIN_TASK_REWARD_PENDING, interval=2
            ):
                self.device.click(MEIRIN_TASK_REWARD_PENDING)
                awaiting_reward_popup = True
                continue

            # Network dialogs share TOUCH_TO_CLOSE and may remain visible
            # during their handler's retry interval. Only the reward title
            # authorizes clearing the pending claim; dismissing another dialog
            # cannot substitute for observing the actual reward popup.
            if self.handle_network_error():
                continue
            if self.match_template_color(MEIRIN_REWARD_POPUP_CHECK) and self.handle_touch_to_close():
                awaiting_reward_popup = False
                continue

            # Retry a dropped tap only while the unclaimed first row remains
            # positively visible. Neither clicks nor popup handling reset the
            # flow deadline, so a stalled claim cannot extend it indefinitely.
            if page_ready and awaiting_reward_popup and self.match_template_color(
                MEIRIN_TASK_REWARD_PENDING, interval=2
            ):
                self.device.click(MEIRIN_TASK_REWARD_PENDING)
                continue

    def run(self) -> bool:
        """Run the supported event and leave final navigation to its owner.

        Pages:
            in: page_main, any
            out: page_common_activity after a claim; current page when skipped
        """
        if not server.is_oversea_server(self.config.Emulator_PackageName) or server.lang != "global_cn":
            logger.info("LimitedActivity: Meirin rewards unsupported on this server/language")
            self.config.task_delay(server_update=True)
            return True
        if not self.config.LimitedActivity_GetMeirinReward:
            logger.info("LimitedActivity: Meirin task reward disabled")
            self.config.task_delay(server_update=True)
            return True

        logger.hr("LimitedActivity: Meirin Rewards", level=1)
        if not self.device.app_is_running():
            from tasks.login.login import Login

            Login(self.config, device=self.device).app_start()

        success = self.run_claim()
        if success:
            self.config.task_delay(server_update=True)
        else:
            self.config.task_delay(success=False)
        return success
