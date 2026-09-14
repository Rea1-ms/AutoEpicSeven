import typing as t

from alasio.config.base import AlasioConfigBase

from ..const import entry

if t.TYPE_CHECKING:
    from ..aes import aes_model as aes
    from ..daily import daily_model as daily
    from ..monthly import monthly_model as monthly
    from ..tool import tool_model as tool


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m module.config.gen ```

class ConfigGenerated(AlasioConfigBase):
    """
    A generated config struct to fool IDE's type-predict and auto-complete
    """
    entry = entry

    """
    ========== nav: aes ==========
    """
    # ----- Alas -----
    Game: "aes.Game"

    # ----- Restart -----
    # Scheduler: "alasio.SchedulerUedit"

    """
    ========== nav: daily ==========
    """
    # ----- MissionReward -----
    # Scheduler: "alasio.SchedulerUedit"
    MissionReward: "daily.MissionReward"

    # ----- Mail -----
    # Scheduler: "alasio.SchedulerUedit"
    Mail: "daily.Mail"

    # ----- CommunityAio -----
    # Scheduler: "alasio.SchedulerUedit"
    CommunityAio: "daily.CommunityAio"

    # ----- DataUpdate -----
    # Scheduler: "alasio.SchedulerUedit"
    Dashboard: "daily.Dashboard"

    # ----- SanctuaryDaily -----
    # Scheduler: "alasio.SchedulerUedit"

    # ----- Knights -----
    # Scheduler: "alasio.SchedulerUedit"
    Knights: "daily.Knights"
    KnightsTeamBattle: "daily.KnightsTeamBattle"

    # ----- Arena -----
    # Scheduler: "alasio.SchedulerUedit"
    Arena: "daily.Arena"

    # ----- Store -----
    # Scheduler: "alasio.SchedulerUedit"
    StoreDaily: "daily.StoreDaily"
    StoreWeekly: "daily.StoreWeekly"

    # ----- PetsGift -----
    # Scheduler: "alasio.SchedulerUedit"
    PetsGift: "daily.PetsGift"

    # ----- SecretShop -----
    # Scheduler: "alasio.SchedulerUedit"
    SecretShop: "daily.SecretShop"

    # ----- Combat -----
    # Scheduler: "alasio.SchedulerUedit"
    Combat: "daily.Combat"
    UrgentTasks: "daily.UrgentTasks"
    CombatRuntime: "daily.CombatRuntime"

    # ----- Gacha -----
    # Scheduler: "alasio.SchedulerUedit"
    Gacha: "daily.Gacha"
    GachaResult: "daily.GachaResult"

    # ----- Pets -----
    # Scheduler: "alasio.SchedulerUedit"

    # ----- SpecialActivity -----
    # Scheduler: "alasio.SchedulerUedit"
    SpecialActivity: "daily.SpecialActivity"
    # GachaResult: "daily.GachaResult"
    ActivityRuntime: "daily.ActivityRuntime"

    """
    ========== nav: monthly ==========
    """
    # ----- SanctuaryMonthly -----
    # Scheduler: "alasio.SchedulerUedit"
    SanctuaryMonthly: "monthly.SanctuaryMonthly"

    """
    ========== nav: tool ==========
    """
    # ----- CombatFarm -----
    # Scheduler: "alasio.SchedulerUedit"
    # Combat: "daily.CombatFarmCombat"
    # CombatRuntime: "daily.CombatRuntime"

    # ----- CommunityAuth -----
    # Scheduler: "alasio.SchedulerUedit"
    CommunityAuth: "tool.CommunityAuth"
