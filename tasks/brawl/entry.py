"""Reach the brawl lobby through the shared route graph and enter one run."""

from dataclasses import dataclass

from module.base.timer import Timer
from module.logger import logger
from tasks.base.ui import UI
from tasks.brawl.assets.assets_brawl import LOBBY_CHECK, LOBBY_EMPTY_CHECK
from tasks.brawl.assets.assets_brawl_entry import (
    LOBBY_CHALLENGE_CHECK,
    LOBBY_CONTINUE,
    LOBBY_ENTER,
    LOBBY_FREE_CHECK,
    LOBBY_NORMAL_CHECK,
)
from tasks.brawl.vision import (
    BrawlMode,
    BrawlObservation,
    BrawlScreen,
    BrawlVision,
    ObservationError,
)


@dataclass(frozen=True)
class EntryResult:
    observation: BrawlObservation
    resumed: bool


class BrawlEntry(UI):
    ACTION_TIMER = "BrawlBenchScan"
    ACTION_INTERVAL = 3
    ENTRY_TIMEOUT = 20

    def enter(self, skip_first_screenshot=True, *, only_free=True):
        """Navigate to the lobby, then continue a save or enter a free new run.

        Pages:
            in: a page supported by the global route graph, the brawl lobby,
                or an already open brawl preparation
            out: a positively verified preparation; unhandled states remain open

        Lobby mode is preserved. Entering a run is a one-shot action: neither a
        stale lobby frame nor an unread transition can authorize a second entry.
        An existing preparation is never sent back through the route graph.
        Disabling free-only entry permits a verified paid quotation; it never
        turns an absent free marker into evidence of a price or affordability.
        """
        if type(only_free) is not bool:
            raise ValueError("The free-only entry setting must be a boolean")
        if not skip_first_screenshot:
            self.device.screenshot()
        current = BrawlVision(self.device.image).observe()
        if current.state == BrawlScreen.UNKNOWN and current.game_mode is None:
            # Page registration is language-specific. The tool dispatches on
            # the resolved device language before importing this controller.
            from tasks.base.page import page_brawl_lobby

            self.ui_ensure(page_brawl_lobby, acquire_lang_checked=False,
                           skip_first_screenshot=True)
        elif current.state not in (BrawlScreen.PREPARATION, BrawlScreen.LOBBY, BrawlScreen.UNKNOWN):
            raise ObservationError(f"Cannot enter brawl from {current.state.value}; keep the current screen for inspection")

        timeout = Timer(self.ENTRY_TIMEOUT).start()
        pending = None
        expected_mode = None
        first = True
        while True:
            if first:
                first = False
            else:
                self.device.screenshot()
            vision = BrawlVision(self.device.image)
            current = vision.observe()

            if current.state == BrawlScreen.PREPARATION and current.panel is not None:
                if current.game_mode is None:
                    raise ObservationError("The entered preparation mode is unread")
                if expected_mode is not None and current.game_mode != expected_mode:
                    raise ObservationError("The entered preparation does not match the lobby mode")
                if pending == "new" and (current.panel.round_number != 1 or current.panel.deployed != 0):
                    raise ObservationError("A new run did not open on an empty first-round preparation")
                resumed = pending != "new"
                logger.info(f"Brawl preparation entered: mode={current.game_mode.value}, "
                            f"round={current.panel.round_number}, resumed={resumed}")
                return EntryResult(current, resumed)
            if current.state not in (BrawlScreen.PREPARATION, BrawlScreen.LOBBY, BrawlScreen.UNKNOWN):
                raise ObservationError(f"Brawl entry interrupted by {current.state.value}")
            if timeout.reached():
                raise ObservationError("Brawl entry did not reach a verified preparation")
            if current.state != BrawlScreen.LOBBY:
                continue

            modes = [mode for mode, button in (
                (BrawlMode.NORMAL, LOBBY_NORMAL_CHECK),
                (BrawlMode.CHALLENGE, LOBBY_CHALLENGE_CHECK),
            ) if vision.matches(button)]
            if len(modes) != 1:
                continue
            mode = modes[0]
            if expected_mode is not None and mode != expected_mode:
                raise ObservationError("The lobby mode changed while entry was pending")

            saved = vision.matches(LOBBY_CHECK, color=True)
            empty = vision.matches(LOBBY_EMPTY_CHECK, color=True)
            continuing = vision.matches(LOBBY_CONTINUE, color=True)
            entering = vision.matches(LOBBY_ENTER, color=True)
            if (saved and empty) or (continuing and entering) or (saved and entering) or (empty and continuing):
                raise ObservationError("The lobby contains contradictory save and entry controls")
            action = "continue" if saved and continuing else "new" if empty and entering else None
            if action is None:
                continue
            if pending is not None:
                if action != pending:
                    raise ObservationError("The lobby save changed after the entry request")
                continue
            if action == "new" and not vision.matches(LOBBY_FREE_CHECK):
                if only_free:
                    raise ObservationError("Free entry is not confirmed; free-only entry is enabled")
                # The user permits paid entry when this switch is disabled,
                # but no reliable paid-price capture has been collected yet.
                # Never infer a payment amount from the enabled entry button
                # or call an unread free marker exhausted free attempts.
                raise ObservationError("Paid brawl entry requires a verified price and available resources")
            # The verified lobby is the completed navigation boundary. The
            # pending entry below owns this one request across repeated frames;
            # a timer from the route or relic return cannot delay a new action.
            button = LOBBY_CONTINUE if action == "continue" else LOBBY_ENTER
            self.device.click(button)
            pending, expected_mode = action, mode
            timeout.reset()
            logger.info(f"Brawl lobby entry requested: {action}, mode={mode.value}")
