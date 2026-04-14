from module.config.stored.classes import (
    StoredInt,
    StoredCounter,
    StoredDailyActivity,
    StoredWeeklyActivity,
    StoredArenaRank,
    StoredShadowCommission,
    StoredTeamBattleStatus,
)


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m module/config/config_updater.py ```

class StoredGenerated:
    Gold = StoredInt("DataUpdate.Dashboard.Gold")
    Skystone = StoredInt("DataUpdate.Dashboard.Skystone")
    Stamina = StoredCounter("DataUpdate.Dashboard.Stamina")
    EquipmentInventory = StoredCounter("DataUpdate.Dashboard.EquipmentInventory")
    DailyActivity = StoredDailyActivity("DataUpdate.Dashboard.DailyActivity")
    WeeklyActivity = StoredWeeklyActivity("DataUpdate.Dashboard.WeeklyActivity")
    ArenaRank = StoredArenaRank("DataUpdate.Dashboard.ArenaRank")
    ArenaFlag = StoredCounter("DataUpdate.Dashboard.ArenaFlag")
    ConquestPoint = StoredInt("DataUpdate.Dashboard.ConquestPoint")
    ShadowCommission = StoredShadowCommission("DataUpdate.Dashboard.ShadowCommission")
    TeamBattle = StoredTeamBattleStatus("DataUpdate.Dashboard.TeamBattle")
