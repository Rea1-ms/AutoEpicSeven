"""Read transmit quotes and confirm only an already selected, unchanged batch."""

import hashlib
import re
from dataclasses import dataclass
from enum import Enum

import numpy as np

from module.base.base import ModuleBase
from module.base.timer import Timer
from module.logger import logger
from module.ocr.ocr import Ocr
from tasks.brawl.assets.assets_brawl import (
    OCR_TRANSMIT_COINS,
    OCR_TRANSMIT_TOKENS,
    TRANSMIT_CONFIRM,
    TRANSMIT_EMPTY_CHECK,
)
from tasks.brawl.assets.assets_brawl_actions import (
    TRANSMIT_COIN_ICON,
    TRANSMIT_TOKEN_ICON,
)
from tasks.brawl.rules import Resources
from tasks.brawl.vision import (
    BrawlMode,
    BrawlScreen,
    BrawlVision,
    ObservationError,
    PreparationPanel,
)

# The complete selection tray is included, not just its first portrait. Any
# changed portrait, imprint or later row invalidates the caller's consent. An
# exact digest is intentionally conservative: animated or obscured trays must
# be observed again and explicitly recaptured, never silently accepted.
SELECTION_AREA = (18, 86, 253, 566)


@dataclass(frozen=True)
class TransmitPanel:
    header: PreparationPanel
    quote: Resources
    selected: bool
    selection_fingerprint: str | None
    game_mode: BrawlMode


@dataclass(frozen=True)
class TransmitSelection:
    header: PreparationPanel
    quote: Resources
    selection_fingerprint: str
    game_mode: BrawlMode


class TransmitOutcome(str, Enum):
    CONFIRMED = "confirmed"
    SELECTION_CHANGED = "selection_changed"
    SCENE_CHANGED = "scene_changed"
    BALANCE_MISMATCH = "balance_mismatch"
    TIMEOUT = "timeout"


def parse_quote(values):
    """Read the displayed two-currency quote without inferring it from heroes."""
    if len(values) != 2:
        raise ObservationError("Incomplete transmit quote")
    if any(not np.isfinite(score) or score < 0.8 for _, score in values):
        raise ObservationError("Transmit quote has low recognition confidence")
    numbers = [re.sub(r"\s+", "", text) for text, _ in values]
    if any(re.fullmatch(r"[0-9]{1,6}", text) is None for text in numbers):
        raise ObservationError("Unrecognized transmit quote format")
    return Resources(*map(int, numbers))


def read_transmit_panel(image, lang="cn"):
    """Read a recognized selection tray; moving a hero into it is not a sale."""
    vision = BrawlVision(image, lang)
    observation = vision.observe()
    if observation.state != BrawlScreen.TRANSMIT_SELECTION or observation.game_mode is None:
        raise ObservationError("No recognized transmit selection scene")
    # A two-digit quote moves the coin and number left together. The currency
    # icons, rather than the previous single-digit OCR rectangle, delimit each
    # current number. Copy detection areas; wrapper offsets are shared state.
    boundaries = []
    for icon in (TRANSMIT_COIN_ICON, TRANSMIT_TOKEN_ICON):
        matches = icon.match_multi_template(image, similarity=0.92)
        if len(matches) != 1:
            raise ObservationError("Transmit currency icon is missing or ambiguous")
        boundaries.append(tuple(matches[0].area))
    coin, token = boundaries
    end = OCR_TRANSMIT_TOKENS.area[2]
    if (token[0] - coin[2] < 15 or abs(coin[1] - token[1]) > 3
            or end - token[2] < 15):
        raise ObservationError("Transmit currency number boundaries are clipped")
    areas = ((coin[2] + 5, coin[1] - 5, token[0] - 4, coin[3] + 6),
             (token[2] + 5, token[1], end, token[3] + 1))
    crops = [image[y1:y2, x1:x2] for x1, y1, x2, y2 in areas]
    values = Ocr(OCR_TRANSMIT_COINS, lang="en", name="BRAWL_TRANSMIT_QUOTE").ocr_multi_lines(crops)
    quote = parse_quote(values)
    empty = vision.matches(TRANSMIT_EMPTY_CHECK)
    ready = vision.matches(TRANSMIT_CONFIRM, color=True)
    if empty and quote == Resources(0, 0) and not ready:
        return TransmitPanel(vision.preparation_panel(), quote, False, None, observation.game_mode)
    if ready and not empty and (quote.coins > 0 or quote.tokens > 0):
        x1, y1, x2, y2 = SELECTION_AREA
        fingerprint = hashlib.sha256(image[y1:y2, x1:x2].tobytes()).hexdigest()
        return TransmitPanel(vision.preparation_panel(), quote, True, fingerprint, observation.game_mode)
    raise ObservationError("Transmit selection and quote disagree or are obscured")


