"""Enter brawl and continue tracked normal runs through verified round boundaries."""

from module.base.base import ModuleBase
from module.config import server
from module.exception import RequestHumanTakeover
from module.logger import logger


def validate_language(config):
    """Reject unsupported explicit languages before constructing a device."""
    if getattr(config, "Emulator_GameLanguage", None) not in (None, "", "auto", "cn"):
        logger.critical("Brawl supports international Simplified Chinese only; check the game language setting")
        raise RequestHumanTakeover


class BrawlTool(ModuleBase):
    def run(self):
        """Enter a run, prepare a new opening or retain an existing lineup, and fight.

        Pages: global navigation page, brawl lobby or preparation in;
        normal saved runs reconstruct their roster, positions and talents, then
        continue to a retained final result. Challenge lineups retain their
        one-battle boundary.
        """
        logger.hr("Brawl entry and verified round progression", level=1)
        validate_language(self.config)
        only_free = getattr(self.config, "Brawl_OnlyFree", True)
        if type(only_free) is not bool:
            logger.critical("The free-only entry setting must be a boolean")
            raise RequestHumanTakeover
        # Device initialization resolves automatic package detection. Reject the
        # resulting asset language before importing any brawl recognition code.
        if server.lang != "global_cn":
            logger.critical("Brawl supports international Simplified Chinese only; check the device and game language")
            raise RequestHumanTakeover

        from tasks.brawl.entry import BrawlEntry
        from tasks.brawl.formation import BrawlFormation, FormationOutcome
        from tasks.brawl.opening import BrawlOpening
        from tasks.brawl.relics import BrawlRelics
        from tasks.brawl.vision import BrawlMode, BrawlScreen, ObservationError

        self.device.screenshot()
        self.entry = BrawlEntry(config=self.config, device=self.device)
        self.entry.interval_timer = self.interval_timer
        self.relics = BrawlRelics(config=self.config, device=self.device)
        self.relics.interval_timer = self.interval_timer
        try:
            bonuses = self.relics.read(skip_first_screenshot=True)
            entered = self.entry.enter(skip_first_screenshot=True, only_free=only_free)
            observation = entered.observation
        except ValueError as exc:
            logger.critical(f"Brawl relics or entry could not be confirmed; stopped on the current screen. Reason: {exc}")
            raise RequestHumanTakeover from exc
        if (observation.state != BrawlScreen.PREPARATION or observation.panel is None
                or observation.game_mode is None):
            logger.critical("A stable brawl preparation is required after entry")
            if observation.reason:
                logger.info(f"Observation reason: {observation.reason}")
            raise RequestHumanTakeover
        if (observation.game_mode == BrawlMode.NORMAL
                and (observation.panel.deployed > 0 or observation.panel.round_number != 1)):
            from tasks.brawl.resume import BrawlResume

            self.formation = BrawlResume(config=self.config, device=self.device)
            self.formation.interval_timer = self.entry.interval_timer
            result = self.formation.recover(class_bonuses=bonuses, skip_first_screenshot=True)
            if result.outcome != FormationOutcome.READY or self.formation.class_bonuses is None:
                self._failed("Saved-run reconstruction could not be confirmed", result)
            return self._run_tracked(result, self.formation.class_bonuses)
        if observation.panel.deployed > 0:
            return self._continue_existing(observation)
        if observation.panel.round_number != 1:
            logger.critical("The saved run has an empty field after round 1; lineup reconstruction is not supported")
            raise RequestHumanTakeover

        self.opening = BrawlOpening(config=self.config, device=self.device)
        self.opening.interval_timer = self.interval_timer
        try:
            observation = self.opening.prepare(skip_first_screenshot=True)
        except ObservationError as exc:
            logger.critical(f"Opening preparation or class talents remain unconfirmed. Reason: {exc}")
            raise RequestHumanTakeover from exc
        # Only this startup's relic visit supplies the profession bonuses. The
        # prepared field and legacy account switches cannot override that read.
        logger.info(f"Using the class talents confirmed on this startup's relic visit: {bonuses}")

        self.formation = BrawlFormation(config=self.config, device=self.device)
        self.formation.interval_timer = self.interval_timer
        logger.info("Scanning all bench hero names and imprints")
        result = self.formation.begin(skip_first_screenshot=True)
        if result.outcome != FormationOutcome.READY:
            self._failed("Complete bench roster could not be confirmed", result)
        if not result.roster.heroes:
            self.formation.invalidate("No acquired heroes are available to arrange")
            logger.critical("The bench is empty; acquire heroes manually before starting on round 1 with an empty field")
            raise RequestHumanTakeover
        logger.info(f"Confirmed {len(result.roster.heroes)} heroes; arranging for the current level and observed talents")
        if result.roster.game_mode == BrawlMode.NORMAL:
            # Normal economy arranges before assessing its first transaction.
            # Hand off the fresh opening scan directly instead of arranging here
            # and immediately scanning/arranging the identical board again.
            return self._run_tracked(result, bonuses)
        result = self.formation.arrange(bonuses, skip_first_screenshot=True)
        if result.outcome != FormationOutcome.COMPLETE:
            self._failed("Formation could not be confirmed", result)
        logger.info(f"Opening formation complete: {len(result.steps)} verified actions")
        return self._run_tracked(result, bonuses)

    def _run_tracked(self, result, bonuses):
        """Continue only while every economy, reward and formation stays verified.

        The child controllers own screenshot-first interaction loops. Each
        returns to a positively confirmed preparation or a retained terminal
        screen before this orchestration loop can hand control to another one.
        There is deliberately no battle/retry count: a same-round loss is a
        valid transition only when the combat controller observed its banner.
        """
        from tasks.brawl.combat import BrawlCombat, RoundOutcome
        from tasks.brawl.formation import FormationOutcome
        from tasks.brawl.vision import BrawlMode

        continuous = result.roster.game_mode == BrawlMode.NORMAL
        if continuous:
            from tasks.brawl.economy import BrawlEconomy

            self.economy = BrawlEconomy(config=self.config, device=self.device)
            self.economy.interval_timer = self.formation.interval_timer
            logger.info("Continuing the tracked normal run until its final result")
        else:
            logger.info("Challenge mode retains the one-battle boundary")
        self.combat = BrawlCombat(config=self.config, device=self.device)
        # The last grid cancellation and the first battle click belong to the
        # same action sequence. A new controller must not erase its cooldown.
        self.combat.interval_timer = self.formation.interval_timer
        while True:
            if continuous:
                try:
                    result = self.economy.prepare(self.formation, bonuses, skip_first_screenshot=True)
                except Exception:
                    self.formation.invalidate("Economy interrupted before verified preparation")
                    raise
                if result.outcome != FormationOutcome.COMPLETE:
                    self._failed("Round economy or formation could not be confirmed", result)
            logger.info(f"Starting battle from round {result.roster.panel.round_number}")
            try:
                battle = self.combat.play_round(result.roster.panel, result.roster.game_mode,
                                                skip_first_screenshot=True)
            except Exception:
                self.formation.invalidate("Battle interrupted before verified preparation")
                raise
            if battle.outcome == RoundOutcome.RUN_ENDED:
                self.formation.invalidate("The run ended; the last formation is no longer actionable")
                logger.info(f"Brawl run ended; stopped on the final result. Reason: {battle.reason}")
                return True
            if battle.outcome != RoundOutcome.NEXT_PREPARATION or battle.panel is None:
                self.formation.invalidate(battle.reason)
                logger.critical(f"Battle progression could not be confirmed; stopped for inspection. Reason: {battle.reason}")
                raise RequestHumanTakeover
            logger.info(f"Preparation returned on round {battle.panel.round_number}; "
                        f"reconciling {len(battle.hero_results)} distinct hero results")
            # Commit each battle's ordered reward evidence exactly once before
            # any economic decision. A new run of prepare sees only that fresh
            # verified roster; it never receives an old battle's rewards again.
            try:
                if continuous:
                    result = self.formation.refresh(hero_results=battle.hero_results, skip_first_screenshot=True)
                else:
                    result = self.formation.arrange(bonuses, hero_results=battle.hero_results,
                                                    skip_first_screenshot=True)
            except Exception:
                self.formation.invalidate("Post-battle reconciliation interrupted before verification")
                raise
            expected = FormationOutcome.READY if continuous else FormationOutcome.COMPLETE
            if result.outcome != expected:
                self._failed("Post-battle roster or formation could not be confirmed", result)
            if continuous and result.roster.panel != battle.panel:
                result = self.formation.invalidate("Preparation changed after the confirmed battle boundary")
                self._failed("Post-battle preparation changed before economic decisions", result)
            for hero in result.roster.heroes:
                position = "bench" if hero.cell is None else f"board row {hero.cell[0] + 1}, column {hero.cell[1] + 1}"
                logger.info(f"Hero: {hero.name}, imprint: {hero.imprint.name}, position: {position}")
            if continuous:
                continue
            logger.info(f"One battle completed; round {result.roster.panel.round_number} formation confirmed "
                        f"with {len(result.steps)} verified actions; stopped on preparation")
            return True

    def _continue_existing(self, observation):
        """Play the saved field unchanged; no historic identity or talent is inferred."""
        from tasks.brawl.combat import BrawlCombat, RoundOutcome

        self.combat = BrawlCombat(config=self.config, device=self.device)
        self.combat.interval_timer = self.interval_timer
        logger.info(f"Continuing saved round {observation.panel.round_number} "
                    f"with {observation.panel.deployed} deployed heroes; retaining the current lineup")
        battle = self.combat.play_round(observation.panel, observation.game_mode,
                                       skip_first_screenshot=True)
        if battle.outcome == RoundOutcome.RUN_ENDED:
            logger.info(f"Saved brawl run ended; stopped on the final result. Reason: {battle.reason}")
            return True
        if battle.outcome != RoundOutcome.NEXT_PREPARATION:
            logger.critical(f"Saved battle progression could not be confirmed. Reason: {battle.reason}")
            raise RequestHumanTakeover
        logger.info(f"Saved battle completed; collected {len(battle.hero_results)} hero results and stopped "
                    f"on round {battle.panel.round_number} preparation. The existing lineup was not rearranged")
        return True

    def _failed(self, message, result):
        logger.critical(f"{message}; stopped for inspection of the current frame")
        logger.info(f"Verified actions: {len(result.steps)}; observation reason: {result.reason}")
        raise RequestHumanTakeover


def run_tool(config_name: str) -> None:
    """Use the manual worker, without scheduler retries or restart requests."""
    from module.config.config import AzurLaneConfig
    from module.device.device import Device

    config = AzurLaneConfig(config_name, task="Brawl")
    validate_language(config)
    device = Device(config=config)
    BrawlTool(config=config, device=device).run()
