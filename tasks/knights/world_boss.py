import re

from module.base.button import ClickButton
from module.base.timer import Timer
from module.logger import logger
from module.ocr.ocr import Ocr
from tasks.base.assets.assets_base_page import BACK
from tasks.base.page import page_knights
from tasks.knights.assets.assets_knights_main_page import (
    KNIGHTS_CHECK,
    WORLD_BOSS_CHECK,
    WORLD_BOSS_LOCKED,
    WORLD_BOSS_OPENING,
)
from tasks.knights.assets.assets_knights_world_boss_flow import (
    AUTO_CONFIG,
    BATTLE_RESULT_CONFIRM,
    BATTLE_START,
    CHOOSE_TEAM,
    EMPTY_TEAM,
    FORM_A_TEAM,
    OPEN_ALL_BOX,
    OPEN_ALL_BOX_CONFIRM,
    RANK,
    SKIP,
    WORLD_BOSS_TOUCH_TO_CLOSE,
)
from tasks.knights.assets.assets_knights_world_boss_weekly_rewards import (
    OCR_WEEKLY_CONTRIBUTION,
    WEEKLY_CONTRIBUTION_TIER_1_LOCKED,
    WEEKLY_CONTRIBUTION_TIER_1_RECEIVED,
    WEEKLY_CONTRIBUTION_TIER_2_LOCKED,
    WEEKLY_CONTRIBUTION_TIER_2_RECEIVED,
)


class OcrWeeklyContribution(Ocr):
    def after_process(self, result):
        result = super().after_process(result)
        result = result.replace("，", ",").replace(" ", "")
        result = result.replace("O", "0").replace("o", "0")
        return result


