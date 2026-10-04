"""Registered hunt screenshots and deterministic image replays."""

from pathlib import Path
from types import SimpleNamespace

from tests.support.offline import fixture_image, record_action, record_frame


MANIFEST = Path(__file__).resolve().parents[1] / "fixtures/historical/manifest.json"
DARK_HUNTS = "20261003-175150-414"
ELEMENT_HUNTS = "20261003-175236-705"


def hunt_image(fixture_id):
    return fixture_image(MANIFEST, fixture_id)


class HuntReplay:
    def __init__(self, task_type, frames, clock):
        self.frames, self.clock, self.index = frames, clock, -1
        self.actions = []
        self.task = object.__new__(task_type)
        self.task.config = SimpleNamespace(
            Combat_Domain="Hunt", Combat_HuntBoss="Wyvern", Combat_Element="Dark",
            Combat_HuntGrade="Hell", Combat_FastCombat=True,
        )
        self.task.interval_timer = {}
        self.task.device = self
        self.task._is_prepare_page = lambda: False
        self.task._handle_dungeon_additional = lambda: False
        self.screenshot()

    def screenshot(self):
        self.index += 1
        if self.index >= len(self.frames):
            raise AssertionError("Hunt replay exhausted before a verified exit")
        self.clock.advance(0.5)
        self.image = self.frames[self.index]
        record_frame(f"hunt-replay:{self.index}")

    def stuck_record_add(self, button):
        pass

    def click(self, button):
        self.actions.append(record_action((self.index, button.name, tuple(button.button))))

    def multi_click(self, button, n, **kwargs):
        self.actions.append(record_action((self.index, button.name, tuple(button.button), n)))

    def swipe(self, start, end, **kwargs):
        self.actions.append(record_action((self.index, "swipe", start, end)))
