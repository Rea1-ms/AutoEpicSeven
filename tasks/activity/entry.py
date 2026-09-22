from module.logger import logger
from tasks.activity.calendar import active_activities
from tasks.activity.common_activity import CommonActivityBatch
from tasks.activity.scheduling import delay_next_activity_check, is_activity_checked_in_window
from tasks.base.page import page_main


class SpecialActivityEntry:
    """Dispatch the supported activities currently active on this server."""

    COMMON_ACTIVITY_OPTIONS = {
        "free_gacha_20": "SpecialActivity_GetFreeGacha",
        "e7wc_battle_gate": "SpecialActivity_GetE7wcBattleGateReward",
        "koharu_raffle": "SpecialActivity_GetKoharuRaffleReward",
        "huche_shop": "SpecialActivity_BuyHucheMysticMedals",
    }

    def __init__(self, config, device=None, task=None):
        self.config = config
        self.device = device
        self.task = task

    def run(self) -> bool:
        """Claim the active tabs together, then return to main once.

        Pages:
            in: page_main, any
            out: page_main after claims; current page when all events skip
        """
        activities = active_activities(self.config)
        if not activities:
            logger.info("SpecialActivity: no supported active event, skip task")
            delay_next_activity_check(self.config)
            return True

        runnable = []
        for event in activities:
            logger.info(f"SpecialActivity: {event.event_id}, ends at {event.end}")
            if event.mode != "legacy" and is_activity_checked_in_window(self.config, event):
                logger.info("SpecialActivity: reward already checked this period")
                continue
            option = self.COMMON_ACTIVITY_OPTIONS.get(event.mode)
            if option is not None and not getattr(self.config, option):
                logger.info(f"SpecialActivity: {event.event_id} reward disabled")
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
            logger.info("SpecialActivity: all activity rewards checked, return to main")
            last_activity.ui_goto(page_main, skip_first_screenshot=True)
        delay_next_activity_check(self.config)
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
