import module.config.server as server
from module.logger import logger
from tasks.activity.scheduling import should_schedule_after_battle
from tasks.base.page import page_combat, page_episode, page_side_story
from tasks.base.resource_bar import ResourceBarMixin
from tasks.base.ui import UI
from tasks.dungeon.burnout import CombatBurnoutMixin
from tasks.dungeon.episode import EpisodeNavigateMixin
from tasks.dungeon.entry import CombatEntryMixin
from tasks.dungeon.execute import CombatExecuteMixin
from tasks.dungeon.plan import COMBAT_PLANS, HUNT_PLAN
from tasks.dungeon.prepare import CombatPrepare
from tasks.dungeon.repeat import CombatRepeatMixin
from tasks.dungeon.runtime import CombatRuntimeMixin, is_background_repeat_combat_active
from tasks.dungeon.side_story import SideStoryNavigateMixin
from tasks.dungeon.stamina_status import CombatStaminaStatusMixin
from tasks.mission_reward.scheduling import should_schedule_mission_reward


class Combat(
    CombatRepeatMixin,
    CombatBurnoutMixin,
    CombatRuntimeMixin,
    CombatExecuteMixin,
    CombatEntryMixin,
    EpisodeNavigateMixin,
    SideStoryNavigateMixin,
    CombatStaminaStatusMixin,
    CombatPrepare,
    ResourceBarMixin,
    UI,
):
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
    def _should_schedule_mission_reward(
        completed_sessions: int, runtime_active: bool
    ) -> bool:
        """
        Only leave combat to claim mission rewards after at least one combat
        session has fully settled and no background repeat-combat session is
        still running.
        """
        return completed_sessions > 0 and not runtime_active

    def _dungeon_domain(self) -> str:
        return getattr(self.config, "Combat_Domain", "Hunt")

    def _combat_plan(self):
        return COMBAT_PLANS.get(self._dungeon_domain(), HUNT_PLAN)

    def _combat_is_farm_task(self) -> bool:
        return (
            getattr(getattr(self.config, "task", None), "command", "Combat")
            == "CombatFarm"
        )

    def _combat_element(self) -> str:
        return getattr(self.config, "Combat_Element", "Water")

    def _combat_grade(self) -> str:
        domain = self._dungeon_domain()
        if domain == "Episode4":
            return self._combat_episode_target().stage_label
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

        Saint 3-7 and Dimensional Hunt do not expose the fast-combat button on
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
        if not self._uses_server_repeat_combat() and is_background_repeat_combat_active(
            self.config
        ):
            logger.info("Combat: background repeat combat active, use normal combat")
            return False
        return self._combat_fast_enabled()

    def _combat_delay_after_settled(self) -> None:
        if self._combat_is_farm_task():
            self.config.task_delay(minute=self.COMBAT_BACKGROUND_CHECK_MINUTES)
            return
        # Burnout mode schedules the next run by stamina regeneration. It
        # only applies to fully settled runs; failure paths keep the normal
        # failure retry interval.
        if self._combat_burnout_schedule():
            return
        self.config.task_delay(server_update=True)

    def _combat_should_call_mission_reward(self) -> bool:
        return not self._combat_is_farm_task()

    def _combat_should_cleanup_saint37_reward_items(self) -> bool:
        if self._uses_server_repeat_combat():
            return False
        return self._combat_is_saint37() and bool(
            getattr(self.config, "Combat_Saint37AutoRecycle", False)
        )

    def _is_in_dungeon_context(self) -> bool:
        """
        Return whether the current screen is already inside the dungeon flow.

        When this is true we do not normalize to main before continuing,
        because doing so would destroy useful local context such as:
        - an already opened prepare page
        - a stage selection page that the user intentionally left open
        - a side-story sub-page on the Saint 3-7 route
        """
        return (
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
            or self._is_episode_stage_page()
            or self._is_episode_choose_page()
            or self._is_episode_supporter_page()
        )

    def _is_in_saint37_flow_context(self) -> bool:
        """
        Return whether the current screen is already on the Saint 3-7 route.

        Saint 3-7 lives under Side Story instead of the normal combat hub.
        Generic combat boards such as `page_combat_common` are still "dungeon
        context", but they are not useful local context for the Saint flow and
        should be routed into `page_side_story` before navigation starts.
        """
        return (
            self._is_prepare_page()
            or self._is_side_story_page()
            or self._is_time_book_page()
            or self._is_episode_preview_page()
            or self._is_side_story_map_page()
            or self._is_supporter_page()
        )

    def _is_in_episode4_flow_context(self) -> bool:
        return (
            self._is_prepare_page()
            or self._is_episode_choose_page()
            or self._is_episode_stage_page()
            or self._is_episode_supporter_page()
        )

    def _is_background_repeat_check_page(self, page) -> bool:
        return bool(getattr(page, "background_repeat_check", False))

    def _prepare_background_repeat_check_context(self) -> None:
        """
        Normalize to a safe page before checking for an old background run.

        Why this exists:
            Old repeat-combat session handling has higher priority than opening
            a new dungeon flow. Before deciding whether to continue the current
            local flow or route into a fresh one, we must first answer:

                "Is there already an older background repeat-combat session
                that this task should adopt instead of starting a new run?"

            That question can only be asked on a safe page:
            - a shared-toolbar page where the top-right repeat marker is
              expected to be stable
            - or page_main as the global fallback

            Do not detect directly on arbitrary current screenshots. A local
            dungeon page may still be valid context for the *new* flow while
            being unsafe for old-session detection, and those two concerns must
            stay separate.

        Current policy:
            - If the current page is already a safe background-repeat precheck
              page, keep that local context and run the old-session detection
              there.
            - Otherwise, normalize to main first, then perform the old-session
              detection on main.

        We keep this separate from the real combat flow on purpose. The
        old-session branch must be decided before we continue any new Combat
        or Saint37 navigation.
        """
        if self._combat_runtime_active():
            return

        current = self.ui_get_current_page(skip_first_screenshot=True)
        if self._is_supporter_page():
            # The side-story choose-team page is already deep inside the local
            # dungeon flow. It does not expose the stable top-right background
            # repeat marker used by startup precheck, so do not try to treat it
            # like a shared-toolbar page. Normalize to main first, then run the
            # old-session adoption check there.
            logger.info(
                "Combat: goto main before checking background repeat combat from supporter page"
            )
            self.ui_goto_main()
            self.device.screenshot()
            return

        if self._is_background_repeat_check_page(current):
            logger.info(f"Combat: precheck background repeat combat on {current}")
            self.device.screenshot()
            return

        logger.info(
            "Combat: current page is unsafe for background repeat precheck, goto main"
        )
        self.ui_goto_main()
        self.device.screenshot()

    def _dungeon_navigate(self, skip_first_screenshot=True) -> bool:
        domain = self._dungeon_domain()
        if domain == "Episode4":
            return self._navigate_episode4(skip_first_screenshot=skip_first_screenshot)
        if domain == "Saint37":
            return self._navigate_side_story(
                skip_first_screenshot=skip_first_screenshot
            )

        plan = self._combat_plan()
        success = self._enter_stage_page(
            plan, skip_first_screenshot=skip_first_screenshot
        )
        if success:
            success = self._select_element(
                plan, skip_first_screenshot=skip_first_screenshot
            )
        if success:
            success = self._enter_prepare_page(
                plan, skip_first_screenshot=skip_first_screenshot
            )
        return success

    def run(self) -> bool:
        logger.hr("Combat", level=1)
        completed_sessions = 0

        if (
            server.is_oversea_server(self.config.Emulator_PackageName)
            and server.lang != "global_cn"
        ):
            logger.info(
                "Combat: the new global-server repeat flow currently supports Chinese assets only; "
                f"skip language={server.lang}"
            )
            self.config.task_delay(server_update=True)
            return True

        if not self.device.app_is_running():
            from tasks.login.login import Login

            Login(self.config, device=self.device).app_start()

        if not hasattr(self.device, "image") or self.device.image is None:
            self.device.screenshot()

        # Before opening a new dungeon flow, first make sure we are on a page
        # where the background repeat marker is expected to be visible, then
        # adopt the old session if one is still alive.
        self._prepare_background_repeat_check_context()
        self._adopt_existing_background_repeat_combat()

        if self._combat_runtime_active():
            session = self._combat_runtime_session()
            logger.info("Combat: background session active, watch current session")
            if session.get("source"):
                logger.attr("CombatSessionSource", session.get("source"))
            if session.get("state"):
                logger.attr("CombatSessionState", session.get("state"))
            logger.attr("CombatSessionDomain", session.get("domain"))
            logger.attr("CombatSessionElement", session.get("element"))
            logger.attr("CombatSessionGrade", session.get("grade"))

            if session.get("state") != "result" and not self.is_in_main(interval=0):
                # Return to main so the session can keep running in background.
                self.ui_goto_main()

            status = self._watch_repeat_combat(skip_first_screenshot=True)
            if status == "finished":
                completed_sessions += 1
                self._combat_runtime_clear()
                should_schedule_reward = (
                    self._combat_should_call_mission_reward()
                    and self._should_schedule_mission_reward(
                        completed_sessions,
                        runtime_active=self._combat_runtime_active(),
                    )
                )
                if should_schedule_reward:
                    if should_schedule_mission_reward(self.config):
                        self.config.task_call("MissionReward", force_call=False)
                    if should_schedule_after_battle(self.config):
                        self.config.task_call("SpecialActivity", force_call=False)
                self._combat_delay_after_settled()
                return True

            if status == "lost":
                logger.warning("Combat: background session lost, relaunch combat")
                self._combat_runtime_clear()
            else:
                self._delay_running_repeat_combat()
                return True

        domain = self._dungeon_domain()
        if domain == "Episode4":
            if self._is_in_episode4_flow_context():
                logger.info("Combat Episode4: continue local episode flow")
            else:
                logger.info("Combat Episode4: route to episode hub before navigation")
                self.ui_goto(page_episode, skip_first_screenshot=True)
        elif domain == "Saint37":
            if self._is_in_saint37_flow_context():
                logger.info("Combat Saint37: continue local side story flow")
            else:
                logger.info("Combat Saint37: route to side story hub before navigation")
                self.ui_goto(page_side_story, skip_first_screenshot=True)
        elif not self._is_in_dungeon_context() and not self.is_in_main(interval=0):
            # Route into the combat hub only after the old-background-session
            # adoption check has already had a chance to run on main.
            self.ui_goto(page_combat, skip_first_screenshot=True)
        elif self._is_in_dungeon_context():
            logger.info("Combat: continue local dungeon flow")

        fast_combat_allowed = self._combat_fast_enabled()
        fast_combat_selected = self._combat_should_use_fast()
        fast_combat_prepared_count = 0
        fast_combat_completed_count = 0
        repeat_combat_planned = True
        repeat_combat_started = False

        logger.attr("CombatDomain", domain)
        logger.attr("CombatElement", self._combat_element())
        logger.attr("CombatGrade", self._combat_grade())
        logger.attr("CombatFastCombatSupported", self._combat_supports_fast_combat())
        logger.attr("CombatFastCombatAllowed", fast_combat_allowed)
        logger.attr("CombatFastCombatSelected", fast_combat_selected)
        logger.attr("CombatFastCombatCount", self._combat_fast_count())
        if self._uses_server_repeat_combat():
            logger.attr(
                "CombatRepeatCombatLeifCount",
                "stamina // 80"
                if self._combat_burnout_enabled()
                else self._repeat_combat_leif_count(),
            )
            logger.attr(
                "CombatRepeatPrioritizeStamina", self._repeat_prioritize_stamina()
            )
            logger.attr("CombatRepeatGearMode", self._repeat_gear_mode())
        elif self._combat_burnout_enabled():
            logger.attr("CombatRepeatCombatCount", "maximum affordable")
        else:
            logger.attr("CombatRepeatCombatCount", self._combat_repeat_count())
        logger.attr("CombatRepeatCombatPlanned", repeat_combat_planned)
        if domain == "Saint37":
            logger.attr(
                "CombatSaint37AutoRecycle",
                self._combat_should_cleanup_saint37_reward_items(),
            )

        success = self._dungeon_navigate(skip_first_screenshot=True)

        prepare_resources = None
        current_stamina = None
        if success:
            prepare_resources = self._update_prepare_resource_snapshot(
                skip_first_screenshot=True
            )
            success = prepare_resources is not None
            if prepare_resources is not None:
                stamina = prepare_resources.get("stamina")
                current_stamina = stamina.value if stamina is not None else None

        if success and fast_combat_selected and self._is_fast_combat_locked():
            logger.warning("Combat: fast combat locked, fallback to repeat combat")
            fast_combat_selected = False

        if success and not self._uses_server_repeat_combat():
            if fast_combat_selected:
                if current_stamina is None:
                    logger.warning("Combat: no stamina field available for fast combat")
                    success = False
                else:
                    fast_prepare, fast_combat_prepared_count = (
                        self._prepare_fast_combat(
                            stamina=current_stamina,
                            use_max=self._combat_is_farm_task(),
                            skip_first_screenshot=True,
                        )
                    )
                    if fast_prepare == "fallback":
                        fast_combat_selected = False
                    elif fast_prepare == "no_stamina":
                        if self._uses_server_repeat_combat():
                            logger.info(
                                "Combat: no stamina for fast combat, continue with server repeat"
                            )
                            fast_combat_selected = False
                        else:
                            logger.info(
                                "Combat: no stamina available, leave prepare page"
                            )
                            self._combat_runtime_clear()
                            if self._leave_to_main(skip_first_screenshot=True):
                                self._combat_delay_after_settled()
                                return True
                            self.config.task_delay(success=False)
                            return False
                    else:
                        success = fast_prepare == "ready"

        if success and self._uses_server_repeat_combat():
            target_leif_count = self._server_repeat_target_leif_count(current_stamina)
            logger.attr("CombatRepeatTargetLeifCount", target_leif_count)
            fast_stamina = current_stamina or 0

            if target_leif_count > 0:
                success = self._prepare_repeat_combat(
                    leif_count=target_leif_count,
                    skip_first_screenshot=True,
                )
                if success:
                    success = self._run_repeat_combat(skip_first_screenshot=True)
                    repeat_combat_started = success

                if success:
                    prepared_leif_count = getattr(
                        self, "_repeat_combat_prepared_leif_count", target_leif_count
                    )
                    reserved_stamina, fast_stamina = self._server_repeat_stamina_budget(
                        current_stamina or 0,
                        prepared_leif_count,
                        self._repeat_prioritize_stamina(),
                    )
                    logger.attr("CombatRepeatReservedStamina", reserved_stamina)
                    logger.attr("CombatFastAvailableStamina", fast_stamina)
            else:
                logger.info("Combat: less than one 80-stamina unit, skip server repeat")

            if success and fast_combat_selected and fast_stamina > 0:
                fast_prepare, fast_combat_prepared_count = self._prepare_fast_combat(
                    stamina=fast_stamina,
                    use_max=False,
                    skip_first_screenshot=True,
                )
                if fast_prepare == "ready":
                    success = self._run_fast_combat(skip_first_screenshot=True)
                    if success:
                        fast_combat_completed_count = fast_combat_prepared_count
                        completed_sessions += 1
                elif fast_prepare == "fallback":
                    fast_combat_selected = False
                elif fast_prepare == "no_stamina":
                    logger.info("Combat: remaining stamina cannot start fast combat")
                else:
                    success = False

        elif success:
            if fast_combat_selected:
                success = self._run_fast_combat(skip_first_screenshot=True)
                if success:
                    fast_combat_completed_count = fast_combat_prepared_count
                    completed_sessions += 1
                    prepare_resources = self._update_prepare_resource_snapshot(
                        skip_first_screenshot=True
                    )
                    success = prepare_resources is not None
                    if prepare_resources is not None:
                        stamina = prepare_resources.get("stamina")
                        current_stamina = stamina.value if stamina is not None else None

            if success and repeat_combat_planned:
                if self._uses_server_repeat_combat():
                    success = self._prepare_repeat_combat(skip_first_screenshot=True)
                    if success:
                        success = self._run_repeat_combat(skip_first_screenshot=True)
                        repeat_combat_started = success
                else:
                    stamina_cost = self._combat_stage_stamina_cost()
                    affordable_count = None
                    if stamina_cost is not None:
                        assert current_stamina is not None
                        affordable_count = current_stamina // stamina_cost
                        logger.attr("CombatRepeatAffordableCount", affordable_count)

                    if not (
                        self._combat_is_farm_task() or self._combat_burnout_enabled()
                    ):
                        fixed_remaining = max(
                            self._combat_repeat_count() - fast_combat_completed_count,
                            0,
                        )
                        if affordable_count is None:
                            affordable_count = fixed_remaining
                        else:
                            affordable_count = min(affordable_count, fixed_remaining)
                        logger.attr("CombatRepeatFixedRemainingCount", fixed_remaining)

                    if affordable_count == 0:
                        if (
                            not (
                                self._combat_is_farm_task()
                                or self._combat_burnout_enabled()
                            )
                            and fast_combat_completed_count
                            >= self._combat_repeat_count()
                        ):
                            logger.info(
                                "Combat: fixed combat count completed by fast combat"
                            )
                        else:
                            logger.info(
                                "Combat: insufficient stamina for pet repeat combat"
                            )
                    else:
                        use_max_repeat = (
                            self._combat_is_farm_task()
                            or self._combat_burnout_enabled()
                        )
                        success = self._prepare_repeat_combat(
                            use_max=use_max_repeat,
                            affordable_count=affordable_count,
                            completed_count=fast_combat_completed_count,
                            skip_first_screenshot=True,
                        )
                        if success:
                            success = self._run_repeat_combat(
                                skip_first_screenshot=True
                            )
                            repeat_combat_started = success

        if success and (not repeat_combat_started or self._uses_server_repeat_combat()):
            success = self._leave_to_main(skip_first_screenshot=True)

        logger.attr("CombatRepeatCombatStarted", repeat_combat_started)

        if success:
            should_schedule_reward = (
                self._combat_should_call_mission_reward()
                and self._should_schedule_mission_reward(
                    completed_sessions,
                    runtime_active=repeat_combat_started,
                )
            )
            if should_schedule_reward:
                if should_schedule_mission_reward(self.config):
                    self.config.task_call("MissionReward", force_call=False)
                if should_schedule_after_battle(self.config):
                    self.config.task_call("SpecialActivity", force_call=False)
            if repeat_combat_started:
                self._combat_runtime_set(self._combat_runtime_build())
                self._delay_running_repeat_combat()
            else:
                self._combat_runtime_clear()
                self._combat_delay_after_settled()
            return True

        self._combat_runtime_clear()
        self._leave_to_main(skip_first_screenshot=True)
        should_schedule_reward = (
            self._combat_should_call_mission_reward()
            and self._should_schedule_mission_reward(
                completed_sessions,
                runtime_active=self._combat_runtime_active(),
            )
        )
        if should_schedule_reward:
            if should_schedule_mission_reward(self.config):
                self.config.task_call("MissionReward", force_call=False)
            if should_schedule_after_battle(self.config):
                self.config.task_call("SpecialActivity", force_call=False)
        self.config.task_delay(success=False)
        return False
