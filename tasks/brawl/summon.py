"""Verify one explicitly requested synergy summon and its actual token debit."""

import re
from dataclasses import dataclass
from enum import Enum

import numpy as np

from module.base.base import ModuleBase
from module.base.timer import Timer
from module.ocr.ocr import Ocr
from tasks.brawl.assets.assets_brawl import HERO_RESULT_CHECK, OCR_ROUND
from tasks.brawl.assets.assets_brawl_actions import SUMMON_LOWER, SUMMON_UPPER
from tasks.brawl.heroes import HeroResult, read_hero_result
from tasks.brawl.observation_cache import (
    PanelObservationCache,
    invalidate_panel_observation_cache,
)
from tasks.brawl.rules import Imprint, Resources, SummonKind, summon_cost
from tasks.brawl.vision import (
    BrawlMode,
    BrawlScreen,
    BrawlVision,
    ObservationError,
    PreparationPanel,
)


@dataclass(frozen=True)
class SummonRequest:
    panel: PreparationPanel
    game_mode: BrawlMode
    kind: SummonKind
    reserve_tokens: int = 0


class SummonOutcome(str, Enum):
    CONFIRMED = "confirmed"
    TIMEOUT = "timeout"
    SCENE_CHANGED = "scene_changed"
    CONTEXT_CHANGED = "context_changed"
    BALANCE_MISMATCH = "balance_mismatch"
    RESULT_UNSUPPORTED = "result_unsupported"


@dataclass(frozen=True)
class SummonReport:
    outcome: SummonOutcome
    hero: HeroResult | None = None
    proceeds: Resources | None = None


def _button(kind):
    if kind == SummonKind.LOWER:
        return SUMMON_LOWER
    if kind == SummonKind.UPPER:
        return SUMMON_UPPER
    raise ValueError("Only captured lower and upper synergy summons are supported")


def capture_summon(image, kind, reserve_tokens=0):
    """Capture a fresh preparation and a caller-selected, affordable draw."""
    kind = SummonKind(kind)
    button = _button(kind)
    if type(reserve_tokens) is not int or reserve_tokens < 0:
        raise ValueError("The token reserve must be a nonnegative integer")
    vision = BrawlVision(image)
    current = vision.observe()
    if (current.state != BrawlScreen.PREPARATION or current.panel is None
            or current.game_mode is None):
        raise ObservationError("A stable preparation is required before paying for a summon")
    if current.panel.synergy_tokens < summon_cost(kind).tokens + reserve_tokens:
        raise ObservationError("The summon would exceed the available token budget")
    if not vision.matches(button, color=True):
        raise ObservationError("The requested summon button is not visibly available")
    return SummonRequest(current.panel, current.game_mode, kind, reserve_tokens)


def _result_balances(vision):
    # The hero overlay covers round and level digits, but leaves both resource
    # numbers visible. Only read this current pair; never infer it from a click.
    crops = [vision.image[y1:y2, x1:x2] for x1, y1, x2, y2 in vision.currency_areas()]
    values = Ocr(OCR_ROUND, lang="en", name="BRAWL_SUMMON_BALANCES").ocr_multi_lines(crops)
    if len(values) != 2 or any(not np.isfinite(score) or score < .8 for _, score in values):
        raise ObservationError("Summon balances have low recognition confidence")
    texts = [re.sub(r"\s+", "", text) for text, _ in values]
    if any(re.fullmatch(r"[0-9]{1,6}", text) is None for text in texts):
        raise ObservationError("Summon balances are incomplete")
    return tuple(map(int, texts))


class _ResultBalanceCache:
    """Reuse measured digits only inside one owned hero-result interaction.

    Hero artwork and the arena animate while the result is visible. They do
    not invalidate unchanged resource glyphs. Every hit still locates both
    currency anchors in the current frame and includes the complete measured
    hero identity, both numeral regions and their locations. No expected debit
    enters this cache. Unknown scenes or unread heroes clear ownership; failed
    reads cannot leave an older balance available for the next frame.
    """

    def __init__(self):
        self.clear()

    def clear(self):
        self._key = self._balances = None

    def read(self, vision, hero, reader=None):
        if hero is None:
            self.clear()
            raise ObservationError("Result balances require a complete current hero")
        try:
            areas = tuple(vision.currency_areas())
            crops = [vision.image[y1:y2, x1:x2] for x1, y1, x2, y2 in areas]
            key = (hero, areas, PanelObservationCache.signature(crops))
            if key == self._key:
                return self._balances
            self.clear()
            balances = (reader or _result_balances)(vision)
        except ObservationError:
            self.clear()
            raise
        self._key, self._balances = key, balances
        return balances


