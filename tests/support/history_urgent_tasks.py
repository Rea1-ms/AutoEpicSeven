# ruff: noqa: E402
from module.config import server as _test_server

_test_server.set_lang("global_cn")


"""Offline checks for the global-CN limited-time Urgent Tasks flow."""

from pathlib import Path
from types import SimpleNamespace


WORKTREE = Path(__file__).resolve().parents[2]
PROJECT_ROOT = WORKTREE
SCREENSHOTS = Path(__file__).parent / "screenshots" / "urgent_tasks"

import module.config.server as server

server.lang = "global_cn"
server.server = "OVERSEA-Play"

from tasks.dungeon.dungeon import Combat


class OfflineDevice:
    def __init__(self, image):
        self.image = image

    @staticmethod
    def stuck_record_add(*args, **kwargs):
        pass


class OfflineConfig(SimpleNamespace):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.cross_values = {}

    def cross_get(self, path, default=None):
        return self.cross_values.get(path, default)

    def cross_set(self, path, value):
        self.cross_values[path] = value


class ExchangeDevice:
    def __init__(self, currency: int):
        self.currency = currency
        self.batches = []
        self.screenshots = 0
        self.click_record_clears = 0

    def screenshot(self):
        self.screenshots += 1

    def multi_click(self, button, n, interval):
        self.batches.append((button.name, n, interval))
        self.currency = max(self.currency - n * 100, 0)

    def click_record_clear(self):
        self.click_record_clears += 1


def build_combat(image_name: str, difficulty: str) -> Combat:
    combat = Combat.__new__(Combat)
    combat.config = OfflineConfig(
        Combat_UrgentTasksDifficulty=difficulty,
        Emulator_GameLanguage="cn",
        Scheduler_ServerUpdate="02:00",
        task=SimpleNamespace(command="Combat"),
    )
    combat.device = OfflineDevice((_ for _ in ()).throw(AssertionError('Unregistered historical screenshot requested: '+image_name)))
    combat._dungeon_domain = lambda: "UrgentTasks"
    return combat

__all__ = ['build_combat']
