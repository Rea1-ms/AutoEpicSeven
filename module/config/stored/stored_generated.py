from module.config.stored.classes import (
    StoredAssignment,
    StoredBase,
    StoredBattlePassLevel,
    StoredBattlePassQuestCalyx,
    StoredBattlePassQuestCavernOfCorrosion,
    StoredBattlePassQuestCredits,
    StoredBattlePassQuestEchoOfWar,
    StoredBattlePassQuestStagnantShadow,
    StoredBattlePassQuestSynthesizeConsumables,
    StoredBattlePassQuestTrailblazePower,
    StoredBattlePassSimulatedUniverse,
    StoredBattlePassWeeklyQuest,
    StoredCounter,
    StoredDaily,
    StoredDailyActivity,
    StoredDungeonDouble,
    StoredE7ArenaRank,
    StoredE7DailyActivity,
    StoredE7ShadowCommission,
    StoredE7TeamBattleStatus,
    StoredE7WeeklyActivity,
    StoredEchoOfWar,
    StoredExpiredAt0400,
    StoredExpiredAtMonday0400,
    StoredImmersifier,
    StoredInt,
    StoredPlanner,
    StoredPlannerOverall,
    StoredRelic,
    StoredResersed,
    StoredSimulatedUniverse,
    StoredSimulatedUniverseElite,
    StoredTrailblazePower,
)


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m module/config/config_updater.py ```

class StoredGenerated:
    Credit = StoredInt("DataUpdate.ItemStorage.Credit")
    StallerJade = StoredInt("DataUpdate.ItemStorage.StallerJade")
    E7Gold = StoredInt("DataUpdate.E7Dashboard.E7Gold")
    E7Skystone = StoredInt("DataUpdate.E7Dashboard.E7Skystone")
    E7Stamina = StoredCounter("DataUpdate.E7Dashboard.E7Stamina")
    E7EquipmentInventory = StoredCounter("DataUpdate.E7Dashboard.E7EquipmentInventory")
    E7DailyActivity = StoredE7DailyActivity("DataUpdate.E7Dashboard.E7DailyActivity")
    E7WeeklyActivity = StoredE7WeeklyActivity("DataUpdate.E7Dashboard.E7WeeklyActivity")
    E7ArenaRank = StoredE7ArenaRank("DataUpdate.E7Dashboard.E7ArenaRank")
    E7ShadowCommission = StoredE7ShadowCommission("DataUpdate.E7Dashboard.E7ShadowCommission")
    E7TeamBattle = StoredE7TeamBattleStatus("DataUpdate.E7Dashboard.E7TeamBattle")
    E7ArenaFlag = StoredCounter("DataUpdate.E7Dashboard.E7ArenaFlag")
    E7ConquestPoint = StoredInt("DataUpdate.E7Dashboard.E7ConquestPoint")
    CloudRemainSeasonPass = StoredInt("DataUpdate.CloudStorage.CloudRemainSeasonPass")
    CloudRemainPaid = StoredInt("DataUpdate.CloudStorage.CloudRemainPaid")
    CloudRemainFree = StoredInt("DataUpdate.CloudStorage.CloudRemainFree")
