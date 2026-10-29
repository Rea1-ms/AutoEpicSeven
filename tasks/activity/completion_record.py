from module.logger import logger
from tasks.activity.assets.assets_activity_completion_record import (
    COMPLETION_RECORD_CHECK,
)
from tasks.activity.navigation import ActivityNavigationMixin
from tasks.activity.scheduling import mark_activity_checked
from tasks.base.page import page_common_activity
from tasks.base.ui import UI


class CompletionRecord(ActivityNavigationMixin, UI):
    """Visit the attendance page without claiming or purchasing anything."""

    def __init__(self, config, device=None, task=None, activity_id="completion_record_2026_10_29"):
        super().__init__(config=config, device=device, task=task)
        self.activity_id = activity_id

    def run_claim(self, skip_first_screenshot=True, *, navigate=True) -> bool:
        """Confirm the attendance page before recording today's visit.

        Pages:
            in: any; completion record selected when navigate=False
            out: page_common_activity, completion record, rewards untouched
        """
        if navigate:
            self.ui_goto(page_common_activity, skip_first_screenshot=skip_first_screenshot)
            if not self.select_activity("完成记录", COMPLETION_RECORD_CHECK):
                return False
        elif not skip_first_screenshot:
            self.device.screenshot()
        if not self.match_template_color(COMPLETION_RECORD_CHECK):
            return False
        mark_activity_checked(self.config, self.activity_id)
        logger.info("LimitedActivity: attendance page visited, leave rewards unclaimed")
        return True
