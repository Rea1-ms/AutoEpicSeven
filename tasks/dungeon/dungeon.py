from module.logger import logger
from tasks.base.page import page_combat
from tasks.base.resource_bar import ResourceBarMixin
from tasks.base.ui import UI
from tasks.dungeon.entry import CombatEntryMixin
from tasks.dungeon.execute import CombatExecuteMixin
from tasks.dungeon.plan import COMBAT_PLANS, HUNT_PLAN
from tasks.dungeon.prepare import CombatPrepare
from tasks.dungeon.runtime import CombatRuntimeMixin
from tasks.dungeon.side_story import SideStoryNavigateMixin


class Combat(CombatRuntimeMixin, CombatExecuteMixin, CombatEntryMixin, SideStoryNavigateMixin, CombatPrepare, ResourceBarMixin, UI):
    COMBAT_RESOURCE_BAR_TIMEOUT_SECONDS = 1
    COMBAT_RESOURCE_BAR_TIMEOUT_COUNT = 2
    COMBAT_CHECK_SIMILARITY = 0.8
    COMBAT_STATE_COLOR_THRESHOLD = 30
    COMBAT_ENTRY_TIMEOUT_SECONDS = 25
    COMBAT_SELECT_TIMEOUT_SECONDS = 20
    COMBAT_PREPARE_TIMEOUT_SECONDS = 25
    COMBAT_RUN_TIMEOUT_SECONDS = 90
    COMBAT_WATCH_TIMEOUT_SECONDS = 20
    COMBAT_MISSING_CHECK_CONFIRM_SECONDS = 1
    COMBAT_EXIT_TIMEOUT_SECONDS = 25
    COMBAT_SCROLL_INTERVAL_SECONDS = 1
    COMBAT_SCROLL_SETTLE_SECONDS = 1.5
    COMBAT_ELEMENT_CLICK_PENDING_SECONDS = 1.2
    COMBAT_BACKGROUND_CHECK_MINUTES = 1
    COMBAT_GRADE_PENDING_SECONDS = 2.5
    COMBAT_START_PENDING_SECONDS = 4.5
    COMBAT_TOGGLE_INTERVAL_SECONDS = 1
    COMBAT_START_INTERVAL_SECONDS = 1.2
    COMBAT_RESULT_INTERVAL_SECONDS = 1
    COMBAT_MAX_SCROLLS = 6
    COMBAT_SCROLL_X = 1100
    COMBAT_SCROLL_START_Y = 620
    COMBAT_SCROLL_END_Y = 220

    @staticmethod
    def _should_schedule_mission_reward(completed_sessions: int, runtime_active: bool) -> bool:
        """
        Only leave combat to claim mission rewards after at least one combat
        session has fully settled and no background repeat-combat session is
        still running.
        """
        return completed_sessions > 0 and not runtime_active

    def _combat_runs_repeat_in_background(self, use_fast_combat: bool) -> bool:
        """
        Return whether the current combat run should end in background repeat.

        CombatFarm always consumes fast combat first when available, then
        continues with repeat combat in background. The daily Combat task only
        runs repeat combat when fast combat is disabled, unsupported, or
        unavailable.
        """
        return self._combat_is_farm_task() or not use_fast_combat

    def _dungeon_domain(self) -> str:
        return getattr(self.config, "Combat_Domain", "Hunt")

    def _combat_plan(self):
        return COMBAT_PLANS.get(self._dungeon_domain(), HUNT_PLAN)

    def _combat_is_farm_task(self) -> bool:
        return getattr(getattr(self.config, "task", None), "command", "Combat") == "CombatFarm"

    def _combat_element(self) -> str:
        return getattr(self.config, "Combat_Element", "Water")

    def _combat_grade(self) -> str:
        domain = self._dungeon_domain()
        if domain == "Saint37":
            return "3-7"
        if domain == "SpiritAltar":
            return getattr(self.config, "Combat_AltarGrade", "Hell")
        return getattr(self.config, "Combat_HuntGrade", "Hell")

    def _combat_fast_enabled(self) -> bool:
        return bool(getattr(self.config, "Combat_FastCombat", True))

    def _combat_supports_fast_combat(self) -> bool:
        """
        Return whether the current combat target provides a fast-combat toggle.

        Side story and Dimensional Hunt do not expose the fast-combat button on
        the prepare page. GUI-side hiding alone is not enough because an old
        persisted config may still keep FastCombat=True. Keep this rule on the
        backend so state loops do not try to click a missing toggle and get
        stuck on the prepare page.
        """
        domain = self._dungeon_domain()
        if domain == "Saint37":
            return False
        return not (domain == "Hunt" and self._combat_grade() == "Dimensional")

    def _combat_should_use_fast(self) -> bool:
        if not self._combat_supports_fast_combat():
            return False
        return self._combat_fast_enabled()

    def _combat_delay_after_settled(self) -> None:
        if self._combat_is_farm_task():
            self.config.task_delay(minute=self.COMBAT_BACKGROUND_CHECK_MINUTES)
        else:
            self.config.task_delay(server_update=True)

    def _combat_should_call_mission_reward(self) -> bool:
        return not self._combat_is_farm_task()

    def _combat_should_cleanup_saint37_reward_items(self) -> bool:
        return self._combat_is_saint37() and bool(getattr(self.config, "Combat_Saint37AutoRecycle", False))

    def _dungeon_navigate(self, skip_first_screenshot=True) -> bool:
        domain = self._dungeon_domain()
        if domain == "Saint37":
            return self._navigate_side_story(skip_first_screenshot=skip_first_screenshot)

        plan = self._combat_plan()
        success = self._enter_stage_page(plan, skip_first_screenshot=skip_first_screenshot)
        if success:
            success = self._select_element(plan, skip_first_screenshot=skip_first_screenshot)
        if success:
            success = self._enter_prepare_page(plan, skip_first_screenshot=skip_first_screenshot)
        return success

    def run(self) -> bool:
        logger.hr("Combat", level=1)
        completed_sessions = 0

        if not self.device.app_is_running():
            from tasks.login.login import Login

            Login(self.config, device=self.device).app_start()

        if not hasattr(self.device, "image") or self.device.image is None:
            self.device.screenshot()

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
            logger.info("Combat: detected dungeon context, skip goto main")
        elif not self.is_in_main(interval=0):
            # Route into the combat hub directly instead of always backing out
            # to main first. _enter_stage_page() will normalize season/common
            # boards afterwards, while menu-aware page routing can now choose
            # the shorter shared-toolbar path from other supported pages.
            self.ui_goto(page_combat, skip_first_screenshot=True)

        self._adopt_existing_background_repeat_combat()

        if self._combat_runtime_active():
            session = self._combat_runtime_session()
            logger.info("Combat: background session active, watch current session")
            if session.get("source"):
                logger.attr("CombatSessionSource", session.get("source"))
            logger.attr("CombatSessionDomain", session.get("domain"))
            logger.attr("CombatSessionElement", session.get("element"))
            logger.attr("CombatSessionGrade", session.get("grade"))

            if not self.is_in_main(interval=0):
                # Return to main so the session can keep running in background.
                self.ui_goto_main()

            status = self._watch_repeat_combat(skip_first_screenshot=True)
            if status == "finished":
                completed_sessions += 1
                self._combat_runtime_clear()
                if self._combat_should_call_mission_reward() and self._should_schedule_mission_reward(
                    completed_sessions,
                    runtime_active=self._combat_runtime_active(),
                ):
                    self.config.task_call("MissionReward", force_call=False)
                self._combat_delay_after_settled()
                return True

            if status == "lost":
                logger.warning("Combat: background session lost, relaunch combat")
                self._combat_runtime_clear()
            else:
                self.config.task_delay(minute=self.COMBAT_BACKGROUND_CHECK_MINUTES)
                return True

        domain = self._dungeon_domain()
        use_fast_combat = self._combat_should_use_fast()
        repeat_in_background = self._combat_runs_repeat_in_background(use_fast_combat)

        logger.attr("CombatDomain", domain)
        logger.attr("CombatElement", self._combat_element())
        logger.attr("CombatGrade", self._combat_grade())
        logger.attr("CombatFastCombatSupported", self._combat_supports_fast_combat())
        logger.attr("CombatFastCombat", use_fast_combat)
        logger.attr("CombatFastCombatCount", self._combat_fast_count())
        logger.attr("CombatRepeatCombatCount", self._combat_repeat_count())
        logger.attr("CombatRepeatInBackground", repeat_in_background)
        if domain == "Saint37":
            logger.attr("CombatSaint37AutoRecycle", self._combat_should_cleanup_saint37_reward_items())

        success = self._dungeon_navigate(skip_first_screenshot=True)

        if success and use_fast_combat and self._is_fast_combat_locked():
            logger.warning("Combat: fast combat locked, fallback to repeat combat")
            use_fast_combat = False

        if success:
            if use_fast_combat:
                fast_prepare = self._prepare_fast_combat(
                    use_max=self._combat_is_farm_task(),
                    skip_first_screenshot=True,
                )
                if fast_prepare == "fallback":
                    use_fast_combat = False
                    repeat_in_background = self._combat_runs_repeat_in_background(use_fast_combat)
                else:
                    success = fast_prepare == "ready"

        if success:
            if use_fast_combat:
                success = self._run_fast_combat(skip_first_screenshot=True)
                if success:
                    completed_sessions += 1

            if success and repeat_in_background:
                success = self._prepare_repeat_combat(
                    use_max=self._combat_is_farm_task(),
                    skip_first_screenshot=True,
                )
                if success:
                    success = self._run_repeat_combat(skip_first_screenshot=True)

        if success and use_fast_combat and not repeat_in_background:
            success = self._leave_to_main(skip_first_screenshot=True)

        if success:
            if self._combat_should_call_mission_reward() and self._should_schedule_mission_reward(
                completed_sessions,
                runtime_active=repeat_in_background,
            ):
                self.config.task_call("MissionReward", force_call=False)
            if repeat_in_background:
                self._combat_runtime_set(self._combat_runtime_build())
                self.config.task_delay(minute=self.COMBAT_BACKGROUND_CHECK_MINUTES)
            else:
                self._combat_runtime_clear()
                self._combat_delay_after_settled()
            return True

        self._combat_runtime_clear()
        self._leave_to_main(skip_first_screenshot=True)
        if self._combat_should_call_mission_reward() and self._should_schedule_mission_reward(
            completed_sessions,
            runtime_active=self._combat_runtime_active(),
        ):
            self.config.task_call("MissionReward", force_call=False)
        self.config.task_delay(success=False)
        return False
