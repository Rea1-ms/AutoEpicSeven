"""Read supported brawl screenshots; incomplete observations stay unknown."""

import re
from dataclasses import dataclass
from enum import Enum

import numpy as np

from module.config import server
from module.ocr.ocr import Ocr
from tasks.brawl.assets.assets_brawl import (
    BRAWL_CHALLENGE_CHECK,
    BRAWL_CHECK,
    COIN_ICON,
    COMBAT_CHECK,
    CURRENCY_TOOLTIP_CHECK,
    FORMATION_CHECK,
    FORMATION_GRID_CHECK,
    GIVE_UP_CHECK,
    HERO_OBTAIN_CHECK,
    HERO_RESULT_CHECK,
    HERO_UNLOCK_CHECK,
    INCOME_TOOLTIP_CHECK,
    LEVEL_STABLE_CHECK,
    LEVEL_UP_CHECK,
    LOBBY_CHECK,
    LOBBY_EMPTY_CHECK,
    OCR_DEPLOYMENT,
    OCR_LEVEL,
    OCR_ROUND,
    PREPARATION_CHECK,
    PROBABILITY_CHECK,
    RANK_UP_CHECK,
    RESOURCE_INFO_CHECK,
    ROUND_CHECK,
    ROUND_CLEAR_CHECK,
    ROUND_FAILED_CHECK,
    RUN_DEFEAT_CHECK,
    RUN_RETURN_CHECK,
    RUN_VICTORY_CHECK,
    SPEED_ONE_CHECK,
    SPEED_TWO_CHECK,
    TOKEN_ICON,
    TRANSMIT_CHECK,
)
from tasks.brawl.rules import TOTAL_ROUNDS, team_capacity

# The same continuation text overlays different hero artwork. Keep its lower
# threshold local to this footer; page identities and resources still use 0.9.
HERO_RESULT_SIMILARITY = 0.8


class BrawlScreen(str, Enum):
    PREPARATION = "preparation"
    LEVEL_UP = "level_up"
    HERO_RESULT = "hero_result"
    HERO_UNLOCK = "hero_unlock"
    RANK_UP = "rank_up"
    COMBAT = "combat"
    ROUND_CLEAR = "round_clear"
    ROUND_FAILED = "round_failed"
    FORMATION_SELECTION = "formation_selection"
    RUN_VICTORY = "run_victory"
    RUN_DEFEAT = "run_defeat"
    TRANSMIT_SELECTION = "transmit_selection"
    PROBABILITY = "probability"
    GIVE_UP_CONFIRM = "give_up_confirm"
    LOBBY = "lobby"
    UNKNOWN = "unknown"


class BrawlMode(str, Enum):
    NORMAL = "normal"
    CHALLENGE = "challenge"


class ObservationError(ValueError):
    """A supported frame has unread or contradictory UI fields."""


@dataclass(frozen=True)
class PreparationPanel:
    round_number: int
    level: int
    deployed: int
    capacity: int
    coins: int
    synergy_tokens: int


@dataclass(frozen=True)
class BrawlObservation:
    state: BrawlScreen
    panel: PreparationPanel | None = None
    reason: str = ""
    game_mode: BrawlMode | None = None
    speed: int | None = None


def parse_panel(values):
    """Reject low confidence and inconsistent fields rather than repair numbers."""
    if len(values) != 5:
        raise ObservationError("Incomplete preparation fields")
    if any(not np.isfinite(score) or score < 0.8 for _, score in values):
        raise ObservationError("Preparation text has low recognition confidence")
    round_text, level_text, count_text, coins_text, tokens_text = (
        re.sub(r"\s+", "", text) for text, _ in values
    )
    level_match = re.fullmatch(r"LV[.．]?(MAX|[1-9])", level_text.upper())
    count_match = re.fullmatch(r"([0-9]{1,2})/([0-9]{1,2})", count_text)
    if (level_match is None or count_match is None
            or re.fullmatch(r"[0-9]{1,2}", round_text) is None
            or re.fullmatch(r"[0-9]{1,6}", coins_text) is None
            or re.fullmatch(r"[0-9]{1,6}", tokens_text) is None):
        raise ObservationError("Unrecognized preparation field format")
    level = 9 if level_match[1] == "MAX" else int(level_match[1])
    round_number = int(round_text)
    deployed, capacity = map(int, count_match.groups())
    if not 1 <= round_number <= TOTAL_ROUNDS or capacity != team_capacity(level) or not 0 <= deployed <= capacity:
        raise ObservationError("Preparation level, round and deployment count disagree")
    return PreparationPanel(round_number, level, deployed, capacity, int(coins_text), int(tokens_text))


