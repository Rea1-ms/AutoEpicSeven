from module.config.stored.classes import (
    StoredInt,
    StoredStamina,
    StoredCounter,
    StoredDailyActivity,
    StoredArenaRank,
    StoredArenaFlag,
    StoredShadowCommission,
    StoredTeamBattleStatus,
)


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m module/config/config_updater.py ```

class StoredGenerated:
    Gold = StoredInt("DataUpdate.Dashboard.Gold")
    Skystone = StoredInt("DataUpdate.Dashboard.Skystone")
    Stamina = StoredStamina("DataUpdate.Dashboard.Stamina")
    EquipmentInventory = StoredCounter("DataUpdate.Dashboard.EquipmentInventory")
    DailyActivity = StoredDailyActivity("DataUpdate.Dashboard.DailyActivity")
    ArenaRank = StoredArenaRank("DataUpdate.Dashboard.ArenaRank")
    ArenaFlag = StoredArenaFlag("DataUpdate.Dashboard.ArenaFlag")
    ConquestPoint = StoredInt("DataUpdate.Dashboard.ConquestPoint")
    ShadowCommission = StoredShadowCommission("DataUpdate.Dashboard.ShadowCommission")
    TeamBattle = StoredTeamBattleStatus("DataUpdate.Dashboard.TeamBattle")
