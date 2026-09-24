"""Farm complete dimensional explorations, using one screenshot-driven loop."""

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
)
from tasks.dimensional_exploration.policy import (
    RunProgress, choose_event, choose_offer, node_priority, normalize, parse_number,
)
from tasks.dimensional_exploration.recruitment import RecruitmentMixin
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
        self._loot_index = None
        self._initial_reserved = 0
        self._node_selected = False
        self._node_kind = None
        self._entry_swipes = 0
        self._unreadable = Timer(40).start()
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
            if state in ("title", "lobby") and self.progress.completed >= self.target:
                return
            self.observe_state(state)
            if state == "settlement":
                if not self.handle_settlement(vision) and self._unreadable.reached():
                    self.require_human("整局结算分数未能识别，尚未增加轮数。")
                continue
            if state in ("start_supply", "recruitment", "map") and self.progress.settlement_seen:
                self.progress.entered_run()
                self.save_progress()
            handlers = {
                "title": lambda v: self.click_action(TITLE_ENTER),
                "chapter": lambda v: self.click_action(ClickButton((141, 170, 252, 261), name="SelectStarJourney")),
                "lobby": self.handle_lobby, "start_supply": self.handle_start_supply,
                "recruitment": self.handle_recruitment, "hero": self.handle_hero,
                "map": self.handle_map, "preview": self.handle_preview,
                "event": self.handle_event, "shop": self.handle_shop,
                "buy": self.handle_buy, "rest": self.handle_rest,
                "upgrade": self.handle_rest_hero, "revive": self.handle_rest_hero,
                "supply_room": self.handle_supply_room, "loot": self.handle_loot,
                "victory": self.handle_victory, "battle": self.handle_battle,
                "prepare": lambda v: self.click_action(BATTLE_START),
                "reward": lambda v: self.click_action(REWARD_CLOSE),
                "failed": lambda v: self.click_action(FAILED_CHECK),
                "leave_confirm": lambda v: self.click_action(ClickButton((692, 433, 810, 484), name="LeaveShopConfirm")),
                "abandon": lambda v: self.click_action(ClickButton((472, 442, 584, 481), name="CancelAbandon")),
            }
            if state in handlers:
                if handlers[state](vision):
                    self._unreadable.reset()
                elif self.ui_additional():
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
        if state == "map":
            self._node_selected = False
            self._node_kind = None
        if state != "unknown":
            self._last_state = state

    def handle_entry(self, vision):
        from tasks.base.page import page_combat_common
        if not self.ui_page_appear(page_combat_common):
            return False
        for token in vision.tokens((22, 90, 1248, 609), name="DimensionalExplorationEntry"):
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
        if vision.bright_text((1040, 646, 1140, 677)):
            return self.click_action(EXPLORE_ENTER)
        tokens = vision.tokens((35, 413, 1218, 604), name="InitialRecruitment")
        buttons = [t for t in tokens if normalize(t.ocr_text) == "招募英雄"]
        if buttons:
            self._initial_reserved = 2 * (len(buttons) - 1)
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
            title = normalize(vision.text((942, 94, 1190, 132)))
            expected = {
                "event": "事件", "shop": "商店", "supply": "补给", "rest": "休息",
                "battle": "一般战斗", "elite": "精英战斗", "boss": "首领战斗",
            }[self._node_kind]
            if title == expected:
                return self.click_action(NODE_ENTER)
        return self.handle_map(vision)

    def handle_event(self, vision):
        if self.appear(EVENT_NEXT):
            return self.click_action(EVENT_NEXT)
        resources = vision.resources()
        if resources is None:
            return False
        loot = vision.number((39, 195, 88, 224))
        choices = vision.event_choices()
        if not choices:
            return False
        selected = choose_event([c for c, _ in choices], cores=resources.cores,
                                fragments=resources.fragments, life=resources.life, loot=loot)
        if selected is None:
            self.require_human("事件选项尚未覆盖，已保存截图供补充规则。")
        area = next(area for c, area in choices if c.index == selected.index)
        logger.attr("ExplorationEvent", selected.text)
        return self.click_action(ClickButton(area, name="ExplorationEventChoice"))

    def handle_shop(self, vision):
        resources = vision.resources()
        if resources is None:
            return False
        offers = vision.offers()
        if any(not o.name or (o.price is None and not o.sold) for o in offers):
            return False
        offer = choose_offer(offers, resources.fragments, resources.life, resources.max_life)
        if offer is None:
            self._pending_offer = None
            return self.click_action(ROOM_LEAVE)
        x = 407 + (offer.index % 4) * 191
        y = 284 + (offer.index // 4) * 251
        if self.click_action(ClickButton((x, y, x + 149, y + 40), name="ExplorationShopOffer")):
            self._pending_offer = offer
            return True
        return False

    def handle_buy(self, vision):
        # A manually opened dialog has no verified offer. Cancel and re-read
        # the shop instead of reconstructing authorization from a dimmed page.
        if self._pending_offer is None:
            return self.click_action(BUY_CANCEL)
        name = normalize(vision.text((640, 342, 955, 373)))
        price = vision.number((676, 488, 740, 528))
        expected = self._pending_offer
        if name != normalize(expected.name) or price != expected.price or not self.appear(BUY_CURRENCY):
            self.require_human("商店确认框与已选商品不一致，已停止购买。")
        return self.click_action(BUY_CONFIRM)

    def handle_rest(self, vision):
        if match_in(self.device.image, ROOM_DONE, ROOM_DONE.search):
            return self.click_action(ROOM_LEAVE)
        # The room permits one action. Recover lost lives, revive a hero, then
        # heal, and only upgrade when survival actions are explicitly disabled.
        for row in (2, 1, 3, 0):
            y = 179 + 109 * row
            area = (930, y, 1173, y + 32)
            if vision.bright_text(area, minimum=80):
                return self.click_action(ClickButton(area, name="ExplorationRestAction"))
        return False

    def handle_supply_room(self, vision):
        if match_in(self.device.image, ROOM_DONE, (1165, 265, 1220, 455)):
            return self.click_action(ROOM_LEAVE)
        area = (932, 399, 1061, 432)
        if vision.bright_text(area, minimum=70):
            return self.click_action(ClickButton(area, name="SupplyLoot"))
        return False

    def handle_loot(self, vision):
        if self._loot_index is None:
            scores = []
            for index in range(3):
                x = 195 + 320 * index
                text = "".join(t.ocr_text for t in vision.tokens((x + 20, 254, x + 236, 407)))
                scores.append((sum(term in text for term in ("速度", "攻击力", "暴击", "伤害")), -index))
            self._loot_index = -max(scores)[1]
        x = 195 + 320 * self._loot_index
        if vision.gold_border((x, 100, x + 13, 232)):
            return self.click_action(LOOT_CHECK)
        return self.click_action(ClickButton((x + 65, 241, x + 200, 406), name="ExplorationLoot"))

    def handle_victory(self, vision):
        for token in vision.tokens((187, 470, 1100, 564), name="ExplorationBattleRewards"):
            if normalize(token.ocr_text) in ("选择战利品", "招募英雄"):
                return self.click_action(ClickButton(token.box, name="ClaimExplorationBattleReward"))
        if self.appear(VICTORY_CONTINUE) and vision.bright_text((590, 647, 692, 675)):
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
        score_text = vision.text((170, 590, 472, 670))
        score = parse_number(score_text.replace("pt", "").strip())
        if not score:
            return False
        if self.progress.settle(rewarded=True):
            self.save_progress()
            logger.info(f"探查结算：{score}分；已完成{self.progress.completed}/{self.target}轮。")
        return self.click_action(ClickButton((600, 683, 699, 710), name="ExplorationSettlementClose"))
