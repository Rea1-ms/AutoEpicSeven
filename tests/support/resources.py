"""Explicit resource observations and an account-free scheduler configuration."""

from contextlib import nullcontext
from datetime import timedelta
from types import SimpleNamespace

from module.config.deep import deep_get, deep_set
from tasks.base.resource_bar import (
    RESOURCE_BAR_SPECS,
    ResourceBarSpec,
    ResourceBarValue,
)
from tasks.base.resource_scheduling import local_now


def resource_values(**values):
    result = {}
    for key, value in values.items():
        current, total = value if isinstance(value, tuple) else (value, 0)
        spec = RESOURCE_BAR_SPECS.get(key, ResourceBarSpec(key, "int"))
        result[key] = ResourceBarValue(spec, str(current), current, total)
    return result


class ResourceConfig:
    def __init__(self):
        self.data = {}
        self.modified = {}
        self.Emulator_PackageName = "com.stove.epic7.google"
        self.Emulator_GameLanguage = "cn"
        self.Scheduler_ServerUpdate = "02:00"
        self.task = SimpleNamespace(command="Store")
        for task in ("Arena", "Combat", "DataUpdate"):
            self.cross_set(f"{task}.Scheduler.Enable", True)
            self.cross_set(f"{task}.Scheduler.NextRun", local_now().replace(microsecond=0) + timedelta(hours=10))
        self.cross_set("Arena.Arena", {"BurnoutMode": "Burnout", "NPCCombat": True})
        self.cross_set("Combat.Combat", {"BurnoutMode": "Burnout", "Domain": "Hunt"})

    def cross_get(self, key, default=None):
        return deep_get(self.data, key, default)

    def cross_set(self, key, value):
        deep_set(self.data, key, value)

    def multi_set(self):
        return nullcontext()

    def is_task_enabled(self, task):
        return self.cross_get(f"{task}.Scheduler.Enable", False)

    def task_delay(self, *, server_update=None, success=None, target=None, minute=None, task=None):
        if target is None:
            target = local_now().replace(microsecond=0) + timedelta(minutes=minute or (1440 if server_update else 30))
        self.cross_set(f"{task or self.task.command}.Scheduler.NextRun", target)
