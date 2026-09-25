"""Farm complete dimensional explorations, using one screenshot-driven loop."""

from pathlib import Path
from dataclasses import replace

from module.base.button import ClickButton
from module.base.timer import Timer
import module.config.server as server
from module.exception import RequestHumanTakeover
from module.logger import logger
from tasks.base.ui import UI
from tasks.dimensional_exploration.assets.assets_dimensional_exploration import (
    BUY_CANCEL, BUY_CONFIRM, BUY_CURRENCY, EVENT_NEXT, EXPLORE_ENTER,
    FAILED_CHECK, LOBBY_CONTINUE, LOBBY_START, LOOT_CHECK, MANUAL_TARGET, NODE_ENTER,
    REWARD_CLOSE, ROOM_DONE, ROOM_LEAVE, SETTLEMENT_EMPTY, SUPPLY_BAGGAGE, SUPPLY_CONFIRM, SUPPLY_SELECTED,
    TITLE_ENTER, VICTORY_CONTINUE, BATTLE_START,
    OCR_ENTRY, OCR_PREVIEW_TITLE, OCR_INITIAL_RECRUITMENT, HERO_CONFIRM_ACTIVE,
    OCR_EVENT_LOOT, OCR_BUY_NAME, OCR_BUY_PRICE, SHOP_OFFER, REST_ACTION,
    SUPPLY_DONE_AREA, SUPPLY_LOOT, OCR_LOOT_EFFECT, LOOT_CARD, LOOT_BORDER,
    OCR_BATTLE_REWARDS, OCR_RESUME_REWARDS, VICTORY_CONTINUE_ACTIVE,
    RESUME_REWARDS_CONTINUE, OCR_SETTLEMENT_SCORE, CHAPTER_SELECT,
    LEAVE_SHOP_CONFIRM, CANCEL_ABANDON, SETTLEMENT_CLOSE,
)
from tasks.dimensional_exploration.policy import (
    RunProgress, choose_offer, node_priority, normalize, parse_number,
)
from tasks.dimensional_exploration.event import EventMemory, decide_event, match_event
from tasks.dimensional_exploration.recruitment import HeroCosts, RecruitmentMixin
from tasks.dimensional_exploration.sampling import EventSampler, choose_sample, sampling_balances, text_key
from tasks.dimensional_exploration.vision import ExplorationVision, match_in
from tasks.dungeon.assets.assets_dungeon_action import AUTO_COMBAT, AUTO_COMBAT_ENEMY_SELECT
from tasks.dungeon.assets.assets_dungeon_state import (
    AUTO_COMBAT_EXIST, AUTO_COMBAT_SKILL_CLOSED, AUTO_COMBAT_SKILL_OPENED,
    COMBAT_RESULT_CLEAR, ENEMY_NUM_EXIST,
)


