from module.base.timer import Timer
from module.logger import logger
from tasks.base.assets.assets_base_page import BACK
from tasks.dungeon.assets.assets_dungeon_repeat_result import REPEAT_COMBAT_OVER


class CombatRuntimeMixin:
    def _combat_runtime_path(self) -> str:
        task = getattr(getattr(self.config, "task", None), "command", "Combat")
        return f"{task}.CombatRuntime.Session"

    def _combat_runtime_session(self) -> dict:
        session = self.config.cross_get(self._combat_runtime_path(), default={})
        return session if isinstance(session, dict) else {}

    def _combat_runtime_active(self) -> bool:
        return bool(self._combat_runtime_session().get("active"))

    def _combat_runtime_set(self, session: dict) -> None:
        self.config.cross_set(self._combat_runtime_path(), session)

    def _combat_runtime_clear(self) -> None:
        self._combat_runtime_set({})

    def _combat_runtime_build(self) -> dict:
        domain = self._dungeon_domain()
        return {
            "active": True,
            "mode": "repeat_background",
            "domain": domain,
            "element": None if domain == "Saint37" else self._combat_element(),
            "grade": self._combat_grade(),
        }

    def _combat_runtime_build_detected_existing(self) -> dict:
        return {
            "active": True,
            "mode": "repeat_background",
            "source": "detected_existing",
        }

    def _adopt_existing_background_repeat_combat(self) -> bool:
        if self._combat_runtime_active():
            return False
        if not self.is_in_main(interval=0):
            return False
        if not self._is_background_repeat_combat_running():
            return False

        logger.info("Combat: detected existing background repeat combat before task start")
        self._combat_runtime_set(self._combat_runtime_build_detected_existing())
        return True

    def _watch_repeat_combat(self, skip_first_screenshot=True) -> str:
        """
        Watch a background repeat-combat session from main.

        The return value is intentionally tri-state:
        - running: session is still active, or state is temporarily ambiguous
        - finished: result window has been consumed and we are back on main
        - lost: repeat marker disappeared twice on main, treat session as gone
        """
        logger.info("Combat: watch repeat combat")
        timeout = Timer(self.COMBAT_WATCH_TIMEOUT_SECONDS, count=60).start()
        stage = "watch"
        result_main_confirm = Timer(0.4, count=2).clear()
        missing_check_confirm = Timer(self.COMBAT_MISSING_CHECK_CONFIRM_SECONDS, count=2).clear()

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning("Combat: repeat combat watch timeout, keep session active")
                return "running"

            if self._handle_dungeon_network_error(interval=1):
                logger.warning("Combat: background repeat combat has network error, keep session active")
                return "running"

            if stage == "watch":
                if self.appear_then_click(REPEAT_COMBAT_OVER, interval=1):
                    logger.info("Combat: repeat combat over, open result")
                    stage = "result"
                    missing_check_confirm.clear()
                    timeout.reset()
                    continue

                if self._is_repeat_result_window():
                    stage = "result"
                    missing_check_confirm.clear()
                    timeout.reset()
                    continue

                if self._is_repeat_combat_running():
                    missing_check_confirm.clear()
                    logger.info("Combat: repeat combat still running in background")
                    return "running"

                if self.is_in_main(interval=0):
                    if not missing_check_confirm.started():
                        logger.info("Combat: repeat combat check missing once, confirm again")
                        missing_check_confirm.start()
                    elif missing_check_confirm.reached():
                        logger.warning("Combat: repeat combat session active but check is missing")
                        return "lost"
                else:
                    missing_check_confirm.clear()

                if self._handle_dungeon_additional():
                    missing_check_confirm.clear()
                    timeout.reset()
                    continue
                continue

            if stage == "result":
                if self.appear_then_click(REPEAT_COMBAT_OVER, interval=1):
                    timeout.reset()
                    continue

                if self._combat_should_cleanup_saint37_reward_items():
                    if self._cleanup_saint37_reward_items(skip_first_screenshot=True):
                        stage = "finish"
                        timeout.reset()
                        result_main_confirm.clear()
                        continue
                    logger.warning("Combat Saint37: cleanup failed, fallback to close repeat result")

                if self._is_repeat_result_window():
                    if self.handle_ad_buff_x_close(interval=0.5):
                        logger.info("Combat: close repeat combat result")
                        stage = "finish"
                        timeout.reset()
                        result_main_confirm.clear()
                        continue
                    timeout.reset()
                    continue

                if self.handle_ad_buff_x_close(interval=0.5):
                    logger.info("Combat: close repeat combat result")
                    stage = "finish"
                    timeout.reset()
                    result_main_confirm.clear()
                    continue

                if self._handle_dungeon_additional():
                    timeout.reset()
                    continue
                continue

            if stage == "finish":
                if self._is_repeat_result_window():
                    if self.handle_ad_buff_x_close(interval=0.5):
                        timeout.reset()
                        continue
                    timeout.reset()
                    continue

                if self.is_in_main(interval=0):
                    if not result_main_confirm.started():
                        result_main_confirm.start()
                    elif result_main_confirm.reached():
                        logger.info("Combat: repeat combat finished")
                        return "finished"
                else:
                    result_main_confirm.clear()

                if self._handle_dungeon_additional():
                    timeout.reset()
                    continue
                continue

    def _leave_to_main(self, skip_first_screenshot=True) -> bool:
        """
        Recover back to main from dungeon-local pages.

        This is kept separate from generic page routing on purpose. Dungeon
        tasks can leave on transient local states such as stage boards, prepare
        pages, side story sub-pages, or result windows that are not stable
        routing nodes. A generic ui_goto(page_main) is fine on clean success
        paths, but failure cleanup still needs a dungeon-aware unwind helper.

        Pages:
            in: dungeon-local pages (combat or side story)
            out: main
        """
        logger.info("Dungeon: return to main")
        timeout = Timer(self.COMBAT_EXIT_TIMEOUT_SECONDS, count=80).start()

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning("Dungeon: return to main timeout")
                return False

            if self.is_in_main(interval=0):
                return True

            if self._handle_dungeon_additional():
                timeout.reset()
                continue

            if (
                self._is_prepare_page()
                or self._is_stage_page()
                or self._is_combat_general_board()
                or self._is_combat_season_board()
                or self._is_combat_urgent_board()
                or self._is_side_story_page()
                or self._is_time_book_page()
                or self._is_episode_preview_page()
                or self._is_side_story_map_page()
                or self._is_supporter_page()
            ):
                if self.appear_then_click(BACK, interval=1):
                    timeout.reset()
                    continue
