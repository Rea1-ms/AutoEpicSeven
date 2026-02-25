from module.base.timer import Timer
from module.logger import logger
from tasks.base.page import page_knights, page_knights_world_boss
from tasks.knights.assets.assets_knights import KNIGHTS_CHECK
from tasks.knights.assets.assets_knights_expedition import (
    AUTO_CONFIG,
    BATTLE_RESULT_CONFIRM,
    BATTLE_START,
    CHOOSE_TEAM,
    CHOOSE_TEAM_CHECK,
    EMPTY_TEAM,
    FORM_A_TEAM,
    OPEN_ALL_BOX,
    OPEN_ALL_BOX_CONFIRM,
    READY_TO_FIGHT,
    RANK,
    SKIP,
    WORLD_BOSS,
)


class KnightsWorldBossMixin:
    READY_TO_FIGHT_LUMA_SIMILARITY = 0.8
    READY_TO_FIGHT_COLOR_THRESHOLD = 30
    WORLD_BOSS_FORM_RETRY_SECONDS = 4
    WORLD_BOSS_AUTO_CONFIG_RETRY_SECONDS = 1.2
    WORLD_BOSS_READY_RETRY_SECONDS = 2
    WORLD_BOSS_READY_PENDING_SECONDS = 2.5

    WORLD_BOSS_STAGE_ENTRY = "entry"
    WORLD_BOSS_STAGE_SELECT = "select_team"
    WORLD_BOSS_STAGE_SETUP = "setup_team"

    def _is_ready_to_fight(self, interval=0) -> bool:
        """
        READY_TO_FIGHT uses luma + color double check:
            luma match + color match => still has chances today.
        """
        self.device.stuck_record_add(READY_TO_FIGHT)

        if interval and not self.interval_is_reached(READY_TO_FIGHT, interval=interval):
            return False

        appear = False
        if READY_TO_FIGHT.match_template_luma(self.device.image, similarity=self.READY_TO_FIGHT_LUMA_SIMILARITY):
            if READY_TO_FIGHT.match_color(self.device.image, threshold=self.READY_TO_FIGHT_COLOR_THRESHOLD):
                appear = True

        if appear and interval:
            self.interval_reset(READY_TO_FIGHT, interval=interval)

        return appear

    def _is_ready_to_fight_exhausted(self) -> bool:
        """
        READY_TO_FIGHT template appears but color mismatch:
            means today chances are exhausted.
        """
        if READY_TO_FIGHT.match_template_luma(self.device.image, similarity=self.READY_TO_FIGHT_LUMA_SIMILARITY):
            return not READY_TO_FIGHT.match_color(
                self.device.image, threshold=self.READY_TO_FIGHT_COLOR_THRESHOLD
            )
        return False

    def _world_boss_no_stamina_todo(self) -> bool:
        """
        TODO:
            识别无体力弹窗，并与调度器联动延后任务。
        """
        return False

    def _enter_world_boss(self, skip_first_screenshot=True) -> bool:
        logger.info("Knights: enter world boss")
        self.ui_goto(page_knights_world_boss, skip_first_screenshot=skip_first_screenshot)
        return True

    def _world_boss_once(self, skip_first_screenshot=True) -> str:
        """
        Returns:
            str:
                completed / exhausted / no_stamina / failed
        """
        timeout = Timer(120, count=300).start()
        exhausted_confirm = Timer(1, count=2).start()
        settlement_progress = False
        stage = self.WORLD_BOSS_STAGE_ENTRY
        ready_retry = Timer(self.WORLD_BOSS_READY_RETRY_SECONDS, count=0).start()
        ready_pending_timer = Timer(self.WORLD_BOSS_READY_PENDING_SECONDS, count=0).start()
        ready_pending = False
        form_retry = Timer(self.WORLD_BOSS_FORM_RETRY_SECONDS, count=0).start()
        auto_config_retry = Timer(self.WORLD_BOSS_AUTO_CONFIG_RETRY_SECONDS, count=0).start()
        auto_config_clicked = False
        rank_selected = False

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning("World boss round timeout")
                return "failed"

            # Exhausted check should only be trusted at entry state and without pending transition.
            if stage == self.WORLD_BOSS_STAGE_ENTRY and not ready_pending:
                if self._is_ready_to_fight_exhausted():
                    if exhausted_confirm.reached():
                        logger.info("World boss chances exhausted")
                        return "exhausted"
                else:
                    exhausted_confirm.reset()
            else:
                exhausted_confirm.reset()

            if self._world_boss_no_stamina_todo():
                return "no_stamina"

            # Do NOT call ui_additional() here:
            # it includes AD_BUFF_X_CLOSE / TOUCH_TO_CLOSE, which can close
            # world-boss entry/select panels and cause READY_TO_FIGHT loops.
            if self.handle_network_error():
                timeout.reset()
                continue

            if self.appear(CHOOSE_TEAM_CHECK, interval=1):
                if stage == self.WORLD_BOSS_STAGE_ENTRY:
                    logger.info("World boss: entered choose team page")
                stage = self.WORLD_BOSS_STAGE_SELECT
                ready_pending = False

            if stage == self.WORLD_BOSS_STAGE_ENTRY:
                # Sometimes CHOOSE_TEAM appears before check marker is stable.
                if self.appear_then_click(CHOOSE_TEAM, interval=1):
                    logger.info("World boss: CHOOSE_TEAM appeared before check marker")
                    stage = self.WORLD_BOSS_STAGE_SELECT
                    ready_pending = False
                    timeout.reset()
                    continue

                if ready_pending:
                    if ready_pending_timer.reached():
                        ready_pending = False
                    else:
                        continue

                if self._is_ready_to_fight(interval=1):
                    if ready_retry.reached():
                        logger.info("World boss: READY_TO_FIGHT -> CHOOSE_TEAM")
                        self.device.click(READY_TO_FIGHT)
                        ready_retry.reset()
                        ready_pending = True
                        rank_selected = False
                        ready_pending_timer.reset()
                        timeout.reset()
                    continue

            if stage == self.WORLD_BOSS_STAGE_SELECT:
                if self.appear(FORM_A_TEAM):
                    logger.info("World boss: FORM_A_TEAM -> team setup")
                    self.device.click(FORM_A_TEAM)
                    stage = self.WORLD_BOSS_STAGE_SETUP
                    ready_pending = False
                    form_retry.reset()
                    auto_config_retry.clear()
                    auto_config_clicked = False
                    timeout.reset()
                    continue

                if self.appear_then_click(CHOOSE_TEAM, interval=2):
                    rank_selected = False
                    timeout.reset()
                    continue

                if (not rank_selected) and self.appear_then_click(RANK, interval=1):
                    rank_selected = True
                    timeout.reset()
                    continue

            if stage == self.WORLD_BOSS_STAGE_SETUP:
                # Returned to select panel unexpectedly.
                if self.appear(CHOOSE_TEAM) and not any(
                    [self.appear(FORM_A_TEAM), self.appear(AUTO_CONFIG), self.appear(BATTLE_START), self.appear(EMPTY_TEAM)]
                ):
                    stage = self.WORLD_BOSS_STAGE_SELECT
                    ready_pending = False
                    continue

                # Retry AUTO_CONFIG at a fixed cadence to avoid click spam.
                # Keep AUTO_CONFIG before BATTLE_START to ensure team is generated.
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
                    ready_pending = False
                    rank_selected = False
                    auto_config_clicked = False
                    timeout.reset()
                    continue

                # If setup failed and FORM_A_TEAM is still visible, retry entering setup slowly.
                if self.appear(FORM_A_TEAM) and form_retry.reached():
                    logger.info("World boss: FORM_A_TEAM still visible, retry")
                    self.device.click(FORM_A_TEAM)
                    form_retry.reset()
                    auto_config_retry.clear()
                    auto_config_clicked = False
                    ready_pending = False
                    timeout.reset()
                    continue

            if stage == self.WORLD_BOSS_STAGE_SELECT:
                if self._is_ready_to_fight(interval=0) and not self.appear(CHOOSE_TEAM_CHECK, interval=0):
                    stage = self.WORLD_BOSS_STAGE_ENTRY
                    ready_pending = False
                    rank_selected = False
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

            # End of one world boss round:
            # back to READY_TO_FIGHT or entry layer.
            if settlement_progress:
                if self._is_ready_to_fight(interval=0):
                    return "completed"
                if self.appear(WORLD_BOSS):
                    return "completed"
                if self.appear(KNIGHTS_CHECK):
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

            if self.appear(READY_TO_FIGHT):
                return True
            if self.appear(WORLD_BOSS) or self.appear(KNIGHTS_CHECK):
                return True

            if self.handle_ad_buff_x_close(interval=1):
                timeout.reset()
                continue
            if self.handle_touch_to_close(interval=1):
                timeout.reset()
                continue
            if self.ui_additional():
                timeout.reset()
                continue
            if self.handle_network_error():
                timeout.reset()
                continue

    def _back_to_knights_from_world_boss(self, skip_first_screenshot=True) -> bool:
        self.ui_goto(page_knights, skip_first_screenshot=skip_first_screenshot)
        return True

    def run_world_boss(self, skip_first_screenshot=True) -> bool:
        logger.hr("Knights WorldBoss", level=2)

        if not self._enter_world_boss(skip_first_screenshot=skip_first_screenshot):
            return False

        rounds = 0
        while 1:
            status = self._world_boss_once(skip_first_screenshot=True)
            if status == "completed":
                rounds += 1
                logger.info(f"World boss round finished: {rounds}")
                continue

            if status == "exhausted":
                self._close_world_boss_exhausted_popup(skip_first_screenshot=True)
                self._back_to_knights_from_world_boss(skip_first_screenshot=True)
                logger.info(f"World boss done: exhausted, rounds={rounds}")
                return True

            if status == "no_stamina":
                logger.info("World boss no stamina TODO: scheduler integration pending")
                self._back_to_knights_from_world_boss(skip_first_screenshot=True)
                return True

            self._back_to_knights_from_world_boss(skip_first_screenshot=True)
            return False