class DimensionalExploration(RecruitmentMixin, UI):
    def __init__(self, config, device=None, task=None):
        super().__init__(config, device=device, task=task)
        self._last_state = None
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
        self._node_selected = False
        self._node_kind = None
        self._entry_swipes = 0
        self._unreadable = Timer(40).start()
        self.event_memory = EventMemory.from_saved(getattr(config, "DimensionalExplorationRuntime_EventHistory", {}))
        self.sampler = None
        name = getattr(config, "config_name", None)
        cost_path = Path("config") / f"dimensional_exploration_hero_costs_{text_key(str(name))}.json" if name else None
        self.hero_costs = HeroCosts(cost_path)
        if getattr(config, "DimensionalExploration_EventSampling", False):
            name = str(getattr(config, "config_name", "default"))
            profile = "".join(c if c.isalnum() or c in "_-" else "_" for c in name)[:48]
            root = Path("screenshots/dimensional_exploration_events") / f"config_{profile}_{text_key(name)[:8]}"
            self.sampler = EventSampler(root)
            logger.info(f"事件自动采样目录：{root}")
        self.reset_hero_search()

    def action_ready(self):
        return self.interval_is_reached("ExplorationAction", interval=2)

    def click_action(self, button):
        if not self.action_ready():
            return False
        self.device.click(button)
        self.interval_reset("ExplorationAction", interval=2)
        return True

    def require_human(self, message):
        self.device.save_screenshot(genre="dimensional_exploration")
        raise RequestHumanTakeover(message)

    def save_progress(self, finished=False):
        self.config.DimensionalExplorationRuntime_Session = {
            "target": self.target, "completed": self.progress.completed,
            "settlement_seen": self.progress.settlement_seen, "finished": finished,
        }

    def run(self):
        """Run a configured batch and disable it after the last settlement.

        Pages:
            in: main, combat common, exploration title/lobby, or an ongoing run
            out: main after the requested number of rewarded settlements
        """
        if not server.is_oversea_server(self.config.Emulator_PackageName) or server.lang != "global_cn":
            logger.info("次元探查目前只支持国际服中文界面。")
            self.config.task_delay(server_update=True)
            return
        self.target = int(self.config.DimensionalExploration_RunCount)
        if not 1 <= self.target <= 999:
            raise RequestHumanTakeover("探查轮数应在1至999之间。")
        session = self.config.DimensionalExplorationRuntime_Session
        if session.get("finished"):
            session = {}
        self.progress = RunProgress(int(session.get("completed", 0)), bool(session.get("settlement_seen", False)))
        self.save_progress()
        self.device.screenshot()
        if ExplorationVision(self.device.image).state() == "unknown":
            from tasks.base.page import Page, page_combat_common
            if any(self.ui_page_appear(page) for page in Page.iter_pages()):
                self.ui_ensure(page_combat_common)
        self.explore()
        self.ui_goto_main()
        with self.config.multi_set():
            self.save_progress(finished=True)
            self.config.Scheduler_Enable = False
        logger.info(f"次元探查完成：{self.progress.completed}/{self.target}轮，任务已停止。")

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
            vision = ExplorationVision(self.device.image)
            state = vision.state()
            self.observe_event(state, vision)
            if state in ("title", "lobby") and self.progress.completed >= self.target:
                return
            self.observe_state(state)
            self.observe_progress(state)
            if state == "settlement":
                if not self.handle_settlement(vision) and self._unreadable.reached():
                    self.require_human("整局结算分数未能识别，尚未增加轮数。")
                continue
            if state in ("start_supply", "recruitment", "map") and self.progress.settlement_seen:
                self.progress.entered_run()
                self.save_progress()
            handlers = {
                "title": lambda v: self.click_action(TITLE_ENTER),
                "chapter": lambda v: self.click_action(CHAPTER_SELECT),
                "lobby": self.handle_lobby, "start_supply": self.handle_start_supply,
                "recruitment": self.handle_recruitment, "hero": self.handle_hero,
                "map": self.handle_map, "preview": self.handle_preview,
                "event": self.handle_event, "shop": self.handle_shop,
                "buy": self.handle_buy, "rest": self.handle_rest,
                "upgrade": self.handle_rest_hero, "revive": self.handle_rest_hero,
                "supply_room": self.handle_supply_room, "loot": self.handle_loot,
                "victory": self.handle_victory, "battle": self.handle_battle,
                "resume_rewards": self.handle_victory,
                "prepare": lambda v: self.click_action(BATTLE_START),
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
                    self.require_human(f"次元探查停留在{state}，未识别到可确认的操作。")
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
        if state == self._last_state:
            return
        logger.attr("ExplorationState", state)
        if state != "unknown":
            self._unreadable.reset()
        if state == "hero" and self._last_state != "unknown":
            self.reset_hero_search()
        if state == "loot":
            self._loot_index = None
        if state in ("map", "victory"):
            self._initial_reserved = 0
            self._initial_recruitment = False
        if state == "map":
            self._node_selected = False
            self._node_kind = None
            self._shop_offers = None
            self._shop_candidate = None
            self._pending_offer = None
            self._purchase_confirmed = False
            self._recruit_skipped = False
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
        for token in vision.tokens(OCR_ENTRY):
            if normalize(token.ocr_text) == "次元探查":
                return self.click_action(ClickButton(token.box, name="EnterDimensionalExploration"))
        if self._entry_swipes >= 4:
            self.require_human("普通战斗入口中未找到次元探查，请补充入口截图。")
        if self.action_ready():
            self.device.swipe((1020, 356), (370, 356), name="FindDimensionalExploration")
            self._entry_swipes += 1
            self.interval_reset("ExplorationAction", interval=2)
            return True
        return False

    def handle_lobby(self, vision):
        if self.appear(LOBBY_CONTINUE):
            return self.click_action(LOBBY_CONTINUE)
        if self.appear(LOBBY_START):
            return self.click_action(LOBBY_START)
        return False

    def handle_start_supply(self, vision):
        if SUPPLY_SELECTED.match_color(self.device.image, threshold=25):
            return self.click_action(SUPPLY_CONFIRM)
        if self.appear(SUPPLY_BAGGAGE):
            return self.click_action(SUPPLY_BAGGAGE)
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
            return True
        return False

    def handle_preview(self, vision):
        # Opening a preview pans the map. Re-read arrows from the new frame;
        # coordinates from the map before the click are never reused here.
        if self._node_selected:
            title = normalize(vision.text(OCR_PREVIEW_TITLE))
            expected = {
                "event": "事件", "shop": "商店", "supply": "补给", "rest": "休整",
                "battle": "一般战斗", "elite": "精英战斗", "boss": "首领战斗",
            }[self._node_kind]
            if title == expected:
                return self.click_action(NODE_ENTER)
        return self.handle_map(vision)

    def handle_event(self, vision):
        self.observe_event("event", vision)
        if self.appear(EVENT_NEXT):
            return self.click_action(EVENT_NEXT)
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
        if matched is None:
            if self.sampler is not None:
                if self.event_memory.pending and not self.event_memory.advanced:
                    return False
                return self.handle_event_sample(vision, choices, story, balances)
            self.require_human("事件或选项代价尚未覆盖，已保存截图供补充事件表。")
        # Multi-step encounters may expose another known option set without
        # visiting a map. Only a fully recognized new set confirms advancement;
        # an unknown/partially read frame must retain the pending choice.
        if self.event_memory.pending and self.event_memory.pending not in {b.key for _, b in matched[1]}:
            if self.event_memory.observe("event", narration=True):
                self.save_event_history()
        decision = decide_event(observed, **balances, story=story, memory=self.event_memory)
        if decision is None:
            if self.sampler is not None:
                self.sampler.before(vision.image, story, observed, balances, None, "catalog", "没有可负担的选项")
            self.require_human("事件没有可负担且能保留最后生命体征的选项，已保存截图。")
        selected = decision.choice
        area = next(area for c, area in choices if c.index == selected.index)
        if not self.action_ready():
            return False
        logger.attr("ExplorationEvent", f"{decision.event.name}: {selected.text}")
        logger.attr("ExplorationEventReason", "首次尝试专属奖励" if decision.first_collectible else "重复事件优先低消耗")
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
        if selected is None:
            self.sampler.before(vision.image, story, observed, balances, None, "sampling", "代价不明或资源不足")
            self.require_human("事件采样已保存，但所有选项均存在未识别代价或资源不足，等待人工处理。")
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
        if not self.event_memory.pending:
            return
        narration = state == "event" and self.appear(EVENT_NEXT)
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
        if state not in ("event", "reward") and state not in self.sampler.END_STATES and any(
                f["state"] == state for f in frames):
            return
        text = vision.event_story() if narration else vision.event_reward() if state == "reward" else ""
        if state not in self.sampler.END_STATES and any(
                f["state"] == state and normalize(f["text"]) == normalize(text) for f in frames):
            return
        self.sampler.observe(state, vision.image, text=text, resources=sampling_balances(vision), narration=narration)

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
            self.record_progress("商店付款已由碎片余额变化确认")
        offer = choose_offer(self._shop_offers, resources.fragments, resources.life, resources.max_life)
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
            self.require_human("商店确认框与已选商品不一致，已停止购买。")
        if self.click_action(BUY_CONFIRM):
            self._purchase_confirmed = True
            return True
        return False

    def handle_rest(self, vision):
        if match_in(self.device.image, ROOM_DONE, ROOM_DONE.search):
            return self.click_action(ROOM_LEAVE)
        # The room permits one action. Recover lost lives, revive a hero, then
        # heal, and only upgrade when survival actions are explicitly disabled.
        for row in (2, 1, 3, 0):
            button = list(REST_ACTION.iter_buttons())[row]
            if vision.bright_text(button, minimum=80):
                return self.click_action(ClickButton(button.area, name="ExplorationRestAction"))
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
        resumed = vision.state() == "resume_rewards"
        region = OCR_RESUME_REWARDS if resumed else OCR_BATTLE_REWARDS
        for token in vision.tokens(region):
            text = normalize(token.ocr_text)
            if text == "选择战利品" or (text == "招募英雄" and not self._recruit_skipped):
                self._initial_recruitment = False
                self._initial_reserved = 0
                return self.click_action(ClickButton(token.box, name="ClaimExplorationBattleReward"))
        if resumed and vision.bright_text(RESUME_REWARDS_CONTINUE):
            return self.click_action(RESUME_REWARDS_CONTINUE)
        if self.appear(VICTORY_CONTINUE) and vision.bright_text(VICTORY_CONTINUE_ACTIVE):
            return self.click_action(VICTORY_CONTINUE)
        return False

    def handle_battle(self, vision):
        if any(self.appear(asset) for asset in (
                AUTO_COMBAT_ENEMY_SELECT, AUTO_COMBAT_SKILL_CLOSED, AUTO_COMBAT_SKILL_OPENED)):
            self.device.stuck_record_clear()
            return False
        if self.appear(ENEMY_NUM_EXIST) or self.appear(MANUAL_TARGET):
            return self.click_action(AUTO_COMBAT)
        if self.appear(AUTO_COMBAT_EXIST):
            self.device.stuck_record_clear()
        return False

    def handle_settlement(self, vision):
        if self.appear(SETTLEMENT_EMPTY):
            self.require_human("本轮未进行探查，未计入刷取轮数。")
        score_text = vision.text(OCR_SETTLEMENT_SCORE)
        score = parse_number(score_text.replace("pt", "").strip())
        if not score:
            return False
        if self.progress.settle(rewarded=True):
            self.save_progress()
            logger.info(f"探查结算：{score}分；已完成{self.progress.completed}/{self.target}轮。")
        return self.click_action(SETTLEMENT_CLOSE)
