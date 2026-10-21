from module.logger import logger
from tasks.activity.calendar import ACTIVITY_TASK_MODES, active_activities
from tasks.activity.common_activity import CommonActivityBatch
from tasks.activity.scheduling import delay_next_activity_check, is_activity_checked_in_window
from tasks.base.page import page_main


class SpecialActivityEntry:
    """Dispatch special entrances; subclasses may own another scheduler task."""

    SCHEDULER_TASK = "SpecialActivity"
    ACTIVITY_MODES = ACTIVITY_TASK_MODES[SCHEDULER_TASK]

    COMMON_ACTIVITY_OPTIONS = {
        "huche_shop": "SpecialActivity_BuyHucheMysticMedals",
    }

    def __init__(self, config, device=None, task=None):
        self.config = config
        self.device = device
        self.task = task or self.SCHEDULER_TASK

    def run(self) -> bool:
        """Run this task's active entrances, then return to main once.

        Pages:
            in: page_main, any
            out: page_main after claims; current page when all events skip
        """
        # Only this task's entrances may participate. A common-sidebar failure
        # can then delay LimitedActivity without consuming SpecialActivity's
        # pending run, and shop failures cannot suppress the daily claims.
        activities = [event for event in active_activities(self.config) if event.mode in self.ACTIVITY_MODES]
        if not activities:
            logger.info(f"{self.SCHEDULER_TASK}: no supported active event, skip task")
            delay_next_activity_check(self.config, task=self.SCHEDULER_TASK)
            return True

        runnable = []
        for event in activities:
            logger.info(f"{self.SCHEDULER_TASK}: {event.event_id}, ends at {event.end}")
            if event.mode != "legacy" and is_activity_checked_in_window(self.config, event):
                logger.info(f"{self.SCHEDULER_TASK}: reward already checked this period")
                continue
            option = self.COMMON_ACTIVITY_OPTIONS.get(event.mode)
            if option is not None and not getattr(self.config, option):
                logger.info(f"{self.SCHEDULER_TASK}: {event.event_id} reward disabled")
                continue
            runnable.append(event)

        common_activities = [event for event in runnable if event.mode in CommonActivityBatch.ACTIVITIES]
        last_activity = None
        common_done = False
        for event in runnable:
            option = self.COMMON_ACTIVITY_OPTIONS.get(event.mode)
            if event.mode in CommonActivityBatch.ACTIVITIES:
                if common_done:
                    continue
                activity = CommonActivityBatch(
                    config=self.config,
                    device=self.device,
                    task=self.task,
                )
                self.device = activity.device
                if not activity.run(common_activities):
                    return False
                common_done = True
                last_activity = activity
                continue
            elif event.mode == "huche_shop":
                from tasks.activity.huche_shop import HucheShop

                activity = HucheShop(
                    config=self.config,
                    device=self.device,
                    task=self.task,
                    activity_id=event.event_id,
                )
            else:
                from tasks.activity.legacy.summer_2026_06_25.special_activity import SpecialActivity

                activity = SpecialActivity(
                    config=self.config,
                    device=self.device,
                    task=self.task,
                )
            # Every tab uses the same device and its latest screenshot, even
            # when the entry was constructed without a device. A skipped tab
            # must not create a client or take ownership of final navigation.
            self.device = activity.device
            if not activity.run():
                return False
            # Modern flows leave a verified page for the next activity. The
            # legacy flow owns its separate page and already returns to main.
            # Keep failure handling in the failing flow; never overwrite its
            # retry schedule or hide its unresolved popup by continuing.
            last_activity = activity if option is not None else None

        if last_activity is not None:
            logger.info(f"{self.SCHEDULER_TASK}: all activity rewards checked, return to main")
            last_activity.ui_goto(page_main, skip_first_screenshot=True)
        delay_next_activity_check(self.config, task=self.SCHEDULER_TASK)
        return True

    def run_login_daily_reward(self) -> bool:
        """Claim a legacy login reward only while its calendar entry is active.

        Pages:
            in: page_main
            out: page_main
        """
        if not self.config.is_task_enabled("SpecialActivity"):
            return True
        if not any(event.mode == "legacy" for event in active_activities(self.config)):
            logger.info("SpecialActivity: active rewards use normal scheduling")
            return True
        if not self.config.SpecialActivity_GetDailyReward:
            return True

        from tasks.activity.legacy.summer_2026_06_25.special_activity import SpecialActivity

        activity = SpecialActivity(
            config=self.config,
            device=self.device,
            task=self.task,
        )
        success = activity.run_get_daily_reward(skip_first_screenshot=True)
        activity.ui_goto(page_main, skip_first_screenshot=True)
        return success
