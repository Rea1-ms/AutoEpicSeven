"""Reusable speed control for a caller that already owns the brawl state loop."""

from enum import Enum

from module.base.base import ModuleBase
from module.base.timer import Timer
from module.logger import logger
from tasks.brawl.assets.assets_brawl import SPEED_ONE_CHECK
from tasks.brawl.vision import BrawlObservation, BrawlScreen, BrawlVision


class SpeedOutcome(str, Enum):
    ENABLED = "enabled"
    SCENE_CHANGED = "scene_changed"
    TIMEOUT = "timeout"


class BrawlCombat(ModuleBase):
    SPEED_INTERVAL = 3
    SPEED_TIMEOUT = 10
    SPEED_INTERRUPTED_STATES = frozenset({
        BrawlScreen.LEVEL_UP,
        BrawlScreen.HERO_RESULT,
        BrawlScreen.HERO_UNLOCK,
        BrawlScreen.RANK_UP,
        BrawlScreen.ROUND_CLEAR,
        BrawlScreen.ROUND_FAILED,
        BrawlScreen.FORMATION_SELECTION,
        BrawlScreen.RUN_VICTORY,
        BrawlScreen.RUN_DEFEAT,
        BrawlScreen.TRANSMIT_SELECTION,
        BrawlScreen.PROBABILITY,
        BrawlScreen.GIVE_UP_CONFIRM,
        BrawlScreen.LOBBY,
    })

    @staticmethod
    def _speed_control_available(observation: BrawlObservation):
        return observation.state == BrawlScreen.COMBAT or (
            observation.state == BrawlScreen.PREPARATION and observation.panel is not None
        )

    def _handle_double_speed(self, observation: BrawlObservation):
        # The observation must come from the current device image. An x1 icon
        # underneath an overlay is not an actionable control. Keep the interval
        # across consecutive calls: clearing it on entry would permit an extra
        # toggle while the game's previous click is still pending.
        if self._speed_control_available(observation) and observation.speed == 1:
            if self.interval_is_reached(SPEED_ONE_CHECK, interval=self.SPEED_INTERVAL):
                self.device.click(SPEED_ONE_CHECK)
                self.interval_reset(SPEED_ONE_CHECK, interval=self.SPEED_INTERVAL)
                return True
        return False

    def handle_double_speed(self):
        """Click a positively recognized x1 control; the caller owns screenshots.

        Returns:
            bool: Whether this call actually clicked. This is not confirmation
                that the game switched speed; only a later x2 image proves it.
        """
        return self._handle_double_speed(BrawlVision(self.device.image).observe())

    def ensure_double_speed(self, skip_first_screenshot=True):
        """Bound the short speed-toggle interaction, never the whole battle.

        Args:
            skip_first_screenshot: Reuse the caller's freshly prepared first
                image; standalone callers must explicitly prepare that image.

        Returns:
            SpeedOutcome: ENABLED only after observing x2. SCENE_CHANGED leaves
                the recognized page for the parent loop. TIMEOUT proves nothing
                about speed or battle completion and must not count as success.

        Pages:
            in: stable brawl preparation or combat
            out: confirmed x2 on preparation/combat, a recognized interruption,
                or the final unconfirmed frame after the local timeout
        """
        timeout = Timer(self.SPEED_TIMEOUT, count=3).start()
        while True:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            observation = BrawlVision(self.device.image).observe()
            # Check positive exits before timeout and actions. A slow screenshot
            # may reveal x2 just as the timer expires; that frame is still valid
            # evidence. Neither a click nor the disappearance of x1 is success.
            if self._speed_control_available(observation) and observation.speed == 2:
                logger.info("Brawl double speed confirmed")
                return SpeedOutcome.ENABLED
            if observation.state in self.SPEED_INTERRUPTED_STATES:
                logger.info(f"Brawl speed control interrupted by {observation.state.value}")
                return SpeedOutcome.SCENE_CHANGED
            if timeout.reached():
                logger.warning("Brawl double speed remains unconfirmed after local timeout")
                return SpeedOutcome.TIMEOUT

            if self._handle_double_speed(observation):
                continue
