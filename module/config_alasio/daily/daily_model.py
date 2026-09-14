import typing as t

import alasio.config.alasio.group_export as a
import msgspec as m
import typing_extensions as e


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m module.config.gen ```

class MissionReward(a.GroupBase):
    ClaimDailyRewards: bool = True


class Mail(a.GroupBase):
    CollectWithin: t.Literal['1h', '1d'] = '1d'
    LastCheckAt: a.T_DATETIME = a.DEFAULT_TIME
    LastRemainingText: str = ''
    LastRemainingSince: a.T_DATETIME = a.DEFAULT_TIME


class CommunityAio(a.GroupBase):
    CredentialsFile: str = ''


class Knights(a.GroupBase):
    ClaimSigninRateReward: bool = True
    Support: bool = True
    SupportLowerLevelFairyFlower: bool = True
    SupportBeginnerPenguin: bool = True
    RequestItem: t.Literal['LowerLevelFairyFlower', 'BeginnerPenguin'] = 'LowerLevelFairyFlower'
    WeeklyTask: bool = True
    WorldBoss: bool = True


class KnightsTeamBattle(a.GroupBase):
    TeamBattle: bool = True
    Reminder: bool = False
    ReminderLeadMinutes: t.Literal[30, 60, 120, 180] = 60
    ReminderLastEnd: str = ''


class Arena(a.GroupBase):
    BurnoutMode: t.Literal['Daily', 'Burnout'] = 'Daily'
    ClaimWeeklyRewards: bool = True
    ClaimWeeklyBattleRewards: bool = True
    ClaimBattlePassRewards: bool = True
    NPCCombat: bool = True
    NPCCombatFastBattle: bool = True
    NPCCombatCount: int = 5


class StoreDaily(a.GroupBase):
    BuyDailyFreeItem: bool = True
    BuyFriendshipMobility40: bool = True
    BuyFriendshipArenaFlag: bool = True
    BuyConquestMobility40: t.Literal[0, 1, 2, 3] = 0


class StoreWeekly(a.GroupBase):
    BuyConquestMorogora: bool = False
    BuyFriendshipArtifactEnhancementStone: t.Literal[0, 1, 2, 3] = 0
    BuyInheritanceMorogora: t.Literal[0, 1, 2] = 0
    BuyInheritancePotentialFragments: t.Literal[0, 1, 2] = 0
    BuyInheritanceEquipmentReforgingStoneSelectionChest: bool = False
    BuyInheritanceLesserArtifactCharm: t.Literal[0, 1, 2, 3] = 0
    BuyInheritanceGreaterArtifactCharm: bool = False
    BuyInheritanceLesserAccessoryCharm: t.Literal[0, 1, 2, 3] = 0
    BuyInheritanceGreaterAccessoryCharm: bool = False


class PetsGift(a.GroupBase):
    LastClaimAt: a.T_DATETIME = a.DEFAULT_TIME


class Combat(a.GroupBase):
    BurnoutMode: t.Literal['Daily', 'Burnout'] = 'Daily'
    Domain: t.Literal['Hunt', 'SpiritAltar', 'Saint37', 'Episode4'] = 'Hunt'
    Element: t.Literal['Dark', 'Light', 'Water', 'Fire', 'Nature'] = 'Water'
    AltarGrade: t.Literal['Pri', 'Mid', 'High', 'Hell'] = 'Hell'
    HuntGrade: t.Literal['Mid', 'High', 'Hell', 'Dimensional'] = 'Hell'
    Episode4Material: t.Literal[
        'CATALYST_RARE_BENEVOLENT', 'CATALYST_RARE_SECRET', 'CATALYST_RARE_FIGHTING_SPIRIT', 'CATALYST_RARE_SNIPER',
        'HEART_OF_THE_WOODS', 'CATALYST_RARE_OATH', 'CATALYST_RARE_MYSTERIOUS', 'CATALYST_EPIC_OATH', 'BREATH_OF_KARMA',
        'CATALYST_EPIC_SNIPER', 'CATALYST_EPIC_FIGHTING_SPIRIT', 'FROZEN_SEED', 'CATALYST_EPIC_BENEVOLENT',
        'CATALYST_EPIC_MYSTERIOUS', 'CATALYST_EPIC_SECRET', 'TRACES_OF_BRILLIANCE',
    ] = 'BREATH_OF_KARMA'
    FastCombat: bool = True
    FastCombatCount: int = 10
    RepeatCombatCount: int = 5
    RepeatCombatLeifCount: int = 1
    RepeatCombatPrioritizeStamina: bool = True
    RepeatCombatGearMode: t.Literal['Sell', 'Extract'] = 'Extract'
    RepeatCombatEquipmentScore: t.Literal[22, 24, 26, 28, 30, 32] = 28
    RepeatCombatHeroSpeedFilter: bool = True
    RepeatCombatHeroSpeed: t.Literal[2, 3, 4] = 4
    RepeatCombatLegendarySpeedFilter: bool = True
    RepeatCombatLegendarySpeed: t.Literal[2, 3, 4, 5] = 4
    RepeatCombatFlatMainStatExceptSpeed: bool = True
    RepeatCombatExcludeOtherworldLegendary: bool = False
    Saint37AutoRecycle: bool = False


class UrgentTasks(a.GroupBase):
    Enable: bool = True
    Difficulty: t.Literal['Normal', 'Superior'] = 'Superior'
    LastCheckAt: a.T_DATETIME = a.DEFAULT_TIME


class CombatFarmCombat(Combat):
    FastCombat: bool = False
    FastCombatCount: t.Literal[10] = 10


class CombatRuntime(a.GroupBase):
    Session: str = '{}'


class SecretShop(a.GroupBase):
    OnlyFree: bool = True
    MaxRefresh: int = 10
    BuyCovenantBookmark: bool = True
    BuyMysticMedal: bool = True


class Gacha(a.GroupBase):
    CollectGoldenInheritance: bool = True


class GachaResult(a.GroupBase):
    SaveScreenshot: bool = True
    OcrResult: bool = False


class SpecialActivity(a.GroupBase):
    GetDailyReward: bool = True
    GetTaskReward: bool = True
    GetFreeGacha: bool = True
    GetEnergyDrink: bool = True


class ActivityRuntime(a.GroupBase):
    TaskRewardClaimedAt: a.T_DATETIME = a.DEFAULT_TIME
    FreeGacha20CheckedAt: a.T_DATETIME = a.DEFAULT_TIME


class Dashboard(a.GroupBase):
    Gold: str = '{}'
    Skystone: str = '{}'
    Stamina: str = '{}'
    EquipmentInventory: str = '{}'
    DailyActivity: str = '{}'
    ArenaRank: str = '{}'
    ArenaFlag: str = '{}'
    ConquestPoint: str = '{}'
    ShadowCommission: str = '{}'
    TeamBattle: str = '{}'
