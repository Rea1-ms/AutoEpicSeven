import typing as t

from alasio.config.base import AlasioConfigBase

from ..const import entry

if t.TYPE_CHECKING:
    from ..aes import aes_model as aes
    from ..daily import daily_model as daily
    from ..dashboard import dashboard_model as dashboard
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
    # Scheduler: "aes.AesScheduler"

    """
    ========== nav: daily ==========
    """
    # ----- MissionReward -----
    # Scheduler: "aes.AesScheduler"
    MissionReward: "daily.MissionReward"

    # ----- Mail -----
    # Scheduler: "aes.AesScheduler"
    Mail: "daily.Mail"

    # ----- CommunityAio -----
    # Scheduler: "aes.AesScheduler"
    CommunityAio: "daily.CommunityAio"

    # ----- DataUpdate -----
    # Scheduler: "aes.AesScheduler"

    # ----- SanctuaryDaily -----
    # Scheduler: "aes.AesScheduler"

    # ----- Knights -----
    # Scheduler: "aes.AesScheduler"
    Knights: "daily.Knights"
    KnightsTeamBattle: "daily.KnightsTeamBattle"

    # ----- Arena -----
    # Scheduler: "aes.AesScheduler"
    Arena: "daily.Arena"

    # ----- Store -----
    # Scheduler: "aes.AesScheduler"
    StoreDaily: "daily.StoreDaily"
    StoreWeekly: "daily.StoreWeekly"

    # ----- PetsGift -----
    # Scheduler: "aes.AesScheduler"
    PetsGift: "daily.PetsGift"

    # ----- SecretShop -----
    # Scheduler: "aes.AesScheduler"
    SecretShop: "daily.SecretShop"

    # ----- Combat -----
    # Scheduler: "aes.AesScheduler"
    Combat: "daily.Combat"
    UrgentTasks: "daily.UrgentTasks"
    CombatRuntime: "daily.CombatRuntime"

    # ----- Gacha -----
    # Scheduler: "aes.AesScheduler"
    Gacha: "daily.Gacha"
    GachaResult: "daily.GachaResult"

    # ----- Pets -----
    # Scheduler: "aes.AesScheduler"

    # ----- SpecialActivity -----
    # Scheduler: "aes.AesScheduler"
    SpecialActivity: "daily.SpecialActivity"
    # GachaResult: "daily.GachaResult"
    ActivityRuntime: "daily.ActivityRuntime"

    """
    ========== nav: monthly ==========
    """
    # ----- SanctuaryMonthly -----
    # Scheduler: "aes.AesScheduler"
    SanctuaryMonthly: "monthly.SanctuaryMonthly"

    """
    ========== nav: tool ==========
    """
    # ----- CombatFarm -----
    # Scheduler: "aes.AesScheduler"
    # Combat: "daily.CombatFarmCombat"
    # CombatRuntime: "daily.CombatRuntime"

    # ----- CommunityAuth -----
    # Scheduler: "aes.AesScheduler"
    CommunityAuth: "tool.CommunityAuth"

    """
    ========== nav: device ==========
    """

    """
    ========== nav: dashboard ==========
    """
    # ----- Dashboard -----
    Gold: "dashboard.Gold"
    Skystone: "dashboard.Skystone"
    Stamina: "dashboard.Stamina"
    EquipmentInventory: "dashboard.EquipmentInventory"
    DailyActivity: "dashboard.DailyActivity"
    ArenaRank: "dashboard.ArenaRank"
    ArenaFlag: "dashboard.ArenaFlag"
    ConquestPoint: "dashboard.ConquestPoint"
    ShadowCommission: "dashboard.ShadowCommission"
    TeamBattle: "dashboard.TeamBattle"
