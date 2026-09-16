from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from module.config.deep import deep_get


EXECUTION_MODE_DAILY = 'Daily'
EXECUTION_MODE_BURNOUT = 'Burnout'

_SCHEDULER_TASKS = (
    'Restart',
    'MissionReward',
    'Mail',
    'CommunityAio',
    'DataUpdate',
    'SanctuaryDaily',
    'Knights',
    'Arena',
    'Store',
    'PetsGift',
    'SecretShop',
    'Combat',
    'Gacha',
    'Pets',
    'SpecialActivity',
    'SanctuaryMonthly',
    'CombatFarm',
    'CommunityAuth',
)

# These values remain readable and writable by tasks, but the legacy GUI never
# exposed them. Device fields intentionally stay visible because the dedicated
# Device page uses them as user-facing connection and recovery settings.
STATIC_HIDE_TARGETS = frozenset({
    *(f'{task}.Scheduler.ServerUpdate' for task in _SCHEDULER_TASKS),
    'Mail.Mail.LastCheckAt',
    'Mail.Mail.LastRemainingText',
    'Mail.Mail.LastRemainingSince',
    'CommunityAio.CommunityAio.CredentialsFile',
    'Knights.KnightsTeamBattle.ReminderLastEnd',
    'PetsGift.PetsGift.LastClaimAt',
    'Combat.UrgentTasks.LastCheckAt',
    'Combat.CombatRuntime.Session',
    'CombatFarm.CombatRuntime.Session',
    'CommunityAuth.CommunityAuth.CredentialsFile',
    'SpecialActivity.ActivityRuntime.TaskRewardClaimedAt',
    'SpecialActivity.ActivityRuntime.FreeGacha20CheckedAt',
})

_CONDITIONAL_HIDE_TARGETS = frozenset({
    'Knights.KnightsTeamBattle.ReminderLeadMinutes',
    'Knights.Knights.RequestItem',
    'Arena.Arena.NPCCombatFastBattle',
    'Arena.Arena.NPCCombatCount',
    'Arena.Arena.BurnoutMode',
    'SecretShop.SecretShop.MaxRefresh',
    'Combat.Combat.Episode4Material',
    'Combat.Combat.FastCombat',
    'Combat.Combat.FastCombatCount',
    'Combat.Combat.RepeatCombatCount',
    'Combat.Combat.RepeatCombatHeroSpeed',
    'Combat.Combat.RepeatCombatLegendarySpeed',
    'Combat.Combat.RepeatCombatLeifCount',
    'Combat.Combat.RepeatCombatPrioritizeStamina',
    'Combat.Combat.Saint37AutoRecycle',
    'Combat.Combat.BurnoutMode',
    'Combat.Combat.Element',
    'Combat.Combat.AltarGrade',
    'Combat.Combat.HuntGrade',
    'Combat.UrgentTasks.Difficulty',
})

# Alasio asks for one target collection for both permanent and conditional
# hiding. Only the conditional dependencies below can trigger a live refresh.
DYNAMIC_HIDE_TARGETS = STATIC_HIDE_TARGETS | _CONDITIONAL_HIDE_TARGETS

DYNAMIC_HIDE_DEPENDENCIES = frozenset({
    'Knights.KnightsTeamBattle.Reminder',
    'Knights.Knights.Support',
    'Arena.Arena.NPCCombat',
    'Arena.Arena.BurnoutMode',
    'SecretShop.SecretShop.OnlyFree',
    'Combat.Combat.Domain',
    'Combat.Combat.HuntGrade',
    'Combat.Combat.FastCombat',
    'Combat.Combat.RepeatCombatHeroSpeedFilter',
    'Combat.Combat.RepeatCombatLegendarySpeedFilter',
    'Combat.Combat.BurnoutMode',
    'Combat.UrgentTasks.Enable',
})


def normalize_execution_mode(value: Any) -> str:
    """Convert legacy burnout booleans to the execution-mode select value."""
    if value is True or value == EXECUTION_MODE_BURNOUT:
        return EXECUTION_MODE_BURNOUT
    return EXECUTION_MODE_DAILY


