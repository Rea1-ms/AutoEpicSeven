"""Farm complete dimensional explorations, using one screenshot-driven loop."""

from pathlib import Path
from dataclasses import replace

from module.base.button import ClickButton
from module.base.timer import Timer
import module.config.server as server
from module.exception import RequestHumanTakeover
from module.logger import logger
from tasks.base.ui import UI
from tasks.base.assets.assets_base_popup import NETWORK_ERROR_ABNORMAL, TOUCH_TO_CLOSE
from tasks.dimensional_exploration.assets.assets_dimensional_exploration import (
    BUY_CANCEL, BUY_CONFIRM, BUY_CURRENCY, EVENT_NEXT, EXPLORE_ENTER,
    FAILED_CHECK, LOBBY_CONTINUE, LOBBY_START, LOOT_CHECK, MANUAL_TARGET, NODE_ENTER,
    REWARD_CLOSE, ROOM_DONE, ROOM_LEAVE, SETTLEMENT_EMPTY,
    TITLE_ENTER, VICTORY_CONTINUE, BATTLE_START,
    OCR_PREVIEW_TITLE, OCR_INITIAL_RECRUITMENT, HERO_CONFIRM_ACTIVE,
    OCR_EVENT_LOOT, OCR_BUY_NAME, OCR_BUY_PRICE, SHOP_OFFER,
    SUPPLY_DONE_AREA, SUPPLY_LOOT, OCR_LOOT_EFFECT, LOOT_CARD, LOOT_BORDER,
    OCR_BATTLE_REWARDS, OCR_RESUME_REWARDS, VICTORY_CONTINUE_ACTIVE,
    RESUME_REWARDS_CONTINUE, OCR_SETTLEMENT_STARLIGHT, CHAPTER_SELECT,
    LEAVE_SHOP_CONFIRM, CANCEL_ABANDON, SETTLEMENT_CLOSE,
    REWARD_LEAVE_CONFIRM, REWARD_LEAVE_CANCEL, EXPLORATION_ENTRY,
)
from tasks.dimensional_exploration.policy import (
    RunProgress, choose_offer, node_priority, normalize,
)
from tasks.dimensional_exploration.events.event import EventMemory, decide_event, event_branch, match_event, pending_event
from tasks.dimensional_exploration.events.health import EventHealthMixin
from tasks.dimensional_exploration.events.introduction import EventIntroductionMixin
from tasks.dimensional_exploration.events.recruitment_limit import EventRecruitmentLimitMixin
from tasks.dimensional_exploration.rooms.rest import RestMixin
from tasks.dimensional_exploration.rooms.starting_strategy import StartingStrategyMixin
from tasks.dimensional_exploration.heroes.recruitment import HeroCosts, RecruitmentMixin
from tasks.dimensional_exploration.heroes.upgrade import UpgradeMixin
from tasks.dimensional_exploration.events.sacrifice import SacrificeMixin
from tasks.dimensional_exploration.events.sampling import EventSampler, choose_sample, sampling_balances, text_key
from tasks.dimensional_exploration.recognition.vision import ExplorationVision, match_in
from tasks.dimensional_exploration.recognition.hero_names import HeroNameOcr
from tasks.dungeon.assets.assets_dungeon_action import AUTO_COMBAT
from tasks.dungeon.assets.assets_dungeon_state import (
    AUTO_COMBAT_EXIST, COMBAT_RESULT_CLEAR,
)


