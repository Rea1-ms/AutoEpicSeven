"""Registered update captures with real matching, intervals, and a virtual clock."""

import numpy as np

from module.base.base import ModuleBase
from tests.support.interaction import InteractionReplay, scene
from tests.support.offline import ROOT, fixture_image, record_action

MANIFEST = ROOT / 'tests/fixtures/login_update/manifest.json'


def capture(name, **values):
    return scene(fixture=f'20261029-{name}', **values)


class LoginUpdateReplay(InteractionReplay):
    def __init__(self, task_type, frames, clock, **kwargs):
        self.click_areas = []
        self.lifecycle = []
        self.sleeps = []
        super().__init__(task_type, frames, clock, **kwargs)

    def screenshot(self):
        super().screenshot()
        self.image = (
            fixture_image(MANIFEST, self.frame['fixture'])
            if self.frame.get('fixture') else np.zeros((720, 1280, 3), dtype=np.uint8)
        )

    def appear(self, button, interval=0, **kwargs):
        if self.frame.get('fixture'):
            return ModuleBase.appear(self.task, button, interval=interval, **kwargs)
        return super().appear(button, interval=interval, **kwargs)

    def click(self, button):
        super().click(button)
        self.click_areas.append((self.index, button.name, tuple(button.button)))

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        super().sleep(seconds)

    def app_stop(self):
        self.lifecycle.append(record_action((self.index, 'app_stop')))

    def app_start(self):
        self.lifecycle.append(record_action((self.index, 'app_start')))

    def get_orientation(self):
        return 0
