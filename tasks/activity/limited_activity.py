from tasks.activity.calendar import ACTIVITY_OPTIONS, ACTIVITY_TASK_MODES
from tasks.activity.entry import SpecialActivityEntry


class LimitedActivityEntry(SpecialActivityEntry):
    """Schedule limited events sharing the common activity entrance."""

    SCHEDULER_TASK = "LimitedActivity"
    ACTIVITY_MODES = ACTIVITY_TASK_MODES[SCHEDULER_TASK]
    COMMON_ACTIVITY_OPTIONS = ACTIVITY_OPTIONS[SCHEDULER_TASK]
