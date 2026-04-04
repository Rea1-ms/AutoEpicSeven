from module.base.timer import Timer
from module.exception import RequestHumanTakeover
from module.logger import logger
from module.ocr.ocr import DigitCounter
from tasks.base.assets.assets_base_page import BACK
from tasks.base.assets.assets_base_popup import POPUP_CONFIRM
from tasks.knights.assets.assets_knights_gvg import (
    KNIGHTS_CREST,
    OCR_KNIGHTS_CREST,
    TEAM_BATTLE_RESULT_CONFIRM,
)
from tasks.knights.assets.assets_knights_main_page import (
    KNIGHTS_CHECK,
    TEAM_BATTLE_LOCKED,
    TEAM_BATTLE_OPENING,
)
from tasks.knights.team_battle_status import (
    KnightsTeamBattleStatusMixin,
    TeamBattleCrestStatus,
)


class OcrKnightsCrest(DigitCounter):
    def after_process(self, result):
        result = super().after_process(result)
        result = result.replace("O", "0").replace("o", "0")
        result = result.replace("I", "1").replace("l", "1")
        result = result.replace("／", "/")
        return result


class KnightsTeamBattleMixin(KnightsTeamBattleStatusMixin):
    TEAM_BATTLE_HOME_SIMILARITY = 0.7
    TEAM_BATTLE_LOCKED_SIMILARITY = 0.8
    TEAM_BATTLE_FLOW_TIMEOUT_SECONDS = 120
    TEAM_BATTLE_ENTRY_PENDING_SECONDS = 8
    TEAM_BATTLE_BACK_TIMEOUT_SECONDS = 12

    def _is_knights_home(self, interval=0) -> bool:
        return self.appear(KNIGHTS_CHECK, interval=interval)

    def _is_team_battle_home(self, interval=0) -> bool:
        return self.appear(KNIGHTS_CREST, interval=interval, similarity=self.TEAM_BATTLE_HOME_SIMILARITY)

    def _is_team_battle_locked(self, interval=0) -> bool:
        self.device.stuck_record_add(TEAM_BATTLE_LOCKED)

        if interval and not self.interval_is_reached(TEAM_BATTLE_LOCKED, interval=interval):
            return False

        appear = TEAM_BATTLE_LOCKED.match_template_luma(
            self.device.image, similarity=self.TEAM_BATTLE_LOCKED_SIMILARITY
        )

        if appear and interval:
            self.interval_reset(TEAM_BATTLE_LOCKED, interval=interval)

        return appear

    def _team_battle_time_ocr_todo(self):
        """
        TODO:
            OCR guild-war start/end time after assets and format are ready.
        """
        pass

    def _ocr_knights_crest(self) -> TeamBattleCrestStatus | None:
        lang = self.config.Emulator_GameLanguage
        if lang == "auto" or not lang:
            lang = "cn"
        ocr = OcrKnightsCrest(OCR_KNIGHTS_CREST, lang=lang, name="KnightsCrest")
        current, remain, total = ocr.ocr_single_line(self.device.image)
        if total and current <= total:
            logger.attr("KnightsCrest", f"{current}/{total}")
            # Guild war counter is remaining_attacks / total_attacks.
            return TeamBattleCrestStatus(current=current, remain=current, total=total)

        logger.warning(f"Knights crest OCR invalid: {current}/{total}")
        return None

    def _back_to_knights(self, skip_first_screenshot=True) -> bool:
        timeout = Timer(self.TEAM_BATTLE_BACK_TIMEOUT_SECONDS, count=36).start()

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning("Team battle back-to-knights timeout")
                return False

            if self._is_knights_home(interval=1):
                return True

            if self.appear_then_click(TEAM_BATTLE_RESULT_CONFIRM, interval=1):
                timeout.reset()
                continue

            if self._is_team_battle_home(interval=1):
                self.device.click(BACK)
                timeout.reset()
                continue

            if self.handle_network_error():
                timeout.reset()
                continue

    def run_team_battle(self, skip_first_screenshot=True) -> bool:
        logger.info("Knights: team battle")
        timeout = Timer(self.TEAM_BATTLE_FLOW_TIMEOUT_SECONDS, count=360).start()
        entry_pending = Timer(self.TEAM_BATTLE_ENTRY_PENDING_SECONDS, count=0)
        entry_clicked = False
        self._reset_team_battle_status_runtime()

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.appear(POPUP_CONFIRM):
                raise RequestHumanTakeover(
                    "Detected an unfinished Guild War battle. "
                    "Please complete it manually, restart AES, "
                    "or disable GVG-related tasks."
                )

            if timeout.reached():
                logger.warning("Team battle flow timeout")
                return False

            if self._is_team_battle_home(interval=1):
                status = self._ocr_knights_crest()
                if status is None:
                    self._update_team_battle_dashboard_invalid()
                else:
                    self._update_team_battle_dashboard_counter(status)
                    self._send_or_schedule_team_battle_reminder(status)
                self._team_battle_time_ocr_todo()
                logger.info("Team battle home reached")
                return self._back_to_knights(skip_first_screenshot=True)

            if self.appear_then_click(TEAM_BATTLE_RESULT_CONFIRM, interval=1):
                timeout.reset()
                continue

            if self.handle_network_error():
                timeout.reset()
                continue

            if self._is_knights_home(interval=1):
                if self._is_team_battle_locked(interval=1):
                    logger.info("Team battle is locked, skip for now")
                    self._update_team_battle_dashboard_locked()
                    return True

                if self.appear_then_click(TEAM_BATTLE_OPENING, interval=1):
                    logger.info("Team battle: knights home -> team battle")
                    entry_clicked = True
                    entry_pending.reset()
                    timeout.reset()
                    continue

                if entry_clicked:
                    if entry_pending.reached():
                        logger.warning("Team battle entry did not leave knights page in time")
                        return False
                    continue