class BrawlVision:
    def __init__(self, image, lang="cn"):
        if server.lang != "global_cn" or lang not in ("cn", "auto", None, ""):
            raise ValueError("Brawl recognition currently supports international Chinese only")
        if not isinstance(image, np.ndarray) or image.shape != (720, 1280, 3) or image.dtype != np.uint8:
            raise ValueError("Brawl requires a 1280x720 RGB uint8 screenshot")
        self.image = image
        self.lang = "cn"

    def matches(self, button, *, color=False):
        similarity = HERO_RESULT_SIMILARITY if button is HERO_RESULT_CHECK else 0.9
        if color:
            return button.match_template_color(self.image, similarity=similarity, threshold=20)
        return button.match_template(self.image, similarity=similarity)

    def currency_areas(self):
        """Locate both icons in this frame; never reuse a previous match offset.

        Balance text changes the horizontal placement of the whole resource bar.
        Search temporarily within the two original icon anchors, then immediately
        copy independent match rectangles and restore each shared wrapper. Missing
        or ambiguous icons invalidate the pair, including after a successful frame.
        """
        boundaries = []
        left = COIN_ICON.area[0] - 50
        right = TOKEN_ICON.area[2] + 25
        top = COIN_ICON.area[1] - 3
        bottom = COIN_ICON.area[3] + 3
        for icon in (COIN_ICON, TOKEN_ICON):
            original = icon.search
            try:
                icon.load_search((left, top, right, bottom))
                matches = icon.match_multi_template(self.image, similarity=0.92)
                if len(matches) != 1:
                    raise ObservationError("Currency icon is missing or ambiguous")
                # Match areas describe this frame's detection. Click rectangles
                # can carry a shared wrapper's earlier offset and are not valid
                # OCR anchors when another caller previously matched the icon.
                boundaries.append(tuple(matches[0].area))
            finally:
                icon.load_search(original)
        coin, token = boundaries
        if not coin[2] < token[0] or abs(coin[1] - token[1]) > 3:
            raise ObservationError("Currency icons are out of order")
        if not self.matches(RESOURCE_INFO_CHECK):
            raise ObservationError("Currency information boundary is missing")
        end = RESOURCE_INFO_CHECK.button[0] - 5
        if not token[2] + 3 < end or token[0] - coin[2] < 15:
            raise ObservationError("Currency number areas are clipped")
        return ((coin[2] + 3, top, token[0] - 4, bottom),
                (token[2] + 3, top, end, bottom))

    def preparation_panel(self):
        areas = [OCR_ROUND.area, OCR_LEVEL.area, OCR_DEPLOYMENT.area, *self.currency_areas()]
        regions = [self.image[y1:y2, x1:x2] for x1, y1, x2, y2 in areas]
        # These five fields contain Latin level labels and digits only. The
        # numeric/English model reads the isolated zero as a digit instead of
        # Chinese punctuation; never replace an OCR punctuation mark with zero.
        values = Ocr(OCR_ROUND, lang="en", name="BRAWL_PANEL").ocr_multi_lines(regions)
        return parse_panel(values)

    def observe(self, *, read_panel=True):
        modes = [mode for mode, button in (
            (BrawlMode.NORMAL, BRAWL_CHECK), (BrawlMode.CHALLENGE, BRAWL_CHALLENGE_CHECK),
        ) if self.matches(button)]
        mode = modes[0] if len(modes) == 1 else None

        def observed(state, panel=None, reason=""):
            speed = None
            if state == BrawlScreen.COMBAT or (state == BrawlScreen.PREPARATION and panel is not None):
                one = self.matches(SPEED_ONE_CHECK, color=True)
                two = self.matches(SPEED_TWO_CHECK, color=True)
                if one != two:
                    speed = 1 if one else 2
            return BrawlObservation(state, panel, reason, mode, speed)

        # Overlays precede their underlying page. A visible green start button
        # behind a translucent explanation is not a usable preparation screen.
        # Rank rewards reuse the summon prompt and can cover the final result;
        # classify them first and never infer completion through that overlay.
        for state, button in (
            (BrawlScreen.RANK_UP, RANK_UP_CHECK),
            (BrawlScreen.HERO_UNLOCK, HERO_UNLOCK_CHECK),
            (BrawlScreen.GIVE_UP_CONFIRM, GIVE_UP_CHECK),
            (BrawlScreen.LEVEL_UP, LEVEL_UP_CHECK),
            (BrawlScreen.HERO_RESULT, HERO_OBTAIN_CHECK),
            (BrawlScreen.HERO_RESULT, HERO_RESULT_CHECK),
            (BrawlScreen.PROBABILITY, PROBABILITY_CHECK),
            (BrawlScreen.TRANSMIT_SELECTION, TRANSMIT_CHECK),
            (BrawlScreen.ROUND_CLEAR, ROUND_CLEAR_CHECK),
            (BrawlScreen.ROUND_FAILED, ROUND_FAILED_CHECK),
        ):
            if self.matches(button):
                return observed(state)
        if self.matches(RUN_RETURN_CHECK, color=True):
            victory = self.matches(RUN_VICTORY_CHECK)
            defeat = self.matches(RUN_DEFEAT_CHECK)
            if victory != defeat:
                return observed(BrawlScreen.RUN_VICTORY if victory else BrawlScreen.RUN_DEFEAT)
        for button in (CURRENCY_TOOLTIP_CHECK, INCOME_TOOLTIP_CHECK):
            if self.matches(button):
                return observed(BrawlScreen.UNKNOWN, reason="Tooltip is open")
        if self.matches(FORMATION_CHECK) or self.matches(FORMATION_GRID_CHECK, color=True):
            return observed(BrawlScreen.FORMATION_SELECTION)
        if self.matches(LOBBY_CHECK, color=True) or self.matches(LOBBY_EMPTY_CHECK, color=True):
            return observed(BrawlScreen.LOBBY)
        if mode is None:
            return observed(BrawlScreen.UNKNOWN, reason="Brawl identity is missing, ambiguous or obscured")
        if self.matches(COMBAT_CHECK):
            return observed(BrawlScreen.COMBAT)
        if self.matches(PREPARATION_CHECK, color=True) and self.matches(ROUND_CHECK):
            if not self.matches(LEVEL_STABLE_CHECK, color=True):
                return observed(BrawlScreen.PREPARATION, reason="Level label is transitioning or obscured")
            if not read_panel:
                return observed(BrawlScreen.PREPARATION)
            try:
                return observed(BrawlScreen.PREPARATION, self.preparation_panel())
            except ObservationError as exc:
                return observed(BrawlScreen.PREPARATION, reason=str(exc))
        return observed(BrawlScreen.UNKNOWN, reason="No supported scene anchor")
