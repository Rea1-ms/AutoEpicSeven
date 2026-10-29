"""Verify one ordinary ten-coin summon with bounded recovery for missed taps."""

from dataclasses import dataclass

from module.base.base import ModuleBase
from module.base.timer import Timer
from tasks.base.assets.assets_base_popup import AD_BUFF_X_CLOSE, TOUCH_TO_CLOSE
from tasks.brawl.assets.assets_brawl import HERO_RESULT_CHECK
from tasks.brawl.assets.assets_brawl_actions import SUMMON_NORMAL
from tasks.brawl.heroes import read_hero_result
from tasks.brawl.observation_cache import invalidate_panel_observation_cache
from tasks.brawl.rules import Imprint, Resources, SummonKind, summon_cost
from tasks.brawl.summon import (
    SummonOutcome,
    SummonReport,
    _result_balances,
    _ResultBalanceCache,
)
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
    # A tap is only a request.  A missed tap must be retried, but an unlimited
    # retry loop could spend more than one draw if the game is merely slow to
    # update.  Keep the retry budget deliberately small and require the button
    # to remain visible on every retry.
    MAX_PAYMENT_ATTEMPTS = 3
    ACTION_TIMER = "BrawlBenchScan"

    def summon_once(self, request, skip_first_screenshot=True):
        """Request one summon and verify its result, retrying lost taps finitely.

        Pages:
            in: stable preparation matching a captured ordinary summon request
            out: preparation with the exact debit, or the unconfirmed last frame

        A tap is only a request: unchanged preparation frames may trigger at
        most ``MAX_PAYMENT_ATTEMPTS`` taps at ``ACTION_INTERVAL`` spacing.  A
        result page or exact preparation debit permanently disables retries.
        Neither a changed balance nor a new hero card alone proves the purchase.
        The result must be read after this call's payment, the visible balance
        must agree, and a positively recognized footer can be clicked once.
        Repeated result frames never repeat that close. The common
        ``AD_BUFF_X_CLOSE`` popup is handled before page recognition. A
        full-imprint overflow is accepted only after its exact coin and token
        credit is measured on the returned preparation; the caller still
        reconciles the hero against a fresh complete bench scan before deciding
        whether another draw is needed.
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
        click_attempted = debit_confirmed = returned = closed = result_seen = False
        payment_progress_seen = False
        payment_attempts = 0
        retry_timer = Timer(self.ACTION_INTERVAL).start()
        ad_close_timer = Timer(2).clear()
        result = None
        result_pixels = None
        proceeds = None
        balance_cache = _ResultBalanceCache()

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
            # This popup can cover the summon page immediately after a click.
            # Handle it explicitly instead of letting the generic touch-to-close
            # path dismiss a result before its OCR and debit are verified.
            # Match visibility independently from click throttling: an ad
            # inside its close interval still blocks the underlying controls.
            if AD_BUFF_X_CLOSE.match_template(image, similarity=0.85):
                if timeout.reached():
                    return report(SummonOutcome.TIMEOUT)
                if ad_close_timer.reached():
                    self.device.click(AD_BUFF_X_CLOSE)
                    invalidate_panel_observation_cache()
                    ad_close_timer.reset()
                continue

            # A captured quote authorizes this action, while click_attempted and
            # debit_confirmed deliberately have different meanings.  A click
            # can be lost; only an observed debit/result makes it irreversible.
            observed = vision.observe()
            if observed.state != BrawlScreen.HERO_RESULT:
                result_pixels = None
                balance_cache.clear()
            if observed.game_mode is not None and observed.game_mode != request.game_mode:
                return report(SummonOutcome.CONTEXT_CHANGED)
            if observed.state == BrawlScreen.HERO_RESULT and (not click_attempted or returned):
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
            if observed.game_mode is None and not (click_attempted and observed.state == BrawlScreen.HERO_RESULT):
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
                    if balances != (before.coins, before.synergy_tokens):
                        # An intermediate currency frame may be a debit in
                        # progress. If OCR later returns to the
                        # old digits, do not interpret that stale frame as a
                        # missed tap and risk a second payment.
                        payment_progress_seen = True
                    if not click_attempted:
                        if panel != before:
                            return report(SummonOutcome.CONTEXT_CHANGED)
                        if timeout.reached():
                            return report(SummonOutcome.TIMEOUT)
                        if vision.matches(SUMMON_NORMAL, color=True):
                            self.device.click(SUMMON_NORMAL)
                            invalidate_panel_observation_cache()
                            click_attempted = True
                            payment_attempts = 1
                            retry_timer.reset()
                            continue
                    elif (not debit_confirmed and not payment_progress_seen
                          and panel == before):
                        # The preparation is unchanged, so no debit has been
                        # observed yet.  Retry only after the action interval,
                        # while the button remains positively recognizable.
                        if payment_attempts < self.MAX_PAYMENT_ATTEMPTS and retry_timer.reached():
                            # Do not send a final tap after the overall step
                            # deadline.  Retries share this deadline; they do
                            # not extend it as the old payment latch did.
                            if timeout.reached():
                                return report(SummonOutcome.TIMEOUT)
                            if not vision.matches(SUMMON_NORMAL, color=True):
                                continue
                            self.device.click(SUMMON_NORMAL)
                            invalidate_panel_observation_cache()
                            payment_attempts += 1
                            retry_timer.reset()
                            continue
                    if balances == expected:
                        # An exact preparation debit is enough to stop payment
                        # retries, but not enough to confirm the summon.  The
                        # result page and its verified close are still required.
                        debit_confirmed = True
                    if debit_confirmed and result is not None and result.is_overflow and closed:
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
                if not click_attempted or returned:
                    return report(SummonOutcome.SCENE_CHANGED)
                result_seen = True
                # Reuse a complete identity only while its current name/badge
                # region is unchanged. Resource evidence has its own glyph
                # cache: animated artwork is not a reason to OCR balances.
                # Neither cache replaces current page/footer validation.
                pixels = image[137:610, 700:1065].tobytes()
                if result is not None and pixels == result_pixels:
                    hero = result
                else:
                    hero = read_hero_result(image)
                balances = None
                if hero is not None:
                    try:
                        balances = balance_cache.read(vision, hero, _result_balances)
                    except ObservationError:
                        pass
                else:
                    balance_cache.clear()
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
                            debit_confirmed = True
                            result = hero
                            result_pixels = pixels
                            if timeout.reached():
                                return report(SummonOutcome.TIMEOUT)
                            if not closed:
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
                                    closed = True
                                    # The payment deadline covers the request
                                    # and retry window. Once identity, debit,
                                    # and the footer are all verified, allow a
                                    # fresh short window for the game to render
                                    # the confirmed preparation page.
                                    timeout.reset()
                                    continue
            elif observed.state != BrawlScreen.UNKNOWN:
                return report(SummonOutcome.SCENE_CHANGED)
            if timeout.reached():
                return report(SummonOutcome.TIMEOUT)
