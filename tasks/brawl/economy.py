"""Prepare each tracked normal-mode round using verified transactions."""

from dataclasses import replace

from module.base.base import ModuleBase
from module.logger import logger
from tasks.brawl.auto_transfer import BrawlAutoTransfer
from tasks.brawl.economy_policy import EconomyAction, plan_economy
from tasks.brawl.formation import FormationOutcome
from tasks.brawl.normal_summon import BrawlNormalSummon, capture_normal_summon
from tasks.brawl.rules import (
    MAX_LEVEL,
    Imprint,
    Resources,
    RuleInputError,
    SummonKind,
    team_capacity,
    upgrade_cost,
)
from tasks.brawl.summon import BrawlSummon, SummonOutcome, capture_summon
from tasks.brawl.upgrade import BrawlUpgrade, UpgradeOutcome, UpgradeRequest
from tasks.brawl.vision import BrawlMode, BrawlScreen, BrawlVision, ObservationError


class BrawlEconomy(ModuleBase):
    ACTION_TIMER = "BrawlBenchScan"
    ACTION_INTERVAL = 3

    def _module(self, factory):
        module = factory(self.config, self.device)
        module.interval_timer = self.interval_timer
        module.ACTION_TIMER = self.ACTION_TIMER
        return module

    def _observe(self, state):
        observation = BrawlVision(self.device.image).observe()
        if (observation.state != BrawlScreen.PREPARATION or observation.panel != state.panel
                or observation.game_mode != state.game_mode):
            raise ObservationError("Economic preparation no longer matches the verified roster")

    @staticmethod
    def _ready(result):
        if result.outcome not in (FormationOutcome.READY, FormationOutcome.COMPLETE):
            raise ObservationError(f"Economic roster verification failed: {result.reason}")
        return result

    @staticmethod
    def _paid_result(before, report):
        if report.outcome != SummonOutcome.CONFIRMED or report.hero is None:
            raise ObservationError(f"The paid summon remains unconfirmed: {report.outcome.value}")
        known = next((hero for hero in before.heroes if hero.name == report.hero.name), None)
        if report.hero.is_overflow:
            # A max-imprint duplicate keeps the owned hero unchanged. It must
            # belong to a hero already confirmed at SSS; a new or lower-imprint
            # identity cannot justify an automatic transfer result.
            if known is None or known.imprint != Imprint.SSS:
                raise ObservationError("A max-imprint refund does not match the tracked roster")
        elif known is not None and (report.hero.previous_imprint != known.imprint
                                    or report.hero.imprint == known.imprint):
            raise ObservationError("A paid draw did not advance the currently owned imprint")
        return (report.hero,)

    def _confirm(self, formation, before, panel, *, hero_results=(), removed=()):
        return self._ready(formation.confirm_economy(
            before, panel, hero_results=hero_results, removed=removed,
            skip_first_screenshot=True))

    def _quotes(self, formation, transfer, names):
        """Read prices without selling; prove every cancellation restored the roster."""
        base = formation.roster
        quotes = {}
        for name in names:
            scan = formation.verified_scan
            before = formation.begin_economy()
            preview = transfer.preview((name,), scan, skip_first_screenshot=True)
            if (preview.names != (name,) or preview.header != before.panel
                    or preview.game_mode != before.game_mode):
                raise ObservationError("The transfer preview does not describe the requested hero")
            report = transfer.finish(preview, commit=False, skip_first_screenshot=True)
            if (report.committed or report.hero_results or report.panel != before.panel
                    or report.game_mode != before.game_mode or report.names != (name,)):
                raise ObservationError("The transfer quote was not cancelled without changes")
            self._confirm(formation, before, before.panel)
            if formation.roster != base:
                raise ObservationError("The roster changed while gathering transfer prices")
            quotes[name] = preview.quote
        return quotes, formation.roster.revision

    def prepare(self, formation, bonuses, skip_first_screenshot=True):
        """Spend, reconcile and arrange until the current normal round is ready.

        Pages: verified tracked preparation in; verified arranged preparation
        out. Each child loop must return to that page before the next decision.
        An interrupted transaction invalidates the session; it is never retried
        as a fresh payment. Battle rewards have already been reconciled by the
        caller and are not reapplied here.
        """
        if not formation.valid or formation.roster.game_mode != BrawlMode.NORMAL:
            return formation.invalidate("Round economy requires a tracked normal-mode roster")
        try:
            if not skip_first_screenshot:
                self.device.screenshot()
            self._observe(formation.roster)
            result = self._ready(formation.arrange(bonuses, skip_first_screenshot=True))
            upgrade = self._module(BrawlUpgrade)
            normal = self._module(BrawlNormalSummon)
            synergy = self._module(BrawlSummon)
            transfer = self._module(BrawlAutoTransfer)
            # These budgets persist across prepare calls and failed rounds. A
            # newly granted hero or a repeated call cannot renew the five-sale
            # allowance for the same next level. Only a verified level change
            # resets it. At max level, allow one free-slot sale per round.
            level = formation.roster.panel.level
            if getattr(self, "_funding_level", None) != level:
                self._funding_level, self._transferred = level, 0
            round_number = formation.roster.panel.round_number
            if getattr(self, "_slot_round", None) != round_number:
                self._slot_round, self._slot_freed = round_number, False
            quotes = quote_revision = None
            while True:
                before = formation.roster
                self._observe(before)
                plan = plan_economy(before, bonuses, observed_quotes=quotes, quote_revision=quote_revision,
                                    transfer_budget=(5 if before.panel.level == MAX_LEVEL
                                                     else 5 - self._transferred),
                                    max_level_slot_freed=self._slot_freed)
                logger.info(f"Brawl economy: round {before.panel.round_number}, "
                            f"level {before.panel.level}, action={plan.action.value}, reason={plan.reason}")
                if plan.revision != before.revision:
                    raise ObservationError("The economic decision uses a stale roster")
                if plan.action == EconomyAction.SAVE:
                    return result
                if plan.action == EconomyAction.UNSUPPORTED:
                    raise ObservationError("The current mode has no confirmed economic policy")
                if plan.action == EconomyAction.QUOTE_TRANSFER:
                    quotes, quote_revision = self._quotes(formation, transfer, plan.targets)
                    continue
                if plan.action == EconomyAction.UPGRADE:
                    formation.begin_economy()
                    outcome = upgrade.upgrade_once(UpgradeRequest(before.panel, before.game_mode),
                                                   skip_first_screenshot=True)
                    if outcome != UpgradeOutcome.CONFIRMED:
                        raise ObservationError(f"The round upgrade remains unconfirmed: {outcome.value}")
                    panel = replace(before.panel, level=before.panel.level + 1,
                                    capacity=team_capacity(before.panel.level + 1),
                                    coins=before.panel.coins - upgrade_cost(before.panel.level))
                    self._confirm(formation, before, panel)
                    self._funding_level, self._transferred = panel.level, 0
                elif plan.action in (EconomyAction.SUMMON_NORMAL, EconomyAction.SUMMON_UPPER):
                    upper = plan.action == EconomyAction.SUMMON_UPPER
                    request = (capture_summon(self.device.image, SummonKind.UPPER) if upper
                               else capture_normal_summon(self.device.image))
                    if request.panel != before.panel or request.game_mode != before.game_mode:
                        raise ObservationError("The summon request differs from the tracked preparation")
                    formation.begin_economy()
                    report = (synergy if upper else normal).summon_once(request, skip_first_screenshot=True)
                    heroes = self._paid_result(before, report)
                    proceeds = report.proceeds or Resources(0, 0)
                    panel = (replace(before.panel,
                                     coins=before.panel.coins + proceeds.coins,
                                     synergy_tokens=before.panel.synergy_tokens - 40 + proceeds.tokens)
                             if upper
                             else replace(before.panel,
                                          coins=before.panel.coins - 10 + proceeds.coins,
                                          synergy_tokens=before.panel.synergy_tokens + proceeds.tokens))
                    self._confirm(formation, before, panel, hero_results=heroes)
                elif plan.action == EconomyAction.TRANSFER:
                    scan = formation.verified_scan
                    formation.begin_economy()
                    preview = transfer.preview(plan.targets, scan, skip_first_screenshot=True)
                    if (preview.names != plan.targets or preview.header != before.panel
                            or preview.game_mode != before.game_mode or preview.quote != plan.expected_reward):
                        # A changed quote cannot authorize a cheaper partial
                        # funding sale. Cancel the reversible selection before
                        # reporting the mismatch; do not execute the old plan.
                        transfer.finish(preview, commit=False, skip_first_screenshot=True)
                        raise ObservationError("The final selected batch no longer matches its observed prices")
                    report = transfer.finish(preview, commit=True, skip_first_screenshot=True)
                    panel = replace(before.panel, coins=before.panel.coins + preview.quote.coins,
                                    synergy_tokens=before.panel.synergy_tokens + preview.quote.tokens)
                    if (not report.committed or report.names != plan.targets or report.quote != preview.quote
                            or report.panel != panel or report.game_mode != before.game_mode):
                        raise ObservationError("The confirmed transfer differs from its selected batch or proceeds")
                    self._confirm(formation, before, panel, hero_results=report.hero_results, removed=plan.targets)
                    if before.panel.level == MAX_LEVEL:
                        self._slot_freed = True
                    else:
                        self._transferred += len(plan.targets)
                else:
                    raise ObservationError("The economic planner returned an unsupported action")
                # Every real action changes the context. Discard all prices,
                # even when a gift happens to recreate a sold name and imprint.
                quotes = quote_revision = None
                result = self._ready(formation.arrange(bonuses, skip_first_screenshot=True))
        except (ObservationError, RuleInputError) as exc:
            logger.warning(f"Brawl economic preparation stopped: {exc}")
            return formation.invalidate(str(exc))
        except Exception:
            formation.invalidate("Economic preparation interrupted before verification")
            raise
