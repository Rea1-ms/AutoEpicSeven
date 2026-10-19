"""Support event screenshots with real production timers and action recording."""

from module.config import server

server.set_lang("global_cn")

from tasks.activity.support_reward import SupportReward
from tests.support.interaction import InteractionReplay, scene
from tests.support.meirin_reward import MANIFEST, Config
from tests.support.offline import fixture_image, record_frame

EVENT_ID = "support_reward_2026_10_19"
AVAILABLE = "support-reward-available"
INCOMPLETE = "support-reward-incomplete"
RECEIVED = "support-reward-received"
WEEKLY = "support-reward-weekly"
POPUP = "support-reward-popup"
HOME = "support-reward-home"
HOME_AFTER = "support-reward-home-after"
CAPTURES = (AVAILABLE, INCOMPLETE, RECEIVED, WEEKLY, POPUP, HOME, HOME_AFTER)


def captured(fixture, *, seconds=0.5, weekly_claim=False):
    return scene(
        fixture=fixture,
        seconds=seconds,
        weekly_claim=weekly_claim,
        page="page_common_activity",
    )


class SupportReplay(InteractionReplay):
    def __init__(self, frames, clock):
        self.images = {}
        super().__init__(SupportReward, frames, clock)
        self.task.config = Config()
        self.task.config.LimitedActivity_GetMeirinReward = False
        self.task.config.LimitedActivity_GetSupportReward = True
        self.task.activity_id = EVENT_ID
        self.task.match_template_color = self.match_template_color
        self.task.match_template_luma = self.match_template_luma

    def screenshot(self):
        super().screenshot()
        fixture = self.frame.get("fixture")
        if fixture:
            if fixture not in self.images:
                self.images[fixture] = fixture_image(MANIFEST, fixture)
            self.image = self.images[fixture].copy()
            record_frame(fixture)
            if self.frame.get("weekly_claim"):
                # No real weekly claim capture is available. This synthetic
                # button tests scrolling/claiming only, not live eligibility.
                claim = fixture_image(MANIFEST, AVAILABLE)
                self.image[350:407, 1044:1223] = claim[350:407, 1044:1223]
                record_frame("synthetic:support-weekly-claim-button")

    def appear(self, button, interval=0, **kwargs):
        if self.frame.get("fixture"):
            return SupportReward.appear(self.task, button, interval=interval, **kwargs)
        return super().appear(button, interval=interval, **kwargs)

    def match_template_color(self, button, interval=0, **kwargs):
        if self.frame.get("fixture"):
            return SupportReward.match_template_color(
                self.task, button, interval=interval, **kwargs
            )
        return self.appear(button, interval=interval, **kwargs)

    def match_template_luma(self, button, interval=0, **kwargs):
        if self.frame.get("fixture"):
            return SupportReward.match_template_luma(
                self.task, button, interval=interval, **kwargs
            )
        return self.appear(button, interval=interval, **kwargs)
