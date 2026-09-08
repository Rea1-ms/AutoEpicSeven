from module.logger import logger
from tasks.activity.scheduling import should_schedule_after_battle
from tasks.arena.burnout import ArenaBurnoutMixin
from tasks.arena.dashboard import ArenaDashboardMixin
from tasks.arena.entry import ArenaEntryMixin, is_arena_settling_period
from tasks.arena.npc_combat import ArenaNpcCombatMixin, OcrFastBattleTimes
from tasks.arena.rewards import ArenaRewardsMixin, next_battle_pass_recheck
from tasks.base.ui import UI
from tasks.mission_reward.scheduling import should_schedule_mission_reward

__all__ = ["Arena", "OcrFastBattleTimes", "next_battle_pass_recheck"]


class Arena(
    ArenaNpcCombatMixin,
    ArenaRewardsMixin,
    ArenaBurnoutMixin,
    ArenaEntryMixin,
    ArenaDashboardMixin,
    UI,
):
    """Run arena entry, dashboard, rewards, NPC combat, and scheduling."""

    @staticmethod
    def _should_schedule_mission_reward_after_npc(rounds_completed: int) -> bool:
        return rounds_completed > 0

    def run(self) -> bool:
        logger.hr("Arena", level=1)

        if not self.device.app_is_running():
            from tasks.login.login import Login

            Login(self.config, device=self.device).app_start()

        if not hasattr(self.device, "image") or self.device.image is None:
            self.device.screenshot()

        status = None

        # Fast-path: if task starts inside arena/NPC context, do not force return to main page.
        # This keeps manual "already in arena" starts seamless while preserving
        # the settlement capability flag used to suppress unavailable rewards.
        if getattr(self.config, "Arena_NPCCombat", False):
            if self._is_npc_combat_context():
                logger.info("Arena: detected NPC combat context, skip goto main")
                status = "settling_npc" if is_arena_settling_period() else "entered"

        if status is None:
            status = self.arena_goto(skip_first_screenshot=True)

        if status in {"entered", "settling_npc"}:
            self._update_arena_dashboard_snapshot(skip_first_screenshot=True)
            if getattr(self.config, "Arena_NPCCombat", False):
                if not self._run_npc_combat(skip_first_screenshot=True):
                    self.config.task_delay(success=False)
                    return False

                if status == "entered":
                    self._claim_weekly_battle_rewards(skip_first_screenshot=True)
                    self._claim_battle_pass_rewards(skip_first_screenshot=True)

                battle_completed = self._should_schedule_mission_reward_after_npc(
                    getattr(self, "_arena_npc_completed_rounds", 0)
                )
                if battle_completed:
                    if should_schedule_mission_reward(self.config):
                        self.config.task_call("MissionReward", force_call=False)
                    if should_schedule_after_battle(self.config):
                        self.config.task_call("SpecialActivity", force_call=False)

            self.config.task_call("DataUpdate", force_call=False)
            self._arena_delay_after_run()
            return True

        self.config.task_delay(success=False)
        return False
