"""Run one verified brawl round and retain its observed hero rewards."""

from dataclasses import dataclass
from enum import Enum

from module.base.base import ModuleBase
from module.base.timer import Timer
from module.logger import logger
from tasks.base.assets.assets_base_popup import TOUCH_TO_CLOSE
from tasks.brawl.assets.assets_brawl import (
    HERO_RESULT_CHECK,
    PREPARATION_CHECK,
    SPEED_ONE_CHECK,
)
from tasks.brawl.cursor import read_cursor
from tasks.brawl.heroes import HeroResult, read_hero_result
from tasks.brawl.rules import TOTAL_ROUNDS, team_capacity
from tasks.brawl.vision import (
    BrawlMode,
    BrawlObservation,
    BrawlScreen,
    BrawlVision,
    PreparationPanel,
)


class SpeedOutcome(str, Enum):
    ENABLED = "enabled"
    SCENE_CHANGED = "scene_changed"
    TIMEOUT = "timeout"


class RoundOutcome(str, Enum):
    NEXT_PREPARATION = "next_preparation"
    RUN_ENDED = "run_ended"
    FAILED = "failed"


@dataclass(frozen=True)
class RoundResult:
    outcome: RoundOutcome
    panel: PreparationPanel | None = None
    hero_results: tuple[HeroResult, ...] = ()
    reason: str = ""


