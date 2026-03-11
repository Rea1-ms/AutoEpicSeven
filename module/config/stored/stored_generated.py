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
    DailyActivity = StoredDailyActivity("DailyQuest.DailyStorage.DailyActivity")
    DailyQuest = StoredDaily("DailyQuest.DailyStorage.DailyQuest")
    BattlePassLevel = StoredBattlePassLevel("BattlePass.BattlePassStorage.BattlePassLevel")
    BattlePassWeeklyQuest = StoredBattlePassWeeklyQuest("BattlePass.BattlePassStorage.BattlePassWeeklyQuest")
    BattlePassSimulatedUniverse = StoredBattlePassSimulatedUniverse("BattlePass.BattlePassStorage.BattlePassSimulatedUniverse")
    BattlePassQuestCalyx = StoredBattlePassQuestCalyx("BattlePass.BattlePassStorage.BattlePassQuestCalyx")
    BattlePassQuestEchoOfWar = StoredBattlePassQuestEchoOfWar("BattlePass.BattlePassStorage.BattlePassQuestEchoOfWar")
    BattlePassQuestCredits = StoredBattlePassQuestCredits("BattlePass.BattlePassStorage.BattlePassQuestCredits")
    BattlePassQuestSynthesizeConsumables = StoredBattlePassQuestSynthesizeConsumables("BattlePass.BattlePassStorage.BattlePassQuestSynthesizeConsumables")
    BattlePassQuestStagnantShadow = StoredBattlePassQuestStagnantShadow("BattlePass.BattlePassStorage.BattlePassQuestStagnantShadow")
    BattlePassQuestCavernOfCorrosion = StoredBattlePassQuestCavernOfCorrosion("BattlePass.BattlePassStorage.BattlePassQuestCavernOfCorrosion")
    BattlePassQuestTrailblazePower = StoredBattlePassQuestTrailblazePower("BattlePass.BattlePassStorage.BattlePassQuestTrailblazePower")
    Assignment = StoredAssignment("Assignment.Assignment.Assignment")
    Credit = StoredInt("DataUpdate.ItemStorage.Credit")
    StallerJade = StoredInt("DataUpdate.ItemStorage.StallerJade")
    CloudRemainSeasonPass = StoredInt("DataUpdate.CloudStorage.CloudRemainSeasonPass")
    CloudRemainPaid = StoredInt("DataUpdate.CloudStorage.CloudRemainPaid")
    CloudRemainFree = StoredInt("DataUpdate.CloudStorage.CloudRemainFree")
    SimulatedUniverseFarm = StoredSimulatedUniverseElite("Rogue.RogueWorld.SimulatedUniverseFarm")
