from tasks.activity.calendar import ACTIVITY_TASK_MODES
from tasks.activity.entry import SpecialActivityEntry


class LimitedActivityEntry(SpecialActivityEntry):
    """Schedule limited events sharing the common activity entrance."""

    SCHEDULER_TASK = "LimitedActivity"
    ACTIVITY_MODES = ACTIVITY_TASK_MODES[SCHEDULER_TASK]
    COMMON_ACTIVITY_OPTIONS = {
        "free_gacha_20": "LimitedActivity_GetFreeGacha",
        "e7wc_battle_gate": "LimitedActivity_GetE7wcBattleGateReward",
        "koharu_raffle": "LimitedActivity_GetKoharuRaffleReward",
    }
