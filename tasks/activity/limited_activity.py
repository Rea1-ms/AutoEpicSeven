from tasks.activity.calendar import ACTIVITY_TASK_MODES
from tasks.activity.entry import SpecialActivityEntry


class LimitedActivityEntry(SpecialActivityEntry):
    """Schedule limited events sharing the common activity entrance."""

    SCHEDULER_TASK = "LimitedActivity"
    ACTIVITY_MODES = ACTIVITY_TASK_MODES[SCHEDULER_TASK]
    COMMON_ACTIVITY_OPTIONS = {
        "free_gacha_20": "LimitedActivity_GetFreeGacha",
        "support_reward": "LimitedActivity_GetSupportReward",
        "completion_record": "LimitedActivity_GetCompletionRecord",
        "detective_search": "LimitedActivity_GetDetectiveSearch",
        "epic_path": "LimitedActivity_GetEpicPathDailyReward",
    }
