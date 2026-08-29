import module.config.server as server_

from datetime import datetime, timedelta, timezone

from module.logger import logger


class SpecialActivityEntry:
    """
    Dispatch the special activity task by the active server and asset language.
    """

    EVENT_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")
    CN_EVENT_END_TIME = datetime(2026, 9, 17, 11, tzinfo=EVENT_TIMEZONE)
    OVERSEA_EVENT_END_TIME = datetime(2026, 10, 29, 2, tzinfo=EVENT_TIMEZONE)

    LEGACY_ACTIVITY = "legacy"
    FREE_GACHA_20_ACTIVITY = "free_gacha_20"

    def __init__(self, config, device=None, task=None):
        self.config = config
        self.device = device
        self.task = task

    def _event_mode(self) -> str | None:
        if (
            server_.is_cn_server(self.config.Emulator_PackageName)
            and server_.lang == "cn"
        ):
            return self.LEGACY_ACTIVITY
        if (
            server_.is_oversea_server(self.config.Emulator_PackageName)
            and server_.lang == "global_cn"
        ):
            return self.FREE_GACHA_20_ACTIVITY
        return None

    def _event_end_time(self, event_mode: str | None = None) -> datetime | None:
        event_mode = event_mode or self._event_mode()
        if event_mode == self.LEGACY_ACTIVITY:
            return self.CN_EVENT_END_TIME
        if event_mode == self.FREE_GACHA_20_ACTIVITY:
            return self.OVERSEA_EVENT_END_TIME
        return None

    def run(self) -> bool:
        event_mode = self._event_mode()
        event_end_time = self._event_end_time(event_mode)
        if event_mode is None or event_end_time is None:
            logger.info(
                "SpecialActivity: unavailable for current server or game language, skip task"
            )
            self.config.task_delay(server_update=True)
            return True

        if datetime.now(self.EVENT_TIMEZONE) >= event_end_time:
            logger.info(f"SpecialActivity: expired at {event_end_time}, skip task")
            self.config.task_delay(server_update=True)
            return True

        if event_mode == self.FREE_GACHA_20_ACTIVITY:
            from tasks.activity.free_gacha_20 import FreeGacha20

            return FreeGacha20(
                config=self.config,
                device=self.device,
                task=self.task,
            ).run()

        from tasks.activity.special_activity import SpecialActivity

        return SpecialActivity(
            config=self.config,
            device=self.device,
            task=self.task,
        ).run()

    def run_login_daily_reward(self) -> bool:
        """Claim the active server's reward before normal tasks begin.

        Pages:
            in: page_main
            out: page_main
        """
        if not self.config.is_task_enabled("SpecialActivity"):
            logger.info("SpecialActivity: disabled, skip post-login reward")
            return True

        event_mode = self._event_mode()
        event_end_time = self._event_end_time(event_mode)
        if (
            event_mode is None
            or event_end_time is None
            or datetime.now(self.EVENT_TIMEZONE) >= event_end_time
        ):
            logger.info("SpecialActivity: unavailable, skip post-login reward")
            return True

        from tasks.base.page import page_main

        if event_mode == self.FREE_GACHA_20_ACTIVITY:
            if not self.config.SpecialActivity_GetFreeGacha:
                logger.info(
                    "SpecialActivity: free-gacha reward disabled, skip post-login claim"
                )
                return True

            from tasks.activity.free_gacha_20 import FreeGacha20

            activity = FreeGacha20(
                config=self.config,
                device=self.device,
                task=self.task,
            )
            success = activity.run_claim(skip_first_screenshot=True)
        else:
            if not self.config.SpecialActivity_GetDailyReward:
                logger.info(
                    "SpecialActivity: daily reward disabled, skip post-login claim"
                )
                return True

            from tasks.activity.special_activity import SpecialActivity

            activity = SpecialActivity(
                config=self.config,
                device=self.device,
                task=self.task,
            )
            success = activity.run_get_daily_reward(skip_first_screenshot=True)

        activity.ui_goto(page_main, skip_first_screenshot=True)
        return success
