"""In-memory configuration and explicit daily observations, never an account."""

from contextlib import ExitStack
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.config import server
from module.config.deep import deep_get, deep_set
from tasks.mission_reward.scheduling import DAILY_PLAN_PATH, daily_snapshot_context
from tests.support.daily_missions import EXPECTED

DAY = datetime.fromisoformat("2026-10-17T03:00:00")


class DailyPlanningCase:
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        previous = server.lang
        self.addCleanup(server.set_lang, previous)
        server.set_lang("cn")
        self.day = DAY
        for module in ("planning", "scheduling"):
            self.stack.enter_context(patch(
                f"tasks.mission_reward.{module}.get_server_last_update",
                side_effect=lambda *args: self.day,
            ))
        self.stack.enter_context(patch(
            "tasks.mission_reward.planning.get_server_next_update",
            side_effect=lambda *args: self.day + timedelta(days=1),
        ))
        self.data = {}
        self.config = SimpleNamespace(
            Emulator_PackageName="com.zlongame.cn.epicseven",
            Emulator_GameLanguage="cn", Scheduler_ServerUpdate="03:00",
            task=SimpleNamespace(command="Combat"),
            Arena_NPCCombat=True, Arena_NPCCombatCount=5, Arena_BurnoutMode="Daily",
            Combat_FastCombat=True, Combat_FastCombatCount=5,
            Combat_RepeatCombatLeifCount=1, Combat_RepeatCombatPrioritizeStamina=False,
            Combat_BurnoutMode="Daily", Combat_Domain="Hunt", Combat_HuntBoss="Wyvern",
            Combat_HuntGrade="Hell",
            cross_get=lambda key, default=None: deep_get(self.data, key, default),
            cross_set=lambda key, value: deep_set(self.data, key, value),
            is_task_enabled=lambda name: deep_get(self.data, f"{name}.Scheduler.Enable", False),
            task_call=Mock(), task_delay=Mock(),
        )
        for task in ("MissionReward", "Arena", "Combat"):
            self.set(f"{task}.Scheduler.Enable", True)
            self.set(f"{task}.Scheduler.NextRun", DAY + timedelta(hours=1))
        self.set("Arena.Arena.NPCCombatCount", 5)
        self.set("Combat.Combat.FastCombatCount", 5)
        self.snapshot()

    def set(self, path, value):
        deep_set(self.data, path, value)

    def snapshot(self, points=55, arena=0, stamina=0, *, claimed=True):
        rows = []
        for key, (current, total, reward) in EXPECTED.items():
            if key.startswith("arena_"):
                current = min(arena, total)
            elif key.startswith("stamina_"):
                current = min(stamina, total)
            elif key in ("gacha", "flower"):
                current = 1
            rows.append({"key": key, "current": current, "total": total, "points": reward})
        self.plan = {
            **daily_snapshot_context(self.config), "day": self.day.isoformat(),
            "points": points, "rewards_claimed": claimed, "missions": rows,
        }
        self.set(DAILY_PLAN_PATH, self.plan)
        return self.plan