class BrawlCombat(ModuleBase):
    ACTION_INTERVAL = 3
    ACTION_TIMER = "BrawlBenchScan"
    STEP_TIMEOUT = 15
    UNREAD_TIMEOUT = 60
    PREPARATION_STABLE_INTERVAL = 0.6
    NETWORK_TIMEOUT = 30
    NETWORK_RETRY_INTERVAL = 5
    NETWORK_RETRY_DELAY = 3
    SPEED_INTERVAL = 3
    SPEED_TIMEOUT = 10
    SPEED_INTERRUPTED_STATES = frozenset({
        BrawlScreen.NETWORK_RETRY,
        BrawlScreen.NETWORK_DISCONNECTED,
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

    def play_round(self, panel: PreparationPanel, game_mode: BrawlMode, skip_first_screenshot=True):
        """Start once, collect readable rewards and stop at the next preparation.

        Pages:
            in: stable preparation matching a verified, nonempty formation
            out: next preparation, an explicit same-round retry after a loss,
                a retained run result, or the last unconfirmed frame

        This method does not establish field identities or apply hero results to
        a roster. Its caller must reconcile the returned results against a fresh
        complete bench and occupied-cell observation before arranging again.
        The caller shares interval_timer with formation so the final grid close,
        battle start, speed toggle and reward close obey one action interval.
        """
        if not isinstance(panel, PreparationPanel) or not isinstance(game_mode, BrawlMode):
            raise TypeError("A verified preparation panel and game mode are required")
        numbers = (panel.round_number, panel.level, panel.deployed, panel.capacity,
                   panel.coins, panel.synergy_tokens)
        if (any(type(value) is not int for value in numbers)
                or not 1 <= panel.round_number <= TOTAL_ROUNDS
                or panel.capacity != team_capacity(panel.level)
                or not 0 < panel.deployed <= panel.capacity
                or not 0 <= panel.coins <= 999999 or not 0 <= panel.synergy_tokens <= 999999):
            raise ValueError("A complete, nonempty formation is required before battle")
        timeout = Timer(self.STEP_TIMEOUT).start()
        unread_timeout = None
        started = battle_seen = reward_seen = False
        round_end = None
        results = []
        pending = None
        pending_closed = False
        preparation_candidate = None
        preparation_stable = Timer(self.PREPARATION_STABLE_INTERVAL)
        network_timeout = None
        network_ready = None

        def finish(outcome, reason, preparation=None):
            if outcome == RoundOutcome.FAILED:
                logger.warning(f"Brawl round remains unconfirmed: {reason}")
            else:
                logger.info(f"Brawl round stopped: {reason}")
            return RoundResult(outcome, preparation, tuple(results), reason)

        while True:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()
            image = self.device.image
            vision = BrawlVision(image)
            current = vision.observe()
            if current.game_mode is not None and current.game_mode != game_mode:
                return finish(RoundOutcome.FAILED, "The brawl mode changed")
            if current.state == BrawlScreen.NETWORK_DISCONNECTED:
                return finish(RoundOutcome.FAILED, "Network disconnected; the tracked run needs reconstruction")
            if current.state == BrawlScreen.NETWORK_RETRY:
                preparation_candidate = None
                preparation_stable.clear()
                if not started:
                    return finish(RoundOutcome.FAILED, "Network retry appeared before this call started a round")
                if network_timeout is None:
                    network_timeout = Timer(self.NETWORK_TIMEOUT).start()
                if network_ready is None:
                    network_ready = Timer(self.NETWORK_RETRY_DELAY).start()
                if network_timeout.reached():
                    return finish(RoundOutcome.FAILED, "Network retry did not recover within its deadline")
                # Preserve started/battle_seen/rewards: retry resumes the same
                # server request, never authorizes another start or acquisition.
                # Match the shared arrow at its current position; its original
                # click rectangle belongs to a different popup layout. Wait by
                # observing fresh frames, without the shared handler's sleep or
                # login handoff (which would invalidate this tracked roster).
                if (network_ready.reached()
                        and self.interval_is_reached(self.ACTION_TIMER, interval=self.NETWORK_RETRY_INTERVAL)
                        and TOUCH_TO_CLOSE.match_template(image, similarity=.85)):
                    self.device.click(TOUCH_TO_CLOSE)
                    self.interval_reset(self.ACTION_TIMER, interval=self.NETWORK_RETRY_INTERVAL)
                    logger.info("Brawl network retry requested; retaining the current round")
                continue
            if network_timeout is not None:
                # Give the interrupted transition one fresh local deadline, not
                # a new battle start. Retain the total retry budget across popup
                # reappearances so flickering unknown/preparation cannot renew it.
                if network_ready is not None and timeout is not None:
                    timeout.reset()
                network_ready = None
            # A readable preparation can precede coin count-up completion and
            # the bench's fade-in. Stability belongs to consecutive readable
            # frames of this return, never to time spent in combat or overlays.
            # Unknown frames discard the candidate without extending the local
            # transition deadline; otherwise an intermittent popup could keep
            # a stale balance actionable or restart the wait indefinitely.
            if (current.state != BrawlScreen.PREPARATION or current.panel is None
                    or current.game_mode != game_mode):
                preparation_candidate = None
                preparation_stable.clear()
            # A run result is a positive terminal boundary, but an unrelated
            # result present at entry cannot stand in for this call's battle.
            # Leave the terminal screen visible instead of exiting the run.
            if current.state in (BrawlScreen.RUN_VICTORY, BrawlScreen.RUN_DEFEAT):
                if not started or current.game_mode != game_mode:
                    return finish(RoundOutcome.FAILED, "A run result appeared outside the requested battle")
                return finish(RoundOutcome.RUN_ENDED, f"Run result: {current.state.value}")
            if current.state not in (BrawlScreen.PREPARATION, BrawlScreen.COMBAT,
                                     BrawlScreen.ROUND_CLEAR, BrawlScreen.ROUND_FAILED,
                                     BrawlScreen.HERO_RESULT, BrawlScreen.UNKNOWN):
                return finish(RoundOutcome.FAILED, f"Unsupported round scene: {current.state.value}")
            # Hero artwork can cover the mode title (the captured Carrot spear
            # does so). Only a positive hero-result page after this call's
            # confirmed battle may inherit that mode. A result before combat,
            # an unknown page or an unread preparation never receives this
            # exception; an explicitly different mode was rejected above.
            owned_reward = started and battle_seen and current.state == BrawlScreen.HERO_RESULT
            if (current.game_mode is None and not owned_reward) or current.state == BrawlScreen.UNKNOWN:
                # A missing anchor during a confirmed fight may be a long
                # animation. It must never turn the short start/reward timeout
                # into a deadline for the whole battle or authorize a click.
                # A separate consecutive-unread guard still leaves an unknown
                # popup for inspection instead of waiting forever. Any next
                # recognized frame clears it; a long recognized fight has no
                # deadline, and intermittent animations do not accumulate time.
                if unread_timeout is None:
                    unread_timeout = Timer(self.UNREAD_TIMEOUT, count=3).start()
                if timeout is not None and timeout.reached():
                    return finish(RoundOutcome.FAILED, "The round transition remained unread")
                if unread_timeout.reached():
                    return finish(RoundOutcome.FAILED, "The brawl scene remained continuously unread")
                continue
            unread_timeout = None

            if current.state == BrawlScreen.PREPARATION:
                observed = current.panel
                if observed is None:
                    if timeout is None:
                        timeout = Timer(self.STEP_TIMEOUT).start()
                    if timeout.reached():
                        return finish(RoundOutcome.FAILED, "The preparation panel remained unread")
                    continue
                if not started:
                    if observed != panel:
                        return finish(RoundOutcome.FAILED, "Preparation changed before battle start")
                    if timeout.reached():
                        return finish(RoundOutcome.FAILED, "Battle start remained unavailable")
                    if (vision.matches(PREPARATION_CHECK, color=True)
                            and self.interval_is_reached(self.ACTION_TIMER, interval=self.ACTION_INTERVAL)):
                        self.device.click(PREPARATION_CHECK)
                        self.interval_reset(self.ACTION_TIMER, interval=self.ACTION_INTERVAL)
                        started = True
                        timeout.reset()
                        logger.info(f"Brawl round {panel.round_number} start requested")
                    continue
                if not battle_seen:
                    if observed != panel:
                        return finish(RoundOutcome.FAILED, "Preparation changed without observed battle progress")
                else:
                    if (observed.round_number not in (panel.round_number, panel.round_number + 1)
                            or observed.level not in (panel.level, panel.level + 1)
                            or observed.capacity != team_capacity(observed.level)
                            or observed.deployed != panel.deployed):
                        return finish(RoundOutcome.FAILED, "The returned preparation does not match one round")
                    if reward_seen and (pending is None or not pending_closed):
                        return finish(RoundOutcome.FAILED, "Preparation returned before the hero reward was closed")
                    next_round = observed.round_number == panel.round_number + 1
                    if next_round or round_end == BrawlScreen.ROUND_FAILED:
                        if timeout is None:
                            timeout = Timer(self.STEP_TIMEOUT).start()
                        cursor = read_cursor(image, observation=current)
                        bench_ready = cursor.empty is True or (
                            cursor.empty is False and cursor.selected is not None
                            and cursor.selected.imprint is not None and cursor.bottom is not None)
                        candidate = (observed, cursor.selected, cursor.top, cursor.bottom,
                                     cursor.empty, cursor.next_area)
                        if not bench_ready:
                            preparation_candidate = None
                            preparation_stable.clear()
                        elif candidate != preparation_candidate:
                            preparation_candidate = candidate
                            preparation_stable.reset()
                            logger.info(f"Brawl post-battle preparation settling: {observed}")
                        elif preparation_stable.reached():
                            reason = (f"Preparation for round {observed.round_number} confirmed" if next_round else
                                      f"Round {observed.round_number} failed; retry preparation confirmed")
                            return finish(RoundOutcome.NEXT_PREPARATION, reason, observed)
                        # Do not scan/swipe an unread bench, and never restart
                        # this deadline when another intermediate balance is
                        # seen. A positive settled return wins over timeout on
                        # the same slow frame, just like other terminal states.
                        if timeout.reached():
                            return finish(RoundOutcome.FAILED,
                                          "The post-battle preparation or bench did not settle")
                        continue
                    # A still-visible old preparation after the start click is
                    # neither an invitation to start again nor proof of a loss.
                    # The same round is accepted only with a real failed banner.
                    if timeout is None:
                        timeout = Timer(self.STEP_TIMEOUT).start()
                if timeout.reached():
                    return finish(RoundOutcome.FAILED, "Battle progress or next preparation remained unconfirmed")
                continue

            if not started:
                return finish(RoundOutcome.FAILED, "Battle or rewards appeared before this call started a round")
            if current.state == BrawlScreen.COMBAT:
                if round_end is not None or reward_seen:
                    return finish(RoundOutcome.FAILED, "Combat reappeared after the round rewards began")
                if not battle_seen:
                    logger.info(f"Brawl round {panel.round_number} combat confirmed")
                battle_seen = True
                timeout = None
                # Only positively resumed combat ends this network episode.
                # A later outage gets its own budget; unknown/old preparation
                # and popup flicker above cannot renew the interrupted episode.
                network_timeout = None
                # Device.screenshot also checks its own inactivity watchdog;
                # disabling only this loop's short timeout does not protect a
                # long automatic fight from that independent 60-second limit.
                # This frame positively proves combat in the requested mode,
                # after our start and before any reward/end boundary, so it is
                # legitimate progress even when no input is needed. Never clear
                # the watchdog for unknown, reward or waiting frames: doing so
                # would conceal an unsupported popup or a stalled transition.
                self.device.stuck_record_clear()
                if (self.interval_is_reached(self.ACTION_TIMER, interval=self.ACTION_INTERVAL)
                        and self._handle_double_speed(current)):
                    self.interval_reset(self.ACTION_TIMER, interval=self.ACTION_INTERVAL)
                continue

            if current.state in (BrawlScreen.ROUND_CLEAR, BrawlScreen.ROUND_FAILED):
                if round_end is not None and round_end != current.state:
                    return finish(RoundOutcome.FAILED, "The observed round outcome changed")
                if round_end is None:
                    round_end = current.state
                    timeout = Timer(self.STEP_TIMEOUT).start()
                    logger.info(f"Brawl round transition: {current.state.value}")
                battle_seen = True
                # Captured round banners are automatic transitions, not buttons.
                # Do not click their detection rectangles or infer rewards from
                # the number of times a transient banner is observed.
                if timeout.reached():
                    return finish(RoundOutcome.FAILED, "The automatic round transition did not complete")
                continue

            if not battle_seen:
                return finish(RoundOutcome.FAILED, "A hero reward appeared without confirmed battle progress")
            # The page boundary matters even when its name or badge is unread.
            # Otherwise a disappearing result could hide a field hero's imprint
            # upgrade: bench and occupied-cell checks cannot recover that badge.
            # Unread tail frames after an already verified close do not invent a
            # new reward, but the first result must never disappear unrecorded.
            reward_seen = True
            if timeout is None:
                timeout = Timer(self.STEP_TIMEOUT).start()
            hero = read_hero_result(image)
            if hero is not None and hero != pending:
                if hero in results:
                    return finish(RoundOutcome.FAILED, "An earlier hero reward reappeared")
                if pending is not None and not pending_closed:
                    return finish(RoundOutcome.FAILED, "The hero reward changed before its close was requested")
                previous = next((item for item in reversed(results) if item.name == hero.name), None)
                if previous is not None and hero.previous_imprint != previous.imprint:
                    return finish(RoundOutcome.FAILED, "Hero rewards do not form a consistent imprint chain")
                pending = hero
                pending_closed = False
                timeout.reset()
            if timeout.reached():
                return finish(RoundOutcome.FAILED, "The hero reward remained unread or unclosed")
            # The obtained heading can appear before the bottom continue prompt.
            # Read the whole result and positively match that prompt before one
            # close click. Repeated images after the click never add another hero
            # or trigger another close; a distinct verified result opens a new
            # reward step while preserving ordered imprint changes for the roster.
            if (hero is not None and hero == pending and not pending_closed
                    and vision.matches(HERO_RESULT_CHECK)
                    and self.interval_is_reached(self.ACTION_TIMER, interval=self.ACTION_INTERVAL)):
                self.device.click(HERO_RESULT_CHECK)
                self.interval_reset(self.ACTION_TIMER, interval=self.ACTION_INTERVAL)
                results.append(hero)
                pending_closed = True
                timeout.reset()
                logger.info(f"Brawl hero reward observed: {hero.name}, imprint={hero.imprint.name}")

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
        if (self._speed_control_available(observation) and observation.speed == 1
                and self.interval_is_reached(SPEED_ONE_CHECK, interval=self.SPEED_INTERVAL)):
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