class DimensionalExploration(StartingStrategyMixin, RestMixin, EventHealthMixin, EventIntroductionMixin, EventRecruitmentLimitMixin, UpgradeMixin, SacrificeMixin, RecruitmentMixin, UI):
    def __init__(self, config, device=None, task=None):
        super().__init__(config, device=device, task=task)
        self._last_state = None
        self._hero_name_ocr = HeroNameOcr()
        self._observed_state = None
        self._analysis_state = None
        self._analysis_timer = Timer(1)
        self._battle_active = False
        self._pending_offer = None
        self._shop_offers = None
        self._shop_candidate = None
        self._purchase_balance = None
        self._purchase_confirmed = False
        self._purchase_candidate = None
        self._progress_state = None
        self._progress_candidate = None
        self._loot_index = None
        self._initial_reserved = 0
        self._initial_recruitment = False
        self._recruit_skipped = False
        self._reward_leave_authorized = False
        self._node_selected = False
        self._node_kind = None
        self._entry_swipes = 0
        self._unreadable = Timer(40).start()
        self.event_memory = EventMemory.from_saved(getattr(config, "DimensionalExplorationRuntime_EventHistory", {}))
        self.sampler = None
        name = getattr(config, "config_name", None)
        self.hero_costs = HeroCosts.from_runtime() if name else HeroCosts()
        if getattr(config, "DimensionalExploration_EventSampling", False):
            name = str(getattr(config, "config_name", "default"))
            profile = "".join(c if c.isalnum() or c in "_-" else "_" for c in name)[:48]
            root = Path("screenshots/dimensional_exploration_events") / f"config_{profile}_{text_key(name)[:8]}"
            self.sampler = EventSampler(root)
            logger.info(f"Exploration event sampling directory: {root}")
        self.load_recruitment()
        self.reset_hero_search()
        self.reset_event_health()
        self.reset_rest_health()
        self.reset_upgrade_search()
        self.reset_sacrifice()

    def action_ready(self):
        return self.interval_is_reached("ExplorationAction", interval=2)

    def click_action(self, button):
        if not self.action_ready():
            return False
        self.device.click(button)
        self.interval_reset("ExplorationAction", interval=2)
        return True

    def handle_network_overlay(self):
        if not self.appear(NETWORK_ERROR_ABNORMAL):
            return False
        # The dimmed page still matches EVENT_CHECK and EVENT_NEXT. Consume
        # every overlay frame before page observations, otherwise a countdown
        # can confirm the pending event receipt and authorize a hidden click.
        # The retry marker supplies its current offset only when it is visible;
        # do not reuse the generic sleep-and-click recovery or an old offset.
        if self.appear(TOUCH_TO_CLOSE) and self.click_action(TOUCH_TO_CLOSE):
            logger.warning("Exploration network abnormal, clicking visible retry")
        self._unreadable.reset()
        return True

    def require_human(self, message):
        logger.error(message)
        self.device.save_screenshot(genre="dimensional_exploration")
        raise RequestHumanTakeover(message)

    def resolve_state(self, vision):
        state = vision.state()
        # Skill animations hide every HUD marker. Only a previously recognized
        # fight or a clicked battle start permits retaining battle context.
        # A positive non-battle page ends it; an arbitrary unknown startup frame
        # must never be promoted to battle or authorize an auto-button click.
        if state == "unknown" and self._battle_active:
            return "battle"
        if state != "unknown":
            self._battle_active = state == "battle"
        return state

    def analysis_ready(self, state):
        # Keep cheap page/exit detection active on every screenshot. Repeating
        # expensive OCR on a stuck, unchanged page is limited to once a second;
        # a new state is inspected immediately, without sleeping after clicks.
        if state != self._analysis_state or self._analysis_timer.reached():
            self._analysis_state = state
            self._analysis_timer.reset()
            return True
        return False

    def save_progress(self, finished=False):
        self.config.DimensionalExplorationRuntime_Session = {
            **getattr(self.config, "DimensionalExplorationRuntime_Session", {}),
            "target": self.target, "completed": self.progress.completed,
            "settlement_seen": self.progress.settlement_seen, "finished": finished,
            "starlight_total": self.progress.starlight_total, "last_starlight": self.progress.last_starlight,
        }

    def run(self):
        """Run a manual batch and finish after the last settlement.

        Pages:
            in: main, combat common, exploration title/lobby, or an ongoing run
            out: main after the requested number of rewarded settlements
        """
        if not server.is_oversea_server(self.config.Emulator_PackageName) or server.lang != "global_cn":
            logger.info("Dimensional exploration currently supports the global Chinese client only.")
            return
        self.validate_upgrade_priority()
        self.target = int(self.config.DimensionalExploration_RunCount)
        if not 1 <= self.target <= 999:
            raise RequestHumanTakeover("Exploration run count must be between 1 and 999.")
        session = self.config.DimensionalExplorationRuntime_Session
        completed = int(session.get("completed", 0))
        previous_target = int(session.get("target", self.target))
        # The last settlement is saved before navigating home. A stop during
        # that return can leave a reached target with finished=False; restoring
        # its count would immediately exit at the lobby on the first Start.
        # Use both the saved target and the requested target so raising a new
        # target cannot revive a completed old batch, while lowering it below
        # the saved count cannot create another immediate no-op start.
        if session.get("finished") or completed >= min(previous_target, self.target):
            # Reset only batch totals. The already-counted result may still be
            # visible, even across another restart before its close succeeds.
            # Only the loop's positive map/setup state can re-arm settlement
            # counting; resetting this guard here would count the old result
            # as the new batch's first run. Old recruitment belongs to the
            # completed batch and must not suppress the next run's choices.
            self.progress = RunProgress(settlement_seen=bool(session.get("settlement_seen", False)))
            self.clear_recruitment()
            logger.info(f"Starting new exploration batch: previous={completed}/{previous_target}, target={self.target}.")
        else:
            self.progress = RunProgress(
                completed, bool(session.get("settlement_seen", False)),
                int(session.get("starlight_total", 0)), session.get("last_starlight"),
            )
        self.save_progress()
        self.device.screenshot()
        if ExplorationVision(self.device.image).state() == "unknown":
            from tasks.base.page import Page, page_combat_common
            if any(self.ui_page_appear(page) for page in Page.iter_pages()):
                self.ui_ensure(page_combat_common)
        self.explore()
        self.ui_goto_main()
        self.save_progress(finished=True)
        logger.info(f"Exploration batch complete: {self.progress.completed}/{self.target} runs, total starlight {self.progress.starlight_total}.")

    def explore(self, skip_first_screenshot=True):
        """Keep subpages in this loop so every action is confirmed by a new frame.

        Pages:
            in: combat common or any recognized exploration state
            out: exploration lobby/title after the configured batch
        """
        while True:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()
            if self.handle_network_overlay():
                continue
            vision = ExplorationVision(self.device.image, hero_name_ocr=self._hero_name_ocr)
            state = self.resolve_state(vision)
            if state in ("title", "lobby") and self.progress.completed >= self.target:
                return
            self.observe_state(state)
            self.observe_progress(state)
            if not self.analysis_ready(state):
                continue
            self.observe_event(state, vision)
            self.observe_rest_health(state)
            self.observe_upgrade(state)
            self.observe_sacrifice(state)
            self.observe_recruitment(state, vision)
            if state == "settlement":
                if not self.handle_settlement(vision) and self._unreadable.reached():
                    self.require_human("Settlement starlight is unreadable; run count has not been increased.")
                continue
            if state in ("start_supply", "recruitment", "map") and self.progress.settlement_seen:
                self.clear_recruitment()
                self.progress.entered_run()
                self.save_progress()
            handlers = {
                "title": lambda v: self.click_action(TITLE_ENTER),
                "chapter": lambda v: self.click_action(CHAPTER_SELECT),
                "lobby": self.handle_lobby, "start_supply": self.handle_start_supply,
                "recruitment": self.handle_recruitment, "hero": self.handle_hero,
                "roster": self.handle_roster,
                "map": self.handle_map, "preview": self.handle_preview,
                "event": self.handle_event, "shop": self.handle_shop,
                "event_recruitment_limit": self.handle_event_recruitment_limit,
                "buy": self.handle_buy, "rest": self.handle_rest,
                "upgrade": self.handle_rest_hero, "revive": self.handle_rest_hero,
                "sacrifice": self.handle_sacrifice,
                "supply_room": self.handle_supply_room, "loot": self.handle_loot,
                "victory": self.handle_victory, "battle": self.handle_battle,
                "resume_rewards": self.handle_victory,
                "reward_leave": self.handle_reward_leave,
                "prepare": self.handle_prepare,
                "reward": lambda v: self.click_action(REWARD_CLOSE),
                "failed": lambda v: self.click_action(FAILED_CHECK),
                "leave_confirm": lambda v: self.click_action(LEAVE_SHOP_CONFIRM),
                "abandon": lambda v: self.click_action(CANCEL_ABANDON),
            }
            if state in handlers:
                if handlers[state](vision):
                    self._unreadable.reset()
                # Recognized roguelike pages contain X icons that resemble the
                # global advertisement close button. Only connection recovery
                # is relevant here; a throttled task click must not fall through
                # to unrelated login/ad handlers on every frame.
                elif self.handle_ui_recovery():
                    self._unreadable.reset()
                elif state not in ("battle", "unknown") and self._unreadable.reached():
                    self.require_human(f"Exploration stalled in {state}; no verified action is available.")
                continue
            if self.handle_entry(vision):
                continue
            if self.appear(COMBAT_RESULT_CLEAR):
                self.click_action(COMBAT_RESULT_CLEAR)
                continue
            if self.ui_additional():
                continue
            # Skill animations have no HUD. Let the framework's existing stuck
            # detection handle truly lost states, rather than time-boxing a fight.

    def observe_state(self, state):
        if state != self._observed_state:
            logger.attr("ExplorationState", state)
            self._observed_state = state
        if state == self._last_state:
            return
        if state != "unknown":
            self._unreadable.reset()
        if state == "hero" and self._last_state != "unknown":
            self.reset_hero_search()
        if state == "loot":
            self._loot_index = None
        if state == "start_supply":
            self.clear_recruitment()
        if state in ("map", "victory"):
            self._initial_reserved = 0
            self._initial_recruitment = False
        if state == "map":
            self._node_selected = False
            self._node_kind = None
            self._recruit_skipped = False
            self._reward_leave_authorized = False
        if state != "unknown":
            self._last_state = state

    def record_progress(self, reason):
        logger.attr("ExplorationProgress", reason)
        self.device.click_record_clear()

    def observe_progress(self, state):
        # Like sanctuary purification's decreasing counter, clear old clicks
        # only after evidence of forward progress. A preview, buy dialog, or
        # selection click alone is not progress. Require two fresh matching
        # frames, and do not let unknown animation frames manufacture an edge.
        if state == "unknown":
            self._progress_candidate = None
            return
        if self._progress_candidate != state:
            self._progress_candidate = state
            return
        previous = self._progress_state
        if previous == state:
            return
        entered = previous in ("map", "preview") and state in (
            "event", "shop", "rest", "supply_room", "prepare", "battle")
        finished = (previous == "battle" and state in ("victory", "failed", "resume_rewards")) or (
            state == "settlement" and previous in ("failed", "victory", "resume_rewards", "battle"))
        if entered or finished:
            self.record_progress(f"{previous} -> {state}")
        self._progress_state = state

    def handle_entry(self, vision):
        from tasks.base.page import page_combat_common
        if not self.ui_page_appear(page_combat_common):
            return False
        if self.appear(EXPLORATION_ENTRY):
            return self.click_action(EXPLORATION_ENTRY)
        if self._entry_swipes >= 4:
            self.require_human("Exploration entry was not found in normal battle; an entry screenshot is required.")
        if self.action_ready():
            self.device.swipe((370, 356), (1020, 356), name="FindDimensionalExploration")
            self._entry_swipes += 1
            self.interval_reset("ExplorationAction", interval=2)
            return True
        return False

    def handle_lobby(self, vision):
        if self.appear(LOBBY_CONTINUE):
            return self.click_action(LOBBY_CONTINUE)
        if self.appear(LOBBY_START):
            if self.click_action(LOBBY_START):
                self.clear_recruitment()
                return True
        return False

    def handle_recruitment(self, vision):
        if vision.bright_text(HERO_CONFIRM_ACTIVE):
            return self.click_action(EXPLORE_ENTER)
        tokens = vision.tokens(OCR_INITIAL_RECRUITMENT)
        buttons = [t for t in tokens if normalize(t.ocr_text) == "招募英雄"]
        if buttons:
            self._initial_reserved = 2 * (len(buttons) - 1)
            self._initial_recruitment = True
            return self.click_action(ClickButton(min(buttons, key=lambda t: t.box[0]).box, name="InitialRecruit"))
        return False

    def handle_map(self, vision):
        resources = vision.resources()
        nodes = vision.nodes()
        if resources is None or not nodes:
            return False
        node = min(nodes, key=lambda n: (node_priority(n.kind, resources.cores), n.area[1]))
        logger.attr("ExplorationRoute", f"cores={resources.cores}, node={node.kind}, score={node.score:.3f}")
        if self.click_action(ClickButton(node.area, name=f"ExplorationNode_{node.kind}")):
            self._node_selected = True
            self._node_kind = node.kind
            # Purchase toasts can briefly hide SHOP_CHECK and expose the map
            # marker underneath. Keep the receipt and inventory through that
            # false state transition; only a reachable node on the real map
            # proves we have left the shop and may discard its snapshot.
            self._shop_offers = None
            self._shop_candidate = None
            self._pending_offer = None
            self._purchase_confirmed = False
            self._purchase_candidate = None
            return True
        return False

    def handle_preview(self, vision):
        # Opening a preview pans the map. Re-read arrows from the new frame;
        # coordinates from the map before the click are never reused here.
        if self._node_selected:
            kind = vision.preview_kind()
            # All captured titles use templates. The shop preview has no
            # supplied source yet; preserve its verified OCR path explicitly.
            if kind is None and self._node_kind == "shop":
                kind = "shop" if normalize(vision.text(OCR_PREVIEW_TITLE)) == "商店" else None
            if kind is not None and kind == self._node_kind:
                return self.click_action(NODE_ENTER)
        return self.handle_map(vision)

    def handle_event(self, vision):
        self.observe_event("event", vision)
        if self.appear(EVENT_NEXT):
            return self.click_action(EVENT_NEXT)
        introduction = self.event_introduction_button(vision)
        if introduction is not None:
            return self.click_action(introduction)
        resources = vision.resources()
        if resources is None:
            return False
        loot = vision.number(OCR_EVENT_LOOT)
        choices = vision.event_choices()
        if not choices:
            return False
        observed = [c for c, _ in choices]
        story = vision.event_story()
        balances = dict(cores=resources.cores, fragments=resources.fragments, life=resources.life,
                        loot=loot, dice=vision.event_dice())
        matched = match_event(observed, story)
        deferred = pending_event(story)
        if deferred is None and matched is not None and matched[0].decision_pending:
            deferred = matched[0]
        if deferred is not None:
            reason = f"Event policy is pending: {deferred.event_id}."
            if self.sampler is not None:
                self.sampler.before(vision.image, story, observed, balances, None, "catalog", reason)
            self.require_human(reason)
        if matched is None:
            if self.sampler is not None:
                if self.event_memory.pending and not self.event_memory.advanced:
                    return False
                return self.handle_event_sample(vision, choices, story, balances)
            self.require_human("Event or option cost is not covered; a screenshot has been saved for the event catalog.")
        # A partial row can identify the event, but cannot establish the best
        # choice or prove that a pending option disappeared into a new stage.
        # Wait for all known options, including unavailable ones, before acting.
        if len(matched[1]) != len(matched[0].branches):
            return False
        # A reviewed multi-stage option set is itself the previous choice's
        # result (e.g. a failed 30% coin reveals the 70-fragment stage). Save it
        # before beginning the next receipt. Match branch keys, not OCR drift:
        # a reworded old option must not complete an unconfirmed click.
        if (self.sampler is not None and self.sampler.active and self.sampler.active['clicked']
                and self.sampler.active['source'] == 'catalog'
                and self.sampler.active['reason'] not in {b.key for _, b in matched[1]}):
            self.sampler.observe('event_options', vision.image,
                                 text="\n".join([story, *(c.text for c in observed)]), resources=balances)
        # Multi-step encounters may expose another known option set without
        # visiting a map. Only a fully recognized new set confirms advancement;
        # an unknown/partially read frame must retain the pending choice.
        if self.event_memory.pending and self.event_memory.pending not in {b.key for _, b in matched[1]}:
            if self.event_memory.observe("event", narration=True):
                self.save_event_history()
        decision = decide_event(observed, **balances, max_life=resources.max_life,
                                story=story, memory=self.event_memory)
        # HP conditions can promote healing over an otherwise free fragment
        # route. Read the roster before ranking those competing options, even
        # when the provisional decision is None because HP is still unknown;
        # checking only the selected branch would permanently skip healing or
        # request manual takeover before ever opening the roster.
        needs_hp = (any(branch.enabled and choice.available and branch.when.requires_hero_hp
                        for choice, branch in matched[1])
                    or decision is not None and (decision.branch.cost.random_hp_percent or decision.branch.cost.all_hp_percent
                                                 or any(r['kind'] == 'all_hp_percent' for r in decision.branch.rewards)))
        if needs_hp:
            ready, clicked = self.inspect_event_health(vision, observed, story)
            if not ready:
                return clicked
            balances['hero_hp'] = self._event_hero_hp
            decision = decide_event(observed, **balances, max_life=resources.max_life,
                                    story=story, memory=self.event_memory)
        if decision is None:
            if self.sampler is not None:
                self.sampler.before(vision.image, story, observed, balances, None, "catalog", "No affordable option")
            self.require_human("No available event option satisfies the policy and life reserve; a screenshot has been saved.")
        selected = decision.choice
        area = next(area for c, area in choices if c.index == selected.index)
        if not self.action_ready():
            return False
        logger.attr("ExplorationEvent", f"{decision.event.event_id}: {selected.text}")
        logger.attr("ExplorationEventReason", "First attempt at the exclusive reward" if decision.first_collectible else "Event table conditions and priority")
        logger.attr("ExplorationEventCost", vars(decision.branch.cost))
        if self.sampler is not None:
            self.sampler.before(vision.image, story, observed, balances, selected, "catalog", decision.branch.key)
        if self.click_action(ClickButton(area, name="ExplorationEventChoice")):
            if self.sampler is not None:
                self.sampler.clicked()
            self.event_memory.begin(decision.branch)
            self.save_event_history()
            return True
        return False

    def handle_event_sample(self, vision, choices, story, balances):
        observed = [c for c, _ in choices]
        # Unknown events need two consistent reads, including all displayed
        # balances, before attempting an option. Partially rendered cost text
        # must not become a supposedly free trial on its first frame.
        if not story or not self.sampler.stable(story, observed, balances):
            return False
        selected = choose_sample(observed, balances, self.sampler.visits(story), self.sampler.pending_text(story))
        if selected is not None and (selected.cost.random_hp_percent or selected.cost.all_hp_percent):
            ready, clicked = self.inspect_event_health(vision, observed, story)
            if not ready:
                return clicked
            balances['hero_hp'] = self._event_hero_hp
            selected = choose_sample(observed, balances, self.sampler.visits(story), self.sampler.pending_text(story))
        if selected is None:
            self.sampler.before(vision.image, story, observed, balances, None, "sampling", "Unknown cost or insufficient resources")
            self.require_human("Event sample saved; every option has an unknown cost or insufficient resources. Manual action is required.")
        if not self.action_ready():
            return False
        self.sampler.before(vision.image, story, observed, balances, selected.choice, "sampling", selected.reason)
        area = next(area for choice, area in choices if choice.index == selected.choice.index)
        logger.attr("ExplorationSampleChoice", f"{selected.choice.text}: {selected.reason}")
        if self.click_action(ClickButton(area, name="ExplorationEventSample")):
            self.sampler.clicked()
            # Unlisted branches are kept in the sample receipt, not inserted
            # into the reviewed catalogue's acquisition/visited history.
            if self.event_memory.pending and self.event_memory.advanced:
                self.event_memory.pending, self.event_memory.advanced = None, False
                self.save_event_history()
            return True
        return False

    def save_event_history(self):
        self.config.DimensionalExplorationRuntime_EventHistory = self.event_memory.as_dict()

    def observe_event(self, state, vision):
        self.observe_event_sample(state, vision)
        narration = state == "event" and self.appear(EVENT_NEXT)
        if narration or state in ("map", "reward", "loot", "prepare", "battle", "victory", "resume_rewards",
                                  "upgrade", "revive", "failed", "settlement", "lobby", "start_supply", "recruitment"):
            self.reset_event_health()
        if not self.event_memory.pending:
            return
        reward_name = vision.event_reward() if state == "reward" else ""
        if self.event_memory.observe(state, narration=narration, reward_name=reward_name):
            self.save_event_history()

    def observe_event_sample(self, state, vision):
        if self.sampler is None or not self.sampler.active or not self.sampler.active["clicked"]:
            return
        narration = state == "event" and self.appear(EVENT_NEXT)
        if not narration and state not in self.sampler.OUTCOME_STATES:
            return
        frames = self.sampler.active["frames"]
        # Combat may last minutes. Save its entry once, not every screenshot;
        # only narration and reward cards need text to distinguish subpages.
        if state not in ("event", "reward", "resume_rewards", "victory", "sacrifice") and state not in self.sampler.END_STATES and any(
                f["state"] == state for f in frames):
            return
        text = vision.event_story() if narration else vision.event_reward() if state == "reward" else ""
        if state in ("resume_rewards", "victory"):
            region = OCR_RESUME_REWARDS if state == "resume_rewards" else OCR_BATTLE_REWARDS
            text = "".join(t.ocr_text for t in vision.tokens(region))
        if state == "sacrifice":
            text = f"Selected items: {vision.sacrifice_counter()}"
        if state not in self.sampler.END_STATES and any(
                f["state"] == state and normalize(f["text"]) == normalize(text) for f in frames):
            return
        self.sampler.observe(state, vision.image, text=text, resources=sampling_balances(vision), narration=narration,
                             confirmed=state != "sacrifice")

    def handle_shop(self, vision):
        if not self.action_ready():
            return False
        resources = vision.resources()
        if resources is None:
            return False
        # Snapshot the complete inventory before the first purchase. Toasts
        # obscure the upper row afterwards, so names/NEW flags must not be
        # replaced by partial OCR. The receipt still rechecks each name/price.
        if self._shop_offers is None:
            offers = vision.offers()
            if any(not o.name or (o.price is None and not o.sold) for o in offers):
                self._shop_candidate = None
                return False
            if offers != self._shop_candidate:
                self._shop_candidate = offers
                return False
            self._shop_offers = offers
        if self._purchase_confirmed:
            expected_balance = self._purchase_balance - self._pending_offer.price
            if resources.fragments != expected_balance:
                self._purchase_candidate = None
                return False
            if self._purchase_candidate != resources.fragments:
                self._purchase_candidate = resources.fragments
                return False
            index = self._pending_offer.index
            self._shop_offers = [replace(o, sold=True) if o.index == index else o for o in self._shop_offers]
            self._purchase_confirmed = False
            self._pending_offer = None
            self.record_progress("Shop payment confirmed by the fragment balance change")
        offer = choose_offer(self._shop_offers, resources.fragments, resources.life, resources.max_life,
                             cores=resources.cores)
        if offer is None:
            self._pending_offer = None
            return self.click_action(ROOM_LEAVE)
        button = list(SHOP_OFFER.iter_buttons())[offer.index]
        if self.click_action(ClickButton(button.area, name="ExplorationShopOffer")):
            self._pending_offer = offer
            self._purchase_balance = resources.fragments
            self._purchase_candidate = None
            return True
        return False

    def handle_buy(self, vision):
        if not self.action_ready():
            return False
        # A manually opened dialog has no verified offer. Cancel and re-read
        # the shop instead of reconstructing authorization from a dimmed page.
        if self._pending_offer is None:
            return self.click_action(BUY_CANCEL)
        name = normalize(vision.text(OCR_BUY_NAME))
        price = vision.number(OCR_BUY_PRICE)
        expected = self._pending_offer
        if name != normalize(expected.name) or price != expected.price or not self.appear(BUY_CURRENCY):
            self.require_human("Shop confirmation does not match the selected offer; purchase stopped.")
        if self.click_action(BUY_CONFIRM):
            self._purchase_confirmed = True
            return True
        return False

    def handle_supply_room(self, vision):
        if match_in(self.device.image, ROOM_DONE, SUPPLY_DONE_AREA.area):
            return self.click_action(ROOM_LEAVE)
        if vision.bright_text(SUPPLY_LOOT, minimum=70):
            return self.click_action(SUPPLY_LOOT)
        return False

    def handle_loot(self, vision):
        selected = [i for i, border in enumerate(LOOT_BORDER.iter_buttons()) if vision.gold_border(border)]
        # The UI selection is authoritative after a restart. Never repeatedly
        # toggle a selected card just because its effect is not our top score.
        if len(selected) == 1:
            self._loot_index = selected[0]
            return self.click_action(LOOT_CHECK)
        if self._loot_index is None:
            scores = []
            for index, region in enumerate(OCR_LOOT_EFFECT.iter_buttons()):
                text = "".join(t.ocr_text for t in vision.tokens(region))
                scores.append((sum(term in text for term in ("速度", "攻击力", "暴击", "伤害")), -index))
            self._loot_index = -max(scores)[1]
        button = list(LOOT_CARD.iter_buttons())[self._loot_index]
        return self.click_action(ClickButton(button.area, name="ExplorationLoot"))

    def handle_victory(self, vision):
        if not self.action_ready():
            return False
        resumed = vision.state() == "resume_rewards"
        region = OCR_RESUME_REWARDS if resumed else OCR_BATTLE_REWARDS
        tokens = vision.tokens(region)
        # Event rank-up cards use the same reward-button row as battle cards.
        # Claim their upgrade before Continue: skipping it opens the unclaimed
        # reward modal, whose safe cancellation returns to this same card and
        # otherwise creates an endless Continue -> Cancel -> Continue loop.
        for token in tokens:
            text = normalize(token.ocr_text)
            if text in ("选择战利品", "英雄升阶") or (text == "招募英雄" and not self._recruit_skipped):
                self._initial_recruitment = False
                self._initial_reserved = 0
                return self.click_action(ClickButton(token.box, name="ClaimExplorationBattleReward"))
        # Empty or partly read text can open a confirmation but must never
        # authorize discarding a reward. Only completed cards and deliberately
        # skipped recruitment cards prove that nothing still needs claiming.
        may_leave = bool(tokens) and all(normalize(token.ocr_text) in ("完成领取", "招募英雄") for token in tokens)
        # The resumed event card also exposes story text beside its button.
        # An explicitly skipped, single-kind hero reward is still safe to
        # discard when quota allows nobody. Require the current persisted
        # event branch and the positively read recruitment button; unrelated
        # narration, a stale/restarted popup or a mixed reward proves nothing.
        if resumed and self._recruit_skipped and any(normalize(t.ocr_text) == "招募英雄" for t in tokens):
            branch = event_branch(self.event_memory.pending)
            if branch and branch.rewards and all(reward['kind'] == 'hero' for reward in branch.rewards):
                may_leave = True
        if resumed and vision.bright_text(RESUME_REWARDS_CONTINUE):
            if self.click_action(RESUME_REWARDS_CONTINUE):
                self._reward_leave_authorized = may_leave
                return True
        if self.appear(VICTORY_CONTINUE) and vision.bright_text(VICTORY_CONTINUE_ACTIVE):
            if self.click_action(VICTORY_CONTINUE):
                self._reward_leave_authorized = may_leave
                return True
        return False

    def handle_reward_leave(self, vision):
        # A popup seen after restart has no trustworthy reward decision. Cancel
        # once and inspect the unobscured cards. Confirm only after this task has
        # checked those cards and deliberately clicked Continue; preserve that
        # authorization through repeated modal frames until the map resets it.
        return self.click_action(REWARD_LEAVE_CONFIRM if self._reward_leave_authorized else REWARD_LEAVE_CANCEL)

    def handle_prepare(self, vision):
        if self.click_action(BATTLE_START):
            self._battle_active = True
            return True
        return False

    def handle_battle(self, vision):
        if vision.auto_combat is True:
            self.device.stuck_record_clear()
            return False
        if vision.auto_combat is False or self.appear(MANUAL_TARGET):
            return self.click_action(AUTO_COMBAT)
        if self.appear(AUTO_COMBAT_EXIST):
            self.device.stuck_record_clear()
        return False

    def handle_settlement(self, vision):
        if self.appear(SETTLEMENT_EMPTY):
            self.require_human("This run did not enter exploration and has not been counted.")
        starlight = vision.number(OCR_SETTLEMENT_STARLIGHT)
        if starlight is None:
            return False
        if self.progress.settle(rewarded=True, starlight=starlight):
            self.save_progress()
            logger.info(f"Exploration settlement: starlight +{starlight}, batch total {self.progress.starlight_total}, "
                        f"completed {self.progress.completed}/{self.target} runs.")
        return self.click_action(SETTLEMENT_CLOSE)
