"""Spend a new game's opening budget before building its first formation."""

from dataclasses import replace

from module.base.base import ModuleBase
from module.logger import logger
from tasks.brawl.normal_summon import BrawlNormalSummon, capture_normal_summon
from tasks.brawl.roster import begin_roster, reconcile
from tasks.brawl.rules import (
    MAX_LEVEL,
    Imprint,
    Resources,
    RuleInputError,
    SummonKind,
    summon_cost,
    team_capacity,
    upgrade_cost,
)
from tasks.brawl.scanner import BrawlBenchScanner
from tasks.brawl.summon import SummonOutcome
from tasks.brawl.upgrade import BrawlUpgrade, UpgradeOutcome, UpgradeRequest
from tasks.brawl.vision import BrawlScreen, BrawlVision, ObservationError


class BrawlOpening(ModuleBase):
    def _module(self, factory):
        module = factory(self.config, self.device)
        # Retry timers retain their own action identities. A child's positively
        # verified return authorizes the next operation; a previous purchase or
        # scanner click must not impose a delay on a different current state.
        module.interval_timer = self.interval_timer
        return module

    def _observe(self, mode=None, expected=None):
        observed = BrawlVision(self.device.image).observe()
        if (observed.state != BrawlScreen.PREPARATION or observed.panel is None
                or observed.game_mode is None or observed.panel.round_number != 1
                or observed.panel.deployed != 0):
            raise ObservationError("Opening preparation requires round one with an empty field")
        if ((mode is not None and observed.game_mode != mode)
                or (expected is not None and observed.panel != expected)):
            raise ObservationError("Opening preparation changed outside its verified action")
        return observed

    def _scan(self, scanner, observed):
        scan = scanner.scan(skip_first_screenshot=True)
        if scan.panel != observed.panel or scan.game_mode != observed.game_mode:
            raise ObservationError("The opening bench scan changed its preparation context")
        return scan

    def prepare(self, skip_first_screenshot=True):
        """Upgrade first, then buy distinct heroes until the team or budget is full.

        Pages:
            in: stable first-round preparation with nobody deployed
            out: stable first-round preparation, still with nobody deployed

        This deliberately spends on every affordable level before any draw,
        following the user's opening policy. It never estimates new ownership
        from the number of result dialogs: duplicates only change an imprint.
        Every purchase must be confirmed individually and every summon must
        agree with a complete subsequent bench scan. Failure raises while
        retaining the last frame, rather than trying another payment.
        """
        if not skip_first_screenshot:
            self.device.screenshot()
        observed = self._observe()
        mode = observed.game_mode
        upgrade = self._module(BrawlUpgrade)
        scanner = self._module(BrawlBenchScanner)
        summon = self._module(BrawlNormalSummon)

        while observed.panel.level < MAX_LEVEL:
            panel = observed.panel
            cost = upgrade_cost(panel.level)
            if panel.coins < cost:
                break
            logger.info(f"Brawl opening upgrade: level {panel.level}, cost {cost} coins")
            outcome = upgrade.upgrade_once(UpgradeRequest(panel, mode), skip_first_screenshot=True)
            if outcome != UpgradeOutcome.CONFIRMED:
                raise ObservationError(f"Opening upgrade was not confirmed: {outcome.value}")
            expected = replace(panel, level=panel.level + 1,
                               capacity=team_capacity(panel.level + 1), coins=panel.coins - cost)
            observed = self._observe(mode, expected)

        try:
            roster = begin_roster(self._scan(scanner, observed))
            cost = summon_cost(SummonKind.NORMAL).coins
            while len(roster.heroes) < roster.panel.capacity and roster.panel.coins >= cost:
                request = capture_normal_summon(self.device.image)
                if request.panel != roster.panel or request.game_mode != mode:
                    raise ObservationError("The ordinary summon no longer matches the complete bench")
                logger.info(f"Brawl opening summon: heroes {len(roster.heroes)}/{roster.panel.capacity}, "
                            f"coins {roster.panel.coins}")
                report = summon.summon_once(request, skip_first_screenshot=True)
                if report.outcome != SummonOutcome.CONFIRMED or report.hero is None:
                    raise ObservationError(f"Opening summon was not confirmed: {report.outcome.value}")
                proceeds = report.proceeds or Resources(0, 0)
                observed = self._observe(mode, replace(
                    roster.panel,
                    coins=roster.panel.coins - cost + proceeds.coins,
                    synergy_tokens=roster.panel.synergy_tokens + proceeds.tokens,
                ))
                # Complete the read-only bench scan before evaluating the
                # result. It cannot change resources or deploy a hero, and it
                # keeps the diagnostic roster available when a paid duplicate
                # fails its stricter imprint check.
                scan = self._scan(scanner, observed)
                known = next((hero for hero in roster.heroes if hero.name == report.hero.name), None)
                # General reconciliation accepts repeated already-applied
                # results when callers replay a collected reward batch. A new
                # paid draw has a stricter contract: an owned hero must actually
                # advance from its current imprint. An unchanged old result
                # plus a coin debit cannot authorize the next paid draw.
                if report.hero.is_overflow:
                    # The game only converts a duplicate whose owned imprint is
                    # already SSS. It must match the roster and never introduce
                    # a new hero or a lower-imprint identity.
                    if known is None or known.imprint != Imprint.SSS:
                        raise ObservationError("A max-imprint refund does not match the opening roster")
                elif known is not None and (report.hero.previous_imprint != known.imprint
                                            or report.hero.imprint == known.imprint):
                    raise ObservationError("The paid draw did not advance the known hero imprint")
                roster = reconcile(roster, scan, (), (report.hero,))
        except RuleInputError as exc:
            raise ObservationError(f"Opening roster could not be verified: {exc}") from exc
        if not roster.heroes:
            raise ObservationError("The opening budget is exhausted without any verified heroes")
        logger.info(f"Brawl opening ready: level {roster.panel.level}, "
                    f"heroes {len(roster.heroes)}/{roster.panel.capacity}, coins {roster.panel.coins}")
        # Scanning can change the selected portrait but cannot spend resources
        # or deploy anyone. Re-read its final image for the formation handoff.
        return self._observe(mode, roster.panel)
