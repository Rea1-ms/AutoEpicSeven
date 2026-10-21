import numpy as np

from module.base.timer import Timer
from module.base.utils import crop, rgb2luma
from module.logger import logger
from tasks.activity.assets.assets_activity_special_26_10_19 import (
    SUPPORT_DAILY_TASK,
    SUPPORT_GATE_CHECK,
    SUPPORT_REWARD_POPUP_CHECK,
    SUPPORT_TASK_GO,
    SUPPORT_TASK_RECEIVED,
    SUPPORT_TASK_REWARD_BUTTON_SEARCH,
    SUPPORT_TASK_REWARD_PENDING,
)
from tasks.activity.navigation import ActivityNavigationMixin
from tasks.activity.scheduling import mark_activity_checked
from tasks.base.page import page_common_activity
from tasks.base.ui import UI


class SupportReward(ActivityNavigationMixin, UI):
    """Claim arena support tickets and available weekly holding rewards."""

    CLAIM_FLOW_TIMEOUT_SECONDS = 45

    def __init__(
        self, config, device=None, task=None, activity_id="support_reward_2026_10_19"
    ):
        super().__init__(config=config, device=device, task=task)
        self.activity_id = activity_id

    def _daily_state(self):
        """Pair the daily label with its own checkmark, never a weekly row."""
        if not self.match_template_luma(SUPPORT_DAILY_TASK):
            return None
        _, top, _, bottom = SUPPORT_DAILY_TASK.button
        button = SUPPORT_TASK_RECEIVED
        searches = tuple(b.search for b in button.iter_buttons())
        try:
            button.load_search((1040, top - 20, 1230, bottom + 20))
            return self.match_template_color(button)
        finally:
            for frame, search in zip(button.iter_buttons(), searches):
                frame.load_search(search)
                frame.clear_offset()

    def run_claim(self, skip_first_screenshot=True, *, navigate=True) -> bool:
        """Claim rewards, checking both ends of the four-row task list.

        Pages:
            in: page_main, any; selected support tab when navigate=False
            out: page_common_activity, support tasks with reward popup closed
        """
        for button in (SUPPORT_TASK_GO, SUPPORT_TASK_RECEIVED, SUPPORT_TASK_REWARD_PENDING):
            button.load_search(SUPPORT_TASK_REWARD_BUTTON_SEARCH)

        if navigate:
            self.ui_goto(
                page_common_activity, skip_first_screenshot=skip_first_screenshot
            )
            if not self.select_activity("应援任务", SUPPORT_GATE_CHECK):
                return False
            skip_first_screenshot = True
        else:
            if not skip_first_screenshot:
                self.device.screenshot()
                skip_first_screenshot = True
            if not self.match_template_color(SUPPORT_GATE_CHECK):
                return False

        timeout = Timer(self.CLAIM_FLOW_TIMEOUT_SECONDS, count=30).start()
        restore_top = True
        scanned_bottom = False
        daily_state = None
        awaiting_popup = False
        pending_label = None

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            ready = self.match_template_color(SUPPORT_GATE_CHECK)
            # The daily row moves below locked weekly tasks after claiming.
            # Keep its identity across this bounded scan, not across runs.
            # A changed list cannot finish a pending claim before its actual
            # reward popup has been seen and dismissed; network dialogs do not
            # provide that evidence. Restart the scan only after that popup.
            if ready and not restore_top and not awaiting_popup:
                observed = self._daily_state()
                if observed is not None:
                    daily_state = observed
                pending = self.match_template_color(SUPPORT_TASK_REWARD_PENDING)
                if not pending and scanned_bottom and daily_state is not None:
                    if daily_state:
                        mark_activity_checked(self.config, self.activity_id)
                        logger.info(
                            "LimitedActivity: daily arena support reward received"
                        )
                    else:
                        logger.info(
                            "LimitedActivity: support tasks unfinished, recheck after arena"
                        )
                    return True

            if timeout.reached():
                logger.warning("LimitedActivity: support reward timeout")
                return False

            if ready and restore_top:
                self.device.swipe((520, 365), (520, 650), duration=(0.3, 0.4))
                restore_top = False
                continue

            if ready and self.match_template_color(SUPPORT_TASK_REWARD_PENDING):
                _, top, _, bottom = SUPPORT_TASK_REWARD_PENDING.button
                label = rgb2luma(crop(self.device.image, (334, top, 800, bottom))) < 128
                # A delayed popup can expose another claimable weekly row in
                # the same place. Retry only the original text mask; even one
                # changed day/counter must wait for the pending reward popup.
                if (
                    not awaiting_popup or np.array_equal(label, pending_label)
                ) and self.interval_is_reached(
                    SUPPORT_TASK_REWARD_PENDING,
                    interval=2,
                ):
                    self.device.click(SUPPORT_TASK_REWARD_PENDING)
                    self.interval_reset(SUPPORT_TASK_REWARD_PENDING, interval=2)
                    pending_label = label
                    awaiting_popup = True
                    continue

            if (
                ready
                and not awaiting_popup
                and not scanned_bottom
                and (
                    self.match_template_color(SUPPORT_TASK_GO)
                    or self.match_template_color(SUPPORT_TASK_RECEIVED)
                )
            ):
                self.device.swipe((520, 650), (520, 355), duration=(0.3, 0.4))
                scanned_bottom = True
                continue

            if self.handle_network_error():
                continue
            if (
                self.match_template_color(SUPPORT_REWARD_POPUP_CHECK)
                and self.handle_touch_to_close()
            ):
                awaiting_popup = False
                restore_top = True
                scanned_bottom = False
                daily_state = None
                pending_label = None
