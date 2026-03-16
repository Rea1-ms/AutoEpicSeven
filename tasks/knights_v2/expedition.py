from module.logger import logger
from tasks.knights_v2.team_battle import KnightsTeamBattleMixin


class KnightsExpeditionMixin(KnightsTeamBattleMixin):
    def _enter_expedition(self, skip_first_screenshot=True) -> bool:
        logger.info("Knights v2: expedition entry is now direct team battle")
        if not hasattr(self.device, "image") or self.device.image is None:
            self.device.screenshot()
        return True

    def run_expedition(self, skip_first_screenshot=True) -> bool:
        logger.hr("Knights Expedition", level=2)

        if not self._enter_expedition(skip_first_screenshot=skip_first_screenshot):
            return False

        if not self.config.KnightsExpedition_TeamBattle:
            logger.info("Knights team battle disabled by config")
            return True

        return self.run_team_battle(skip_first_screenshot=True)
