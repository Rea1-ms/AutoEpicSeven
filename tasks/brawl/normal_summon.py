"""Verify one ordinary ten-coin summon without changing synergy summon contracts."""

from dataclasses import dataclass

from module.base.base import ModuleBase
from module.base.timer import Timer
from tasks.base.assets.assets_base_popup import TOUCH_TO_CLOSE
from tasks.brawl.assets.assets_brawl import HERO_RESULT_CHECK
from tasks.brawl.assets.assets_brawl_actions import SUMMON_NORMAL
from tasks.brawl.heroes import read_hero_result
from tasks.brawl.rules import Imprint, Resources, SummonKind, summon_cost
from tasks.brawl.summon import SummonOutcome, SummonReport, _result_balances
from tasks.brawl.vision import (
    BrawlMode,
    BrawlScreen,
    BrawlVision,
    ObservationError,
    PreparationPanel,
)


@dataclass(frozen=True)
class NormalSummonRequest:
    panel: PreparationPanel
    game_mode: BrawlMode
    reserve_coins: int = 0


def capture_normal_summon(image, reserve_coins=0):
    """Capture the current quote; the caller must establish room on the bench."""
    if type(reserve_coins) is not int or reserve_coins < 0:
        raise ValueError("The coin reserve must be a nonnegative integer")
    vision = BrawlVision(image)
    observed = vision.observe()
    if (observed.state != BrawlScreen.PREPARATION or observed.panel is None
            or observed.game_mode is None):
        raise ObservationError("A stable preparation is required before an ordinary summon")
    if observed.panel.coins < summon_cost(SummonKind.NORMAL).coins + reserve_coins:
        raise ObservationError("The ordinary summon would exceed the available coin budget")
    if not vision.matches(SUMMON_NORMAL, color=True):
        raise ObservationError("The ordinary summon button is not visibly available")
    return NormalSummonRequest(observed.panel, observed.game_mode, reserve_coins)


