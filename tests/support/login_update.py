"""Registered update captures with real matching, intervals, and a virtual clock."""

import json

import numpy as np

from module.base.base import ModuleBase
from tests.support.interaction import InteractionReplay, scene
from tests.support.offline import ROOT, fixture_image, record_action, record_frame

MANIFEST = ROOT / 'tests/fixtures/login_update/manifest.json'
NORMALIZATIONS = {
    key: value.get('normalization')
    for key, value in json.loads(MANIFEST.read_text(encoding='utf-8'))['fixtures'].items()
}


def capture(name, **values):
    key = f'20261029-{name}'
    return scene(fixture=key, portrait=NORMALIZATIONS.get(key) == 'rotate_90_clockwise', **values)


class LoginUpdateReplay(InteractionReplay):
    def __init__(self, task_type, frames, clock, **kwargs):
        self.click_areas = []
        self.lifecycle = []
        self.sleeps = []
        self.native_taps = []
        super().__init__(task_type, frames, clock, **kwargs)

    def screenshot(self):
        super().screenshot()
        self.image = (
            fixture_image(MANIFEST, self.frame['fixture'])
            if self.frame.get('fixture') else np.zeros((720, 1280, 3), dtype=np.uint8)
        )
        if self.frame.get('portrait'):
            self.image = np.rot90(self.image).copy()
        # Explicit mutations model animation/occlusion; they are not captures.
        if 'brightness' in self.frame:
            self.image = (self.image * self.frame['brightness']).astype(np.uint8)
        if self.frame.get('hide_start_text'):
            self.image[580:612, 574:706] = 0
        if self.frame.get('overlay'):
            # Paste real popup anchors onto a title frame to isolate priority
            # even when background start text remains bright and readable.
            for button in self.frame['overlay']:
                for asset in button.buttons:
                    left, top, right, bottom = asset.area
                    self.image[top:bottom, left:right] = asset.image
        if any(key in self.frame for key in ('brightness', 'hide_start_text', 'overlay')):
            record_frame(f'login-explicit-mutation:{self.index}:brightness={self.frame.get("brightness")}'
                         f':hide_start_text={self.frame.get("hide_start_text", False)}'
                         f':overlay={[button.name for button in self.frame.get("overlay", ())]}')

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

    def click_adb(self, x, y):
        self.native_taps.append((self.index, x, y))
        self.actions.append(record_action((self.index, 'PACKAGE_INSTALL_CONFIRM')))

    def app_stop(self):
        self.lifecycle.append(record_action((self.index, 'app_stop')))

    def app_start(self):
        self.lifecycle.append(record_action((self.index, 'app_start')))

    def get_orientation(self):
        return 0
