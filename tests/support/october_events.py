"""Registered captures plus explicit synthetic OCR with production timers."""

import numpy as np

from module.base.base import ModuleBase
from tests.support.interaction import InteractionReplay, scene
from tests.support.meirin_reward import Config
from tests.support.offline import ROOT, fixture_image, record_frame

MANIFEST = ROOT / "tests/fixtures/historical/manifest.json"
HOME, AVAILABLE, POPUP, RECEIVED, HOME_AFTER, ATTENDANCE, DAILY = (
    "203759-503", "203901-709", "203905-985", "203908-514",
    "203917-664", "204832-039", "205440-353",
)
EPIC_HOME_READY, EPIC_DAILY_READY, EPIC_REWARD, EPIC_DAILY_AFTER, EPIC_HOME_CLAIMED = (
    "213319-743", "213321-139", "213322-958", "213324-130", "213328-666",
)


def capture(name, **values):
    return scene(fixture=f"20261029-{name}", page="page_common_activity", **values)


class OctoberReplay(InteractionReplay):
    def __init__(self, task_type, frames, clock):
        self.images = {}
        self.click_areas = []
        super().__init__(task_type, frames, clock)
        self.task.config = Config()
        self.task.config.LimitedActivity_GetCompletionRecord = True
        self.task.config.LimitedActivity_GetDetectiveSearch = True
        self.task.config.LimitedActivity_GetEpicPathDailyReward = True
        self.task.activity_id = "october_test"
        self.task.match_template_color = self.match_template_color
        self.task.match_template_luma = self.match_template_luma

    def screenshot(self):
        super().screenshot()
        key = self.frame.get("fixture")
        if key:
            if key not in self.images:
                self.images[key] = fixture_image(MANIFEST, key)
            self.image = self.images[key].copy()
            if self.frame.get("partial"):
                # Synthetic partial completion: only first-tier receipt from
                # the real received capture; other tiers remain claimable.
                received = fixture_image(MANIFEST, f"20261029-{RECEIVED}")
                self.image[555:612, 305:490] = received[555:612, 305:490]
                record_frame("synthetic:detective-first-tier-received")
            if self.frame.get("locked"):
                # No locked-tier capture exists. Blank the controls only;
                # tests inject an explicit points observation separately.
                self.image[550:655, 300:1240] = 0
                record_frame("synthetic:detective-locked-controls")
            if "epic_receipts" in self.frame:
                # No capture shows all four daily tasks claimed. Explicitly
                # compose receipt controls from the real stamina receipt;
                # the test supplies synthetic counters separately.
                from tasks.activity.epic_path import EpicPath

                received = fixture_image(MANIFEST, f"20261029-{EPIC_DAILY_AFTER}")
                for index in self.frame["epic_receipts"]:
                    _, top, _, bottom = EpicPath.ROW_AREAS[index]
                    upper, lower = top + 16, bottom - 15
                    self.image[upper:lower, 904:1080] = received[443:443 + lower - upper, 904:1080]
                record_frame("synthetic:epic-daily-receipt-controls")
        else:
            self.image = np.zeros((720, 1280, 3), dtype=np.uint8)

    def appear(self, button, interval=0, **kwargs):
        if self.frame.get("fixture"):
            return ModuleBase.appear(self.task, button, interval=interval, **kwargs)
        return super().appear(button, interval=interval, **kwargs)

    def match_template_color(self, button, interval=0, **kwargs):
        if self.frame.get("fixture"):
            return ModuleBase.match_template_color(self.task, button, interval=interval, **kwargs)
        return self.appear(button, interval=interval, **kwargs)

    def match_template_luma(self, button, interval=0, **kwargs):
        if self.frame.get("fixture"):
            return ModuleBase.match_template_luma(self.task, button, interval=interval, **kwargs)
        return self.appear(button, interval=interval, **kwargs)

    def click(self, button):
        super().click(button)
        self.click_areas.append((self.index, button.name, tuple(button.button)))
