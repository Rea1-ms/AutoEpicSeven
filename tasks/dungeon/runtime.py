from module.base.timer import Timer
from module.logger import logger
from tasks.base.assets.assets_base_page import BACK


BACKGROUND_REPEAT_COMBAT_RUNTIME_PATHS = (
    "Combat.CombatRuntime.Session.active",
    "CombatFarm.CombatRuntime.Session.active",
)


def is_background_repeat_combat_active(config) -> bool:
    """
    Return whether any dungeon task owns an active background run.

    The game does not allow another fast battle while a background repeat
    combat session is active, so all later battles must use normal combat.
    """
    return any(config.cross_get(path, default=False) for path in BACKGROUND_REPEAT_COMBAT_RUNTIME_PATHS)


def background_repeat_combat_requires_game_client(config) -> bool:
    """
    Return whether an active background run still depends on the game client.

    Server-managed repeat combat continues after the CN or global client goes
    offline. Legacy client-managed sessions, including sessions without a
    recognized mode, must keep the game alive so an upgrade or malformed
    runtime record cannot silently interrupt an active battle.
    """
    for active_path in BACKGROUND_REPEAT_COMBAT_RUNTIME_PATHS:
        if not config.cross_get(active_path, default=False):
            continue
        mode_path = active_path.removesuffix(".active") + ".mode"
        if config.cross_get(mode_path, default=None) != "repeat_server":
            return True
    return False


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
            "element": (
                None
                if domain in ("Saint37", "Episode4", "UrgentTasks")
                else self._combat_element()
            ),
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

        state = self._detect_background_repeat_combat_state()
        if state is None:
            return False

        logger.info(f"Combat: detected existing background repeat combat before task start ({state})")
        session = self._combat_runtime_build_detected_existing()
        session["state"] = state
        self._combat_runtime_set(session)
        return True

    def _handle_repeat_combat_finish_return(self) -> bool:
        """
        Unwind local dungeon pages back to main after consuming a result.

        This path only matters for the "adopt existing result" startup case:
        - an old background repeat combat finishes
        - user manually stays on a combat-local page
        - a new Combat task starts and detects `state=result`
        - result cleanup succeeds, but we are still sitting somewhere inside
          the dungeon flow instead of already being on main

        Without this extra unwind step, the watch loop would keep waiting for
        `is_in_main()` to become true on its own and eventually time out.
        """
        if not self._is_in_dungeon_context():
            return False

        if self.appear_then_click(BACK, interval=1):
            logger.info("Combat: return to main after repeat combat result")
            return True

        return False

    def _leave_to_main(self, skip_first_screenshot=True) -> bool:
        """
        Recover back to main from dungeon-local pages.

        This is kept separate from generic page routing on purpose. Dungeon
        tasks can leave on transient local states such as stage boards, prepare
        pages, side story sub-pages, or result windows that are not stable
        routing nodes. A generic ui_goto(page_main) is fine on clean success
        paths, but failure cleanup still needs a dungeon-aware unwind helper.

        Why unwind one layer at a time:
        - dungeon entry is not fixed; different combat branches may leave us on
          different local pages, but they still converge back into the same
          closed BACK chain toward prepare / stage / main
        - some transitions are "swallowed" locally, for example side-story
          supporter -> choose team -> prepare. Once prepare is open, BACK no
          longer returns to supporter directly, so local unwind is more
          reliable than trying to globally route from an inferred old page
        - in short: not every dungeon-local page is suitable for global route
          planning, but the BACK-based unwind path is closed and deterministic

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
                or self._is_urgent_tasks_detail_page()
                or self._is_episode_choose_page()
                or self._is_episode_stage_page()
                or self._is_episode_supporter_page()
            ):
                if self.appear_then_click(BACK, interval=1):
                    timeout.reset()
                    continue