def iter_hidden_args(data: dict[str, Any]) -> Iterator[str]:
    """Yield every argument hidden by the legacy AES configuration rules."""
    yield from STATIC_HIDE_TARGETS

    if deep_get(data, 'Knights.KnightsTeamBattle.Reminder', default=False) is False:
        yield 'Knights.KnightsTeamBattle.ReminderLeadMinutes'
    if deep_get(data, 'Knights.Knights.Support', default=True) is False:
        yield 'Knights.Knights.RequestItem'

    if deep_get(data, 'Arena.Arena.NPCCombat', default=False) is False:
        yield 'Arena.Arena.NPCCombatFastBattle'
        yield 'Arena.Arena.NPCCombatCount'
        # Burnout mode reruns arena when flags refill; without NPC combat
        # nothing consumes flags, so the option is meaningless.
        yield 'Arena.Arena.BurnoutMode'
    elif normalize_execution_mode(
        deep_get(data, 'Arena.Arena.BurnoutMode', default=EXECUTION_MODE_DAILY)
    ) == EXECUTION_MODE_BURNOUT:
        yield 'Arena.Arena.NPCCombatCount'

    if deep_get(data, 'SecretShop.SecretShop.OnlyFree', default=True) is True:
        yield 'SecretShop.SecretShop.MaxRefresh'

    task_prefix = 'Combat.Combat'
    combat_domain = deep_get(data, f'{task_prefix}.Domain', default='Hunt')
    combat_hunt_grade = deep_get(data, f'{task_prefix}.HuntGrade', default='Hell')

    if combat_domain != 'Episode4':
        yield f'{task_prefix}.Episode4Material'

    if combat_domain == 'Saint37':
        yield f'{task_prefix}.FastCombat'
        yield f'{task_prefix}.FastCombatCount'
    elif combat_domain == 'Hunt' and combat_hunt_grade == 'Dimensional':
        yield f'{task_prefix}.FastCombat'
        yield f'{task_prefix}.FastCombatCount'
    elif deep_get(data, f'{task_prefix}.FastCombat', default=True) is False:
        yield f'{task_prefix}.FastCombatCount'

    if deep_get(
        data,
        f'{task_prefix}.RepeatCombatHeroSpeedFilter',
        default=True,
    ) is False:
        yield f'{task_prefix}.RepeatCombatHeroSpeed'
    if deep_get(
        data,
        f'{task_prefix}.RepeatCombatLegendarySpeedFilter',
        default=True,
    ) is False:
        yield f'{task_prefix}.RepeatCombatLegendarySpeed'

    if normalize_execution_mode(
        deep_get(data, f'{task_prefix}.BurnoutMode', default=EXECUTION_MODE_DAILY)
    ) == EXECUTION_MODE_BURNOUT:
        yield f'{task_prefix}.FastCombatCount'
        yield f'{task_prefix}.RepeatCombatCount'
        yield f'{task_prefix}.RepeatCombatLeifCount'
        yield f'{task_prefix}.RepeatCombatPrioritizeStamina'

    if combat_domain != 'Saint37':
        yield f'{task_prefix}.Saint37AutoRecycle'

    # Burnout mode schedules by stamina regeneration. Dimensional hunt
    # consumes its own resource, so it remains outside this mode.
    if combat_domain == 'Hunt' and combat_hunt_grade == 'Dimensional':
        yield f'{task_prefix}.BurnoutMode'

    if combat_domain in ('Saint37', 'Episode4'):
        yield f'{task_prefix}.Element'
        yield f'{task_prefix}.AltarGrade'
        yield f'{task_prefix}.HuntGrade'
    else:
        if combat_domain != 'SpiritAltar':
            yield f'{task_prefix}.AltarGrade'
        if combat_domain != 'Hunt':
            yield f'{task_prefix}.HuntGrade'

    if deep_get(data, 'Combat.UrgentTasks.Enable', default=True) is False:
        yield 'Combat.UrgentTasks.Difficulty'
