import module.config.server as server_

from datetime import datetime, timedelta, timezone

from module.logger import logger


class SpecialActivityEntry:
    """
    Dispatch the special activity task by the active server and asset language.
    """

    EVENT_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")
    CN_EVENT_END_TIME = datetime(2026, 9, 17, 11, tzinfo=EVENT_TIMEZONE)
    OVERSEA_EVENT_END_TIME = datetime(2026, 8, 27, 11, tzinfo=EVENT_TIMEZONE)

    def __init__(self, config, device=None, task=None):
        self.config = config
        self.device = device
        self.task = task

    def _event_end_time(self) -> datetime | None:
        if (
                server_.is_cn_server(self.config.Emulator_PackageName)
                and server_.lang == 'cn'
        ):
            return self.CN_EVENT_END_TIME
        if (
                server_.is_oversea_server(self.config.Emulator_PackageName)
                and server_.lang == 'global_cn'
        ):
            return self.OVERSEA_EVENT_END_TIME
        return None

    def run(self) -> bool:
        event_end_time = self._event_end_time()
        if event_end_time is None:
            logger.info(
                'SpecialActivity: unavailable for current server or game language, skip task'
            )
            self.config.task_delay(server_update=True)
            return True

        if datetime.now(self.EVENT_TIMEZONE) >= event_end_time:
            logger.info(f'SpecialActivity: expired at {event_end_time}, skip task')
            self.config.task_delay(server_update=True)
            return True

        from tasks.activity.special_activity import SpecialActivity

        return SpecialActivity(
            config=self.config,
            device=self.device,
            task=self.task,
        ).run()
