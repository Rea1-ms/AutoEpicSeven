from module.logger import logger
from tasks.activity.calendar import active_activities
from tasks.activity.scheduling import delay_next_activity_check, is_activity_checked_today


class SpecialActivityEntry:
    """Dispatch the supported activities currently active on this server."""

    def __init__(self, config, device=None, task=None):
        self.config = config
        self.device = device
        self.task = task

    def run(self) -> bool:
        activities = active_activities(self.config)
        if not activities:
            logger.info("SpecialActivity: no supported active event, skip task")
            delay_next_activity_check(self.config)
            return True

        for event in activities:
            logger.info(f"SpecialActivity: {event.event_id}, ends at {event.end}")
            if event.mode != "legacy" and is_activity_checked_today(self.config, event.event_id):
                logger.info("SpecialActivity: reward already checked today")
                continue
            if event.mode == "free_gacha_20":
                from tasks.activity.free_gacha_20 import FreeGacha20

                success = FreeGacha20(
                    config=self.config,
                    device=self.device,
                    task=self.task,
                    activity_id=event.event_id,
                ).run()
            elif event.mode == "e7wc_battle_gate":
                from tasks.activity.e7wc_battle_gate import E7wcBattleGate

                success = E7wcBattleGate(
                    config=self.config,
                    device=self.device,
                    task=self.task,
                    activity_id=event.event_id,
                ).run()
            else:
                from tasks.activity.legacy.summer_2026_06_25.special_activity import SpecialActivity

                success = SpecialActivity(
                    config=self.config,
                    device=self.device,
                    task=self.task,
                ).run()
            if not success:
                return False

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
        from tasks.base.page import page_main

        activity = SpecialActivity(
            config=self.config,
            device=self.device,
            task=self.task,
        )
        success = activity.run_get_daily_reward(skip_first_screenshot=True)
        activity.ui_goto(page_main, skip_first_screenshot=True)
        return success
