from module.base.timer import Timer
from module.exception import RequestHumanTakeover
from module.logger import logger
from module.ocr.ocr import DigitCounter
from tasks.base.assets.assets_base_popup import POPUP_CONFIRM
from tasks.base.page import page_knights
from tasks.knights.assets.assets_knights_expedition import (
    KNIGHTS_CREST,
    OCR_KNIGHTS_CREST,
    TEAM_BATTLE,
    TEAM_BATTLE_RESULT_CONFIRM,
    WAITING_FOR_WAR,
)


class OcrKnightsCrest(DigitCounter):
    def after_process(self, result):
        result = super().after_process(result)
        # Normalize common OCR confusions on x/y counters.
        result = result.replace("O", "0").replace("o", "0")
        result = result.replace("I", "1").replace("l", "1")
        result = result.replace("／", "/")
        return result


class KnightsTeamBattleMixin:
    TEAM_BATTLE_LUMA_SIMILARITY = 0.8
    TEAM_BATTLE_COLOR_THRESHOLD = 30
    WAITING_FOR_WAR_LUMA_SIMILARITY = 0.8
    WAITING_FOR_WAR_COLOR_THRESHOLD = 30

    TEAM_BATTLE_STATE_ENTER = "enter_expedition"
    TEAM_BATTLE_STATE_SETTLEMENT = "clear_settlement"

    def _is_team_battle_home(self, interval=0) -> bool:
        # I've tried to extend the search area, but similarity is still stuck at 0.75, and I don't know why
        return self.appear(KNIGHTS_CREST, interval=interval, similarity=0.7)

    def _is_team_battle_ready(self, interval=0) -> bool:
        """
        TEAM_BATTLE uses luma + color double check to avoid animation false positives.
        """
        self.device.stuck_record_add(TEAM_BATTLE)

        if interval and not self.interval_is_reached(TEAM_BATTLE, interval=interval):
            return False

        appear = False
        if TEAM_BATTLE.match_template_luma(self.device.image, similarity=self.TEAM_BATTLE_LUMA_SIMILARITY):
            if TEAM_BATTLE.match_color(self.device.image, threshold=self.TEAM_BATTLE_COLOR_THRESHOLD):
                appear = True

        if appear and interval:
            self.interval_reset(TEAM_BATTLE, interval=interval)

        return appear

    def _is_team_battle_waiting_for_war(self, interval=0) -> bool:
        """
        Detect guild war truce period page.
        In truce, crest marker/count is unavailable and team battle should be skipped.
        """
        self.device.stuck_record_add(WAITING_FOR_WAR)

        if interval and not self.interval_is_reached(WAITING_FOR_WAR, interval=interval):
            return False

        appear = False
        if WAITING_FOR_WAR.match_template_luma(self.device.image, similarity=self.WAITING_FOR_WAR_LUMA_SIMILARITY):
            appear = True

        if appear and interval:
            self.interval_reset(WAITING_FOR_WAR, interval=interval)

        return appear

    def _team_battle_time_ocr_todo(self):
        """
        TODO:
            OCR 团战剩余开始/结束时间，后续补充。
        """
        pass

    def _ocr_knights_crest(self) -> tuple[int, int, int]:
        lang = self.config.Emulator_GameLanguage
        if lang == "auto" or not lang:
            lang = "cn"
        ocr = OcrKnightsCrest(OCR_KNIGHTS_CREST, lang=lang, name="KnightsCrest")
        current, remain, total = ocr.ocr_single_line(self.device.image)
        if total and current <= total:
            logger.attr("KnightsCrest", f"{current}/{total}")
        else:
            logger.warning(f"Knights crest OCR invalid: {current}/{total}")
        return current, remain, total

    def run_team_battle(self, skip_first_screenshot=True) -> bool:
        logger.info("Knights expedition: team battle")
        timeout = Timer(120, count=360).start()
        state = self.TEAM_BATTLE_STATE_ENTER

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

            if self._is_team_battle_waiting_for_war(interval=1):
                logger.info("Team battle is in truce period, skip for now")
                return True

            if self._is_team_battle_home(interval=1):
                self._ocr_knights_crest()
                self._team_battle_time_ocr_todo()
                logger.info("Team battle home reached")
                self._back_to_knights(skip_first_screenshot=True)
                return True

            if state == self.TEAM_BATTLE_STATE_ENTER and self._is_team_battle_ready(interval=1):
                logger.info("Team battle: expedition -> team battle")
                self.device.click(TEAM_BATTLE)
                state = self.TEAM_BATTLE_STATE_SETTLEMENT
                timeout.reset()
                continue

            if self.appear(TEAM_BATTLE_RESULT_CONFIRM):
                state = self.TEAM_BATTLE_STATE_SETTLEMENT

            # Settlement can contain multiple confirms in a row.
            if self.appear_then_click(TEAM_BATTLE_RESULT_CONFIRM, interval=1):
                timeout.reset()
                continue

            # If settlement sends us back to expedition page, re-enter team battle.
            if state == self.TEAM_BATTLE_STATE_SETTLEMENT and self._is_team_battle_ready(interval=1):
                self.device.click(TEAM_BATTLE)
                timeout.reset()
                continue

            if self.handle_network_error():
                timeout.reset()
                continue

    def _back_to_knights(self, skip_first_screenshot=True) -> bool:
        self.ui_goto(page_knights, skip_first_screenshot=skip_first_screenshot)
        return True