def capture_selection(image, lang="cn"):
    """Capture the selected batch; this does not select or authorize any hero."""
    panel = read_transmit_panel(image, lang)
    if not panel.selected:
        raise ObservationError("No heroes are selected for transmission")
    return TransmitSelection(panel.header, panel.quote, panel.selection_fingerprint, panel.game_mode)


def _same_context(first, second):
    return (first.round_number, first.level, first.deployed, first.capacity) == (
        second.round_number, second.level, second.deployed, second.capacity,
    )


class BrawlTransmit(ModuleBase):
    CONFIRM_INTERVAL = 3
    CONFIRM_TIMEOUT = 10

    def capture_selection(self):
        return capture_selection(self.device.image)

    def confirm_selected(self, selection: TransmitSelection, skip_first_screenshot=True):
        """Confirm the caller's exact selected batch, then verify its proceeds.

        Args:
            selection: Immutable snapshot from capture_selection, captured only
                after the caller has explicitly chosen the intended heroes.
            skip_first_screenshot: Reuse the caller's fresh first frame.

        Returns:
            TransmitOutcome: CONFIRMED requires a click followed by stable
                preparation with the same round, level and deployed count, and
                both balances increased by exactly the displayed quote. Other
                outcomes retain the final frame for the parent state loop.

        Pages:
            in: transmit selection with an already selected, unchanged batch
            out: stable preparation after verified transmission, a recognized
                interruption, changed selection, or the last timeout frame
        """
        if not isinstance(selection, TransmitSelection):
            raise ValueError("An immutable captured transmit selection is required")
        timeout = Timer(self.CONFIRM_TIMEOUT, count=3).start()
        clicked = False
        returned_to_preparation = False
        before = selection.header
        expected = Resources(before.coins + selection.quote.coins,
                             before.synergy_tokens + selection.quote.tokens)
        while True:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            vision = BrawlVision(self.device.image)
            observation = vision.observe()
            # Positive exits always precede timeout and actions. A tray closing
            # or a bench hero disappearing cannot prove payment: selection
            # itself removes that hero from the bench before confirmation.
            if observation.state == BrawlScreen.PREPARATION:
                current = observation.panel
                if observation.game_mode != selection.game_mode:
                    return TransmitOutcome.SCENE_CHANGED
                if not clicked:
                    return TransmitOutcome.SCENE_CHANGED
                # The preparation anchor proves that the tray closed even if
                # resource digits are still animating. Remember that boundary
                # before waiting for readable balances so a later reopened tray
                # can never cause a second confirmation of this selection.
                returned_to_preparation = True
                if current is not None:
                    if not _same_context(before, current):
                        return TransmitOutcome.SCENE_CHANGED
                    if (current.coins, current.synergy_tokens) == (expected.coins, expected.tokens):
                        logger.info(f"Brawl transmission verified: +{selection.quote}")
                        return TransmitOutcome.CONFIRMED
                    if not (before.coins <= current.coins <= expected.coins
                            and before.synergy_tokens <= current.synergy_tokens <= expected.tokens):
                        return TransmitOutcome.BALANCE_MISMATCH
            elif observation.state == BrawlScreen.TRANSMIT_SELECTION:
                # Once preparation has appeared, a later tray is a new action
                # context. Never repeat a sale merely because its balance took
                # time to refresh, or reopen a tray to seek confirmation.
                if returned_to_preparation:
                    return TransmitOutcome.SCENE_CHANGED
                try:
                    current_selection = capture_selection(self.device.image)
                except ObservationError:
                    if vision.matches(TRANSMIT_EMPTY_CHECK):
                        return TransmitOutcome.SELECTION_CHANGED
                else:
                    if current_selection != selection:
                        return TransmitOutcome.SELECTION_CHANGED
                    if timeout.reached():
                        return TransmitOutcome.TIMEOUT
                    if self.interval_is_reached(TRANSMIT_CONFIRM, interval=self.CONFIRM_INTERVAL):
                        self.device.click(TRANSMIT_CONFIRM)
                        self.interval_reset(TRANSMIT_CONFIRM, interval=self.CONFIRM_INTERVAL)
                        clicked = True
                        continue
            elif observation.state != BrawlScreen.UNKNOWN:
                return TransmitOutcome.SCENE_CHANGED

            if timeout.reached():
                logger.warning("Brawl transmission remains unconfirmed after local timeout")
                return TransmitOutcome.TIMEOUT