class BrawlSummon(ModuleBase):
    ACTION_INTERVAL = 3
    STEP_TIMEOUT = 15
    ACTION_TIMER = "BrawlSummon"

    def summon_once(self, request, skip_first_screenshot=True):
        """Pay once, read one result and verify its return to preparation.

        Pages:
            in: preparation matching the caller's captured request
            out: verified preparation, interruption or the last unconfirmed frame

        Result prompts are dismissed once, only after this call's payment,
        exact displayed debit and positively recognized continuation footer.
        A free hero prompt at entry has no paid provenance. A full-imprint
        overflow is accepted only after its two-resource refund appears as the
        exact preparation balance increase; the refund is never estimated from
        rarity. Additional different hero results stay visible for the caller.
        No roster is updated.
        """
        if (not isinstance(request, SummonRequest)
                or type(request.reserve_tokens) is not int or request.reserve_tokens < 0):
            raise ValueError("A captured summon request is required")
        button = _button(request.kind)
        cost = summon_cost(request.kind)
        if request.panel.synergy_tokens < cost.tokens + request.reserve_tokens:
            raise ValueError("The request exceeds its token budget")
        before = request.panel
        expected = (before.coins, before.synergy_tokens - cost.tokens)
        timeout = Timer(self.STEP_TIMEOUT).start()
        paid = returned = closed = result_seen = False
        result = None
        proceeds = None
        balance_cache = _ResultBalanceCache()

        def report(outcome):
            return SummonReport(outcome, result, proceeds)

        while True:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()
            image = self.device.image
            vision = BrawlVision(image)
            current = vision.observe()
            if current.state != BrawlScreen.HERO_RESULT:
                balance_cache.clear()
            if current.game_mode is not None and current.game_mode != request.game_mode:
                return report(SummonOutcome.CONTEXT_CHANGED)
            if current.state == BrawlScreen.HERO_RESULT and (not paid or returned):
                return report(SummonOutcome.SCENE_CHANGED)
            if current.state not in (BrawlScreen.PREPARATION, BrawlScreen.HERO_RESULT, BrawlScreen.UNKNOWN):
                return report(SummonOutcome.SCENE_CHANGED)
            # A positively recognized preparation ends this result's ownership,
            # even if its mode or counters are temporarily unreadable. Seeing a
            # result is distinct from reading or closing it: an unread card
            # disappearing must never authorize accepting a debit or a later
            # hero. Repeated result frames cannot repeat our close click.
            if current.state == BrawlScreen.PREPARATION and result_seen:
                returned = True
                if not closed:
                    return report(SummonOutcome.SCENE_CHANGED)
            # Hero artwork may cover the mode title. Only an owned result may
            # inherit the mode from this call's payment; preparation must still
            # independently confirm it before the operation can succeed.
            if current.game_mode is None and not (paid and current.state == BrawlScreen.HERO_RESULT):
                if timeout.reached():
                    return report(SummonOutcome.TIMEOUT)
                continue
            if current.state == BrawlScreen.PREPARATION:
                panel = current.panel
                if panel is not None:
                    if (panel.round_number, panel.level, panel.deployed, panel.capacity) != (
                            before.round_number, before.level, before.deployed, before.capacity):
                        return report(SummonOutcome.CONTEXT_CHANGED)
                    balances = (panel.coins, panel.synergy_tokens)
                    if not paid:
                        if panel != before:
                            return report(SummonOutcome.CONTEXT_CHANGED)
                        if timeout.reached():
                            return report(SummonOutcome.TIMEOUT)
                        if vision.matches(button, color=True):
                            self.device.click(button)
                            invalidate_panel_observation_cache()
                            # This captured request owns exactly one payment.
                            # Its paid latch blocks stale preparation frames;
                            # a cooldown from another action adds no evidence.
                            paid = True
                            timeout.reset()
                            continue
                    elif result is not None and result.is_overflow and closed:
                        # The overflow result says a max-imprint duplicate was
                        # converted, but its tiny reward glyphs are not a safe
                        # OCR target. Measure both credited resources on the
                        # positively confirmed preparation instead. This is an
                        # observed delta from the only unobserved transition we
                        # initiated (one result close), not a rarity estimate.
                        coin_gain = panel.coins - before.coins
                        token_gain = panel.synergy_tokens - expected[1]
                        if coin_gain < 0 or token_gain < 0:
                            return report(SummonOutcome.BALANCE_MISMATCH)
                        if coin_gain + token_gain == 0:
                            # The refund can be credited a frame after the
                            # preparation appears. Keep waiting for a positive
                            # measured gain; a persistent zero gain times out
                            # instead of being accepted as a normal summon.
                            if timeout.reached():
                                return report(SummonOutcome.TIMEOUT)
                            continue
                        proceeds = Resources(coin_gain, token_gain)
                        return report(SummonOutcome.CONFIRMED)
                    elif balances == expected and closed:
                        return report(SummonOutcome.CONFIRMED)
                    elif (panel.coins != before.coins
                          or not expected[1] <= panel.synergy_tokens <= before.synergy_tokens):
                        return report(SummonOutcome.BALANCE_MISMATCH)
            elif current.state == BrawlScreen.HERO_RESULT:
                if not paid or returned:
                    return report(SummonOutcome.SCENE_CHANGED)
                result_seen = True
                hero = read_hero_result(image)
                if hero is not None:
                    if (hero.previous_imprint == hero.imprint == Imprint.SSS
                            and not hero.is_overflow):
                        return report(SummonOutcome.RESULT_UNSUPPORTED)
                    if result is not None and hero != result:
                        return report(SummonOutcome.RESULT_UNSUPPORTED)
                    try:
                        balances = balance_cache.read(vision, hero)
                    except ObservationError:
                        balances = None
                    if balances is not None:
                        if (balances[0] != before.coins
                                or not expected[1] <= balances[1] <= before.synergy_tokens):
                            return report(SummonOutcome.BALANCE_MISMATCH)
                        if balances == expected:
                            result = hero
                            if timeout.reached():
                                return report(SummonOutcome.TIMEOUT)
                            if not closed and vision.matches(HERO_RESULT_CHECK):
                                self.device.click(HERO_RESULT_CHECK)
                                # Positive result identity and actual debit
                                # authorize this different action immediately.
                                # Only the local latch prevents repeat closes.
                                closed = True
                                timeout.reset()
                                continue
                else:
                    balance_cache.clear()
            elif current.state != BrawlScreen.UNKNOWN:
                return report(SummonOutcome.SCENE_CHANGED)
            if timeout.reached():
                return report(SummonOutcome.TIMEOUT)
