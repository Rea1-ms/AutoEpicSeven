import module.config.server as server_

from module.logger import logger


class SpecialActivityEntry:
    """
    Dispatch the special activity task by the active server and asset language.
    """

    def __init__(self, config, device=None, task=None):
        self.config = config
        self.device = device
        self.task = task

    def run(self) -> bool:
        if (
                server_.is_oversea_server(self.config.Emulator_PackageName)
                and server_.lang == 'global_cn'
        ):
            from tasks.activity.special_activity import SpecialActivity

            return SpecialActivity(
                config=self.config,
                device=self.device,
                task=self.task,
            ).run()

        logger.info(
            'SpecialActivity: unavailable for current server or game language, skip task'
        )
        self.config.task_delay(server_update=True)
        return True