class KnightsWorldBossMixin:
    CHOOSE_TEAM_LUMA_SIMILARITY = 0.8
    CHOOSE_TEAM_COLOR_THRESHOLD = 30
    WORLD_BOSS_HOME_LUMA_SIMILARITY = 0.8
    WORLD_BOSS_LOCKED_SIMILARITY = 0.9
    WORLD_BOSS_FORM_RETRY_SECONDS = 4
    WORLD_BOSS_AUTO_CONFIG_RETRY_SECONDS = 1.2
    WORLD_BOSS_ENTRY_PENDING_SECONDS = 5
    WORLD_BOSS_ENTRY_TIMEOUT_SECONDS = 20
    WORLD_BOSS_CHOOSE_TEAM_RETRY_SECONDS = 2
    WORLD_BOSS_CHOOSE_TEAM_PENDING_SECONDS = 2.5
    WORLD_BOSS_WEEKLY_CONTRIBUTION_POST_CLICK_SETTLE_SECONDS = 0.35
    WORLD_BOSS_WEEKLY_CONTRIBUTION_TIER_LUMA_SIMILARITY = 0.8
    WORLD_BOSS_WEEKLY_CONTRIBUTION_TIER_COLOR_THRESHOLD = 30

    WORLD_BOSS_STAGE_ENTRY = "entry"
    WORLD_BOSS_STAGE_SELECT = "select_team"
    WORLD_BOSS_STAGE_SETUP = "setup_team"

    def _get_world_boss_ocr_lang(self) -> str:
        lang = getattr(self.config, "Emulator_GameLanguage", None)
        if lang in ("auto", "", None, "cn", "global_cn", "zh", "zh_cn"):
            return "cn"
        if lang in ("en", "global_en", "en_us"):
            return "en"
        if lang in ("jp", "ja", "ja_jp"):
            return "jp"
        if lang in ("tw", "zht", "zh_tw"):
            return "tw"
        return "cn"

    def _is_world_boss_home(self, interval=0) -> bool:
        self.device.stuck_record_add(WORLD_BOSS_CHECK)

        if interval and not self.interval_is_reached(WORLD_BOSS_CHECK, interval=interval):
            return False

        appear = WORLD_BOSS_CHECK.match_template_luma(
            self.device.image, similarity=self.WORLD_BOSS_HOME_LUMA_SIMILARITY
        )

        if appear and interval:
            self.interval_reset(WORLD_BOSS_CHECK, interval=interval)

        return appear

    def _is_knights_home(self, interval=0) -> bool:
        return self.appear(KNIGHTS_CHECK, interval=interval)

    def _is_world_boss_locked(self, interval=0) -> bool:
        self.device.stuck_record_add(WORLD_BOSS_LOCKED)

        if interval and not self.interval_is_reached(WORLD_BOSS_LOCKED, interval=interval):
            return False

        appear = WORLD_BOSS_LOCKED.match_template_luma(
            self.device.image, similarity=self.WORLD_BOSS_LOCKED_SIMILARITY
        )

        if appear and interval:
            self.interval_reset(WORLD_BOSS_LOCKED, interval=interval)

        return appear

    def _is_choose_team_ready(self, interval=0) -> bool:
        """
        CHOOSE_TEAM uses luma + color double check:
            luma match + color match => clickable.
        """
        self.device.stuck_record_add(CHOOSE_TEAM)

        if interval and not self.interval_is_reached(CHOOSE_TEAM, interval=interval):
            return False

        appear = False
        if CHOOSE_TEAM.match_template_luma(self.device.image, similarity=self.CHOOSE_TEAM_LUMA_SIMILARITY):
            if CHOOSE_TEAM.match_color(self.device.image, threshold=self.CHOOSE_TEAM_COLOR_THRESHOLD):
                appear = True

        if appear and interval:
            self.interval_reset(CHOOSE_TEAM, interval=interval)

        return appear

    def _is_choose_team_exhausted(self) -> bool:
        """
        CHOOSE_TEAM template appears but color mismatch:
            means this account has no attempts left today.
        """
        if CHOOSE_TEAM.match_template_luma(self.device.image, similarity=self.CHOOSE_TEAM_LUMA_SIMILARITY):
            return not CHOOSE_TEAM.match_color(self.device.image, threshold=self.CHOOSE_TEAM_COLOR_THRESHOLD)
        return False

    def _handle_world_boss_touch_to_close(self, interval=0) -> bool:
        """
        Handle world-boss-only colored "touch to close" overlay.
        Keep it local to world boss flow to avoid global popup side effects.
        """
        if self.appear_then_click(WORLD_BOSS_TOUCH_TO_CLOSE, interval=interval):
            logger.info("World boss: closed entry touch-to-close overlay")
            return True
        return False

    def _world_boss_no_stamina_todo(self) -> bool:
        """
        Handle no-stamina popup during world boss battle start.

        Flow:
            1) close popup via POPUP_CANCEL
            2) click BACK once to leave team panel
            3) return no_stamina status and let caller navigate out
        """
        if self.handle_popup_cancel(interval=1):
            logger.info("World boss no stamina popup closed")
            self._world_boss_no_stamina_pending_back = True
            return False

        if not getattr(self, "_world_boss_no_stamina_pending_back", False):
            return False

        if self._is_world_boss_home(interval=0):
            logger.info("World boss no stamina handled at home page")
            self._world_boss_no_stamina_pending_back = False
            return True

        if any(
            [
                self.appear(RANK),
                self.appear(FORM_A_TEAM),
                self.appear(AUTO_CONFIG),
                self.appear(BATTLE_START),
                self.appear(EMPTY_TEAM),
            ]
        ):
            logger.info("World boss no stamina: leave team panel")
            self.device.click(BACK)
            self._world_boss_no_stamina_pending_back = False
            return True

        return False

    def _enter_world_boss(self, skip_first_screenshot=True) -> str:
        logger.info("Knights: enter world boss")
        timeout = Timer(self.WORLD_BOSS_ENTRY_TIMEOUT_SECONDS, count=60).start()
        entry_pending = Timer(self.WORLD_BOSS_ENTRY_PENDING_SECONDS, count=0)
        entry_clicked = False

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning("World boss entry timeout")
                return "failed"

            if self._is_world_boss_home(interval=1) or self.appear(RANK, interval=1) or self.appear(FORM_A_TEAM, interval=1):
                return "entered"

            if self._handle_world_boss_touch_to_close(interval=0.6):
                timeout.reset()
                continue

            if self.handle_network_error():
                timeout.reset()
                continue

            if self._is_knights_home(interval=1):
                if self._is_world_boss_locked(interval=1):
                    logger.info("World boss is locked, skip for now")
                    return "locked"

                if self.appear_then_click(WORLD_BOSS_OPENING, interval=1):
                    entry_clicked = True
                    entry_pending.start()
                    timeout.reset()
                    continue

                if entry_clicked:
                    if entry_pending.reached():
                        logger.warning("World boss entry did not leave knights page in time")
                        return "failed"
                    continue

    def _world_boss_once(self, skip_first_screenshot=True) -> str:
        """
        Returns:
            str:
                completed / exhausted / no_stamina / failed
        """
        timeout = Timer(120, count=300).start()
        exhausted_confirm = Timer(1, count=2).start()
        choose_team_exhausted_confirm = Timer(1, count=2).start()
        settlement_progress = False
        stage = self.WORLD_BOSS_STAGE_ENTRY
        choose_team_retry = Timer(self.WORLD_BOSS_CHOOSE_TEAM_RETRY_SECONDS, count=0).start()
        choose_team_pending_timer = Timer(self.WORLD_BOSS_CHOOSE_TEAM_PENDING_SECONDS, count=0).start()
        choose_team_pending = False
        form_retry = Timer(self.WORLD_BOSS_FORM_RETRY_SECONDS, count=0).start()
        auto_config_retry = Timer(self.WORLD_BOSS_AUTO_CONFIG_RETRY_SECONDS, count=0).start()
        auto_config_clicked = False
        rank_selected = False
        self._world_boss_no_stamina_pending_back = False

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning("World boss round timeout")
                return "failed"

            if stage == self.WORLD_BOSS_STAGE_ENTRY and not choose_team_pending:
                if self._is_choose_team_exhausted():
                    if exhausted_confirm.reached():
                        logger.info("World boss chances exhausted")
                        return "exhausted"
                else:
                    exhausted_confirm.reset()
            else:
                exhausted_confirm.reset()

            if self._world_boss_no_stamina_todo():
                return "no_stamina"

            if self.handle_network_error():
                timeout.reset()
                continue

            if self.appear(RANK, interval=1) or self.appear(FORM_A_TEAM, interval=1):
                stage = self.WORLD_BOSS_STAGE_SELECT
                choose_team_pending = False

            if stage == self.WORLD_BOSS_STAGE_ENTRY:
                if self._handle_world_boss_touch_to_close(interval=0.6):
                    timeout.reset()
                    continue

                if self._is_choose_team_exhausted():
                    if choose_team_exhausted_confirm.reached():
                        logger.info("World boss chances exhausted (CHOOSE_TEAM gray)")
                        return "exhausted"
                else:
                    choose_team_exhausted_confirm.reset()

                if choose_team_pending:
                    if choose_team_pending_timer.reached():
                        choose_team_pending = False
                    else:
                        continue

                if self._is_choose_team_ready(interval=1):
                    if choose_team_retry.reached():
                        logger.info("World boss: CHOOSE_TEAM -> rank panel")
                        self.device.click(CHOOSE_TEAM)
                        choose_team_retry.reset()
                        choose_team_pending = True
                        rank_selected = False
                        choose_team_pending_timer.reset()
                        timeout.reset()
                    continue

                if settlement_progress and (self._is_world_boss_home(interval=0) or self.appear(KNIGHTS_CHECK)):
                    return "completed"

            if stage == self.WORLD_BOSS_STAGE_SELECT:
                if self._is_choose_team_exhausted():
                    if choose_team_exhausted_confirm.reached():
                        logger.info("World boss chances exhausted (CHOOSE_TEAM gray)")
                        return "exhausted"
                else:
                    choose_team_exhausted_confirm.reset()

                if (not rank_selected) and self.appear_then_click(RANK, interval=1):
                    rank_selected = True
                    timeout.reset()
                    continue

                if self.appear(FORM_A_TEAM):
                    logger.info("World boss: FORM_A_TEAM -> team setup")
                    self.device.click(FORM_A_TEAM)
                    stage = self.WORLD_BOSS_STAGE_SETUP
                    choose_team_pending = False
                    form_retry.reset()
                    auto_config_retry.clear()
                    auto_config_clicked = False
                    timeout.reset()
                    continue

                if self._is_choose_team_ready(interval=2):
                    self.device.click(CHOOSE_TEAM)
                    rank_selected = False
                    timeout.reset()
                    continue

            if stage == self.WORLD_BOSS_STAGE_SETUP:
                if self._is_choose_team_ready(interval=0) and not any(
                    [self.appear(FORM_A_TEAM), self.appear(AUTO_CONFIG), self.appear(BATTLE_START), self.appear(EMPTY_TEAM)]
                ):
                    stage = self.WORLD_BOSS_STAGE_ENTRY
                    choose_team_pending = False
                    rank_selected = False
                    continue

                if self.appear(EMPTY_TEAM) or self.appear(AUTO_CONFIG) or not auto_config_clicked:
                    if auto_config_retry.reached():
                        if self.appear_then_click(AUTO_CONFIG, interval=0):
                            auto_config_clicked = True
                            timeout.reset()
                        auto_config_retry.reset()
                    if self.appear(EMPTY_TEAM):
                        continue

                if self.appear_then_click(BATTLE_START, interval=1):
                    stage = self.WORLD_BOSS_STAGE_ENTRY
                    choose_team_pending = False
                    rank_selected = False
                    auto_config_clicked = False
                    settlement_progress = False
                    timeout.reset()
                    continue

                if self.appear(FORM_A_TEAM) and form_retry.reached():
                    logger.info("World boss: FORM_A_TEAM still visible, retry")
                    self.device.click(FORM_A_TEAM)
                    form_retry.reset()
                    auto_config_retry.clear()
                    auto_config_clicked = False
                    choose_team_pending = False
                    timeout.reset()
                    continue

            if self.appear_then_click(SKIP, interval=1):
                settlement_progress = True
                timeout.reset()
                continue

            if self.appear_then_click(OPEN_ALL_BOX, interval=1):
                settlement_progress = True
                timeout.reset()
                continue

            if self.appear_then_click(OPEN_ALL_BOX_CONFIRM, interval=1):
                settlement_progress = True
                timeout.reset()
                continue

            if self.appear_then_click(BATTLE_RESULT_CONFIRM, interval=1):
                settlement_progress = True
                timeout.reset()
                continue

            if settlement_progress and (self._is_world_boss_home(interval=0) or self.appear(KNIGHTS_CHECK)):
                return "completed"

    def _close_world_boss_exhausted_popup(self, skip_first_screenshot=True) -> bool:
        timeout = Timer(8, count=24).start()
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning("Close world boss exhausted popup timeout")
                return False

            if self._is_world_boss_home(interval=0):
                return True
            if self.appear(KNIGHTS_CHECK):
                return True

            if self.handle_ad_buff_x_close(interval=1):
                timeout.reset()
                continue
            if self._handle_world_boss_touch_to_close(interval=0.6):
                timeout.reset()
                continue
            if self.handle_network_error():
                timeout.reset()
                continue

    def _back_to_knights_from_world_boss(self, skip_first_screenshot=True) -> bool:
        self.ui_goto(page_knights, skip_first_screenshot=skip_first_screenshot)
        return True

    @staticmethod
    def _extract_weekly_contribution_points(texts: list[str]) -> int | None:
        comma_candidates: list[int] = []
        plain_candidates: list[int] = []

        for text in texts:
            for matched in re.findall(r"\d{1,3}(?:,\d{3})+", text):
                try:
                    comma_candidates.append(int(matched.replace(",", "")))
                except ValueError:
                    continue

            for matched in re.findall(r"\d+", text):
                if len(matched) < 6:
                    continue
                try:
                    plain_candidates.append(int(matched))
                except ValueError:
                    continue

        if comma_candidates:
            return max(comma_candidates)
        if plain_candidates:
            return max(plain_candidates)
        return None

    def _ocr_weekly_contribution_points(self, ocr: OcrWeeklyContribution) -> int | None:
        results = ocr.detect_and_ocr(self.device.image)
        points = self._extract_weekly_contribution_points([result.ocr_text for result in results])
        if points is not None:
            return points

        results = ocr.detect_and_ocr(self.device.image, direct_ocr=True)
        return self._extract_weekly_contribution_points([result.ocr_text for result in results])

    def _weekly_contribution_tier_state(self, received_button, locked_button) -> str:
        """
        Returns:
            str: claimable / received / locked / not_found
        """
        if received_button.match_template_luma(
            self.device.image, similarity=self.WORLD_BOSS_WEEKLY_CONTRIBUTION_TIER_LUMA_SIMILARITY
        ):
            if received_button.match_color(
                self.device.image, threshold=self.WORLD_BOSS_WEEKLY_CONTRIBUTION_TIER_COLOR_THRESHOLD
            ):
                return "received"
            return "claimable"

        if received_button.match_template(
            self.device.image, similarity=self.WORLD_BOSS_WEEKLY_CONTRIBUTION_TIER_LUMA_SIMILARITY
        ):
            return "claimable"

        if locked_button.match_template_luma(
            self.device.image, similarity=self.WORLD_BOSS_WEEKLY_CONTRIBUTION_TIER_LUMA_SIMILARITY
        ) and locked_button.match_color(
            self.device.image, threshold=self.WORLD_BOSS_WEEKLY_CONTRIBUTION_TIER_COLOR_THRESHOLD
        ):
            return "locked"

        return "not_found"

    def _claim_weekly_contribution_tier(self, ocr, name: str, received_button, locked_button) -> bool:
        tier_state = self._weekly_contribution_tier_state(received_button, locked_button)
        logger.info(f"World boss: {name} state={tier_state}")
        if tier_state in {"received", "locked"}:
            return False

        self.device.click(received_button)
        logger.info(f"World boss: click {name}")

        settle = Timer(self.WORLD_BOSS_WEEKLY_CONTRIBUTION_POST_CLICK_SETTLE_SECONDS, count=1).start()
        while not settle.reached():
            self.device.screenshot()

        points = self._ocr_weekly_contribution_points(ocr)
        if points is not None:
            logger.attr("WeeklyContribution", f"{points:,}")

        tier_state_after = self._weekly_contribution_tier_state(received_button, locked_button)
        logger.info(f"World boss: {name} after_click={tier_state_after}")
        return True

    def _claim_weekly_contribution_rewards(self, skip_first_screenshot=True) -> bool:
        logger.info("World boss: claim weekly contribution rewards")
        ocr_button = ClickButton(OCR_WEEKLY_CONTRIBUTION.area, name="OCR_WEEKLY_CONTRIBUTION")
        ocr = OcrWeeklyContribution(
            ocr_button,
            lang=self._get_world_boss_ocr_lang(),
            name="WeeklyContributionOCR",
        )

        if not skip_first_screenshot:
            self.device.screenshot()

        points = self._ocr_weekly_contribution_points(ocr)
        if points is not None:
            logger.attr("WeeklyContribution", f"{points:,}")

        for name, received_button, locked_button in [
            ("WEEKLY_CONTRIBUTION_TIER_1_RECEIVED", WEEKLY_CONTRIBUTION_TIER_1_RECEIVED, WEEKLY_CONTRIBUTION_TIER_1_LOCKED),
            ("WEEKLY_CONTRIBUTION_TIER_2_RECEIVED", WEEKLY_CONTRIBUTION_TIER_2_RECEIVED, WEEKLY_CONTRIBUTION_TIER_2_LOCKED),
        ]:
            self._claim_weekly_contribution_tier(
                ocr=ocr,
                name=name,
                received_button=received_button,
                locked_button=locked_button,
            )
        return True

    def run_world_boss(self, skip_first_screenshot=True) -> bool:
        logger.hr("Knights WorldBoss", level=2)
        self._world_boss_completed_rounds = 0

        enter_status = self._enter_world_boss(skip_first_screenshot=skip_first_screenshot)
        if enter_status == "locked":
            return True
        if enter_status != "entered":
            return False

        rounds = 0
        while 1:
            status = self._world_boss_once(skip_first_screenshot=True)
            if status == "completed":
                rounds += 1
                self._world_boss_completed_rounds = rounds
                logger.info(f"World boss round finished: {rounds}")
                continue

            if status == "exhausted":
                self._close_world_boss_exhausted_popup(skip_first_screenshot=True)
                self._claim_weekly_contribution_rewards(skip_first_screenshot=True)
                self._back_to_knights_from_world_boss(skip_first_screenshot=True)
                logger.info(f"World boss done: exhausted, rounds={rounds}")
                return True

            if status == "no_stamina":
                logger.info("World boss done: no stamina")
                self._claim_weekly_contribution_rewards(skip_first_screenshot=True)
                self._back_to_knights_from_world_boss(skip_first_screenshot=True)
                return True

            self._claim_weekly_contribution_rewards(skip_first_screenshot=True)
            self._back_to_knights_from_world_boss(skip_first_screenshot=True)
            return False
