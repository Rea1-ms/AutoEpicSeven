"""Carrot captures with explicitly composed missing intermediate states."""

from tasks.activity.carrot_reward import CarrotReward
from tests.support.activity_replay import MANIFEST, ActivityReplay
from tests.support.offline import fixture_image, record_frame

EVENT_ID = "carrot_reward_2026_11_12"
CAKE, RULES, AVAILABLE, POPUP, RECEIVED = (
    f"e7-activity-1112-{number}" for number in range(1, 6)
)


class CarrotReplay(ActivityReplay):
    def __init__(self, frames, clock):
        super().__init__(CarrotReward, frames, clock)
        self.task.activity_id = EVENT_ID
        self.task.config.LimitedActivity_GetCarrotReward = True

    def screenshot(self):
        super().screenshot()
        if self.frame.get("carrot_locked"):
            for left, top, right, bottom in CarrotReward.REWARD_AREAS:
                self.image[top:bottom, left:right] = 0
            record_frame("synthetic:carrot-locked-controls")
        if "carrot_receipts" in self.frame:
            received = fixture_image(MANIFEST, RECEIVED)
            for index in self.frame["carrot_receipts"]:
                left, top, right, bottom = CarrotReward.REWARD_AREAS[index]
                self.image[top:bottom, left:right] = received[top:bottom, left:right]
            record_frame("synthetic:carrot-partial-receipts")