class BrawlNormalSummon(ModuleBase):
    ACTION_INTERVAL = 3
    STEP_TIMEOUT = 15
    ACTION_TIMER = "BrawlBenchScan"

    def summon_once(self, request, skip_first_screenshot=True):
        """Pay once and return only after reading, closing and verifying the result.

        Pages:
            in: stable preparation matching a captured ordinary summon request
            out: preparation with the exact debit, or the unconfirmed last frame

        Neither a changed balance nor a new hero card alone proves the purchase.
        The result must be read after this call's payment, the visible balance
        must agree, and a positively recognized footer can be clicked once.
        Repeated result frames never repeat that close. A full-imprint overflow
        is accepted only after its exact coin and token credit is measured on
        the returned preparation; the caller still reconciles the hero against
        a fresh complete bench scan before deciding whether another draw is
        needed.
        """
        cost = summon_cost(SummonKind.NORMAL).coins
        if (not isinstance(request, NormalSummonRequest)
                or not isinstance(request.panel, PreparationPanel)
                or not isinstance(request.game_mode, BrawlMode)
                or type(request.reserve_coins) is not int or request.reserve_coins < 0
                or request.panel.coins < cost + request.reserve_coins):
            raise ValueError("A captured, affordable ordinary summon request is required")
        before = request.panel
        expected = (before.coins - cost, before.synergy_tokens)
        timeout = Timer(self.STEP_TIMEOUT).start()
        paid = returned = closed = result_seen = False
        result = None
        result_pixels = None
        proceeds = None

        def report(outcome):
            return SummonReport(outcome, result, proceeds)

        def balances_valid(balances):
            # Resource digits can animate between old and new values; another
            # debit, token change or refund belongs to an unsupported action.
            return (expected[0] <= balances[0] <= before.coins
                    and balances[1] == before.synergy_tokens)

        while True:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()
            image = self.device.image
            vision = BrawlVision(image)
            action_ready = self.interval_is_reached(self.ACTION_TIMER, interval=self.ACTION_INTERVAL)
            # A cooldown cannot authorize payment. Observe scene/mode on every
            # frame, then read the live panel when spending becomes possible.
            # After payment, returned resources remain an immediate exit check.
            observed = vision.observe(read_panel=paid or action_ready)
            if observed.state != BrawlScreen.HERO_RESULT:
                result_pixels = None
            if observed.game_mode is not None and observed.game_mode != request.game_mode:
                return report(SummonOutcome.CONTEXT_CHANGED)
            if observed.state == BrawlScreen.HERO_RESULT and (not paid or returned):
                return report(SummonOutcome.SCENE_CHANGED)
            if observed.state not in (BrawlScreen.PREPARATION, BrawlScreen.HERO_RESULT, BrawlScreen.UNKNOWN):
                return report(SummonOutcome.SCENE_CHANGED)
            # A positive preparation ends result ownership even when its mode
            # is unreadable. Otherwise a later hero could inherit this payment
            # after an occluded return. An unread or unclosed result disappearing
            # is already an interruption, before considering resource digits.
            if observed.state == BrawlScreen.PREPARATION and result_seen:
                returned = True
                if not closed:
                    return report(SummonOutcome.SCENE_CHANGED)
            # A hero's weapon can cover the mode title on an otherwise stable
            # result (the captured Carrot result is such a case). Only our own
            # confirmed payment attempt plus this positive result scene may
            # carry the session through that occlusion. It cannot authorize a
            # preparation, unknown scene or a result already present at entry.
            # Closing still requires the exact debit, complete identity and
            # visible footer below; the returned preparation must read its mode.
            if observed.game_mode is None and not (paid and observed.state == BrawlScreen.HERO_RESULT):
                if timeout.reached():
                    return report(SummonOutcome.TIMEOUT)
                continue
            if observed.state == BrawlScreen.PREPARATION:
                panel = observed.panel
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
                        if vision.matches(SUMMON_NORMAL, color=True) and action_ready:
                            self.device.click(SUMMON_NORMAL)
                            self.interval_reset(self.ACTION_TIMER, interval=self.ACTION_INTERVAL)
                            paid = True
                            timeout.reset()
                            continue
                    elif result is not None and result.is_overflow and closed:
                        # The result page marks an automatic max-imprint
                        # conversion. Its small reward glyphs are not reliable
                        # OCR input, so the actual credit is measured from the
                        # exact post-close preparation delta. This remains a
                        # measured action result, not a value inferred from the
                        # hero's rarity or from the visible reward icons.
                        coin_gain = panel.coins - expected[0]
                        token_gain = panel.synergy_tokens - before.synergy_tokens
                        if coin_gain < 0 or token_gain < 0:
                            return report(SummonOutcome.BALANCE_MISMATCH)
                        if coin_gain + token_gain == 0:
                            # A delayed credit may still arrive on a later
                            # frame. A zero gain that persists to the local
                            # timeout must not be mistaken for a normal draw.
                            if timeout.reached():
                                return report(SummonOutcome.TIMEOUT)
                            continue
                        proceeds = Resources(coin_gain, token_gain)
                        return report(SummonOutcome.CONFIRMED)
                    elif balances == expected and closed:
                        return report(SummonOutcome.CONFIRMED)
                    elif not balances_valid(balances):
                        return report(SummonOutcome.BALANCE_MISMATCH)
            elif observed.state == BrawlScreen.HERO_RESULT:
                if not paid or returned:
                    return report(SummonOutcome.SCENE_CHANGED)
                result_seen = True
                # Reuse only a successfully read identity and exact debit from
                # this owned result, while every relevant pixel is unchanged.
                # The first region includes the whole name, both badges, arrow
                # and overflow marker; the second includes both moving currency
                # icons and balances. Changed/missing fields force fresh reads.
                # Unknown/other scenes clear the snapshot, and the current
                # footer must still match before the single close click.
                pixels = (image[137:610, 700:1065].tobytes(), image[60:120, 1000:1265].tobytes())
                if result is not None and pixels == result_pixels:
                    hero, balances = result, expected
                else:
                    hero = read_hero_result(image)
                    balances = None
                    if hero is not None:
                        try:
                            balances = _result_balances(vision)
                        except ObservationError:
                            pass
                if hero is not None:
                    if (hero.previous_imprint == hero.imprint == Imprint.SSS
                            and not hero.is_overflow):
                        return report(SummonOutcome.RESULT_UNSUPPORTED)
                    if result is not None and hero != result:
                        return report(SummonOutcome.RESULT_UNSUPPORTED)
                    if balances is not None:
                        if not balances_valid(balances):
                            return report(SummonOutcome.BALANCE_MISMATCH)
                        if balances == expected:
                            result = hero
                            result_pixels = pixels
                            if timeout.reached():
                                return report(SummonOutcome.TIMEOUT)
                            if not closed and action_ready:
                                # The shared arrow survives artwork changes
                                # better than the overlaid continuation text.
                                # It is only a close target after this owned
                                # result's identity and exact debit are known;
                                # it never identifies a hero page on its own.
                                close = None
                                if TOUCH_TO_CLOSE.match_template(image, similarity=0.85):
                                    close = TOUCH_TO_CLOSE
                                elif vision.matches(HERO_RESULT_CHECK):
                                    close = HERO_RESULT_CHECK
                                if close is not None:
                                    self.device.click(close)
                                    self.interval_reset(self.ACTION_TIMER, interval=self.ACTION_INTERVAL)
                                    closed = True
                                    timeout.reset()
                                    continue
            elif observed.state != BrawlScreen.UNKNOWN:
                return report(SummonOutcome.SCENE_CHANGED)
            if timeout.reached():
                return report(SummonOutcome.TIMEOUT)
