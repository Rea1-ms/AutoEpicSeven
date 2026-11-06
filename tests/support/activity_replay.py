"""Registered captures plus explicit synthetic OCR with production timers."""

import unittest
from contextlib import ExitStack

import numpy as np

from module.base.base import ModuleBase
from module.config import server
from tests.support.interaction import InteractionReplay, scene
from tests.support.meirin_reward import Config
from tests.support.offline import ROOT, ControlledClock, fixture_image, record_frame

MANIFEST = ROOT / "tests/fixtures/historical/manifest.json"
HOME, AVAILABLE, POPUP, RECEIVED, HOME_AFTER, ATTENDANCE, DAILY = (
    "20261029-203759-503", "20261029-203901-709", "20261029-203905-985", "20261029-203908-514",
    "20261029-203917-664", "20261029-204832-039", "20261029-205440-353",
)
EPIC_HOME_READY, EPIC_DAILY_READY, EPIC_REWARD, EPIC_DAILY_AFTER, EPIC_HOME_CLAIMED = (
    "20261029-213319-743", "20261029-213321-139", "20261029-213322-958", "20261029-213324-130", "20261029-213328-666",
)


def capture(name, **values):
    return scene(fixture=name, page="page_common_activity", **values)


class ActivityReplay(InteractionReplay):
    def __init__(self, task_type, frames, clock):
        self.images = {}
        self.click_areas = []
        super().__init__(task_type, frames, clock)
        self.task.config = Config()
        self.task.config.LimitedActivity_GetCompletionRecord = True
        self.task.config.LimitedActivity_GetDetectiveSearch = True
        self.task.config.LimitedActivity_GetEpicPathDailyReward = True
        self.task.activity_id = f"{task_type.__name__}_offline"
        self.task.match_template_color = self.match_template_color
        self.task.match_template_luma = self.match_template_luma

    def screenshot(self):
        super().screenshot()
        key = self.frame.get("fixture")
        if key:
            if key not in self.images:
                self.images[key] = fixture_image(MANIFEST, key)
            self.image = self.images[key].copy()
            if "dim" in self.frame:
                # Explicitly simulated overlay/loading frames preserve the
                # captured shapes while making their controls unavailable.
                x1, y1, x2, y2 = self.frame.get("dim_area", (0, 0, 1280, 720))
                self.image[y1:y2, x1:x2] = (
                    self.image[y1:y2, x1:x2].astype(np.float32) * self.frame["dim"]
                ).astype(np.uint8)
                record_frame(f"synthetic:activity-dimmed-{self.frame['dim']}")
            if self.frame.get("partial"):
                # Synthetic partial completion: only first-tier receipt from
                # the real received capture; other tiers remain claimable.
                received = fixture_image(MANIFEST, RECEIVED)
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

                received = fixture_image(MANIFEST, EPIC_DAILY_AFTER)
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


class ActivityReplayCase(unittest.TestCase):
    """Share fixture injection and the production-clock replay boundary."""

    def setUp(self):
        server.set_lang("global_cn")
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.clock = self.stack.enter_context(ControlledClock())

    def replay(self, task, frames):
        return ActivityReplay(task, frames, self.clock)
