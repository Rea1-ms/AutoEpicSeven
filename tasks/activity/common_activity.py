from module.base.timer import Timer
from module.logger import logger
from module.ocr.ocr import Ocr, OcrResultButton
from tasks.activity.assets.assets_activity_common import COMMON_ACTIVITY_LIST
from tasks.activity.assets.assets_activity_special_26_8_27 import FREE_20_GACHA_SELECTED
from tasks.activity.assets.assets_activity_special_26_9_12 import E7WC_BATTLE_GATE_SELECTED
from tasks.activity.assets.assets_activity_special_26_9_17 import CHUN_GATE_SELECTED
from tasks.activity.e7wc_battle_gate import E7wcBattleGate
from tasks.activity.free_gacha_20 import FreeGacha20
from tasks.activity.koharu_raffle import KoharuRaffle
from tasks.activity.navigation import ActivityNavigationMixin
from tasks.base.page import page_common_activity
from tasks.base.ui import UI


class CommonActivityBatch(ActivityNavigationMixin, UI):
    """Handle visible pending events before moving the shared sidebar."""

    ACTIVITIES = {
        "free_gacha_20": ("INFINITY", FREE_20_GACHA_SELECTED, FreeGacha20),
        "e7wc_battle_gate": ("激战门", E7WC_BATTLE_GATE_SELECTED, E7wcBattleGate),
        "koharu_raffle": ("收集抽奖券", CHUN_GATE_SELECTED, KoharuRaffle),
    }

    def run(self, activities) -> bool:
        """Process enabled, unchecked calendar entries supplied by the entry.

        Pages:
            in: page_main, any
            out: page_common_activity after checks; current page on failure
        """
        pending = {}
        for event in activities:
            keyword, selected, worker = self.ACTIVITIES[event.mode]
            pending[event.event_id] = (
                self._activity_text(keyword), selected,
                worker(self.config, device=self.device, activity_id=event.event_id),
            )
        if not pending:
            return True

        if not self.device.app_is_running():
            from tasks.login.login import Login

            Login(self.config, device=self.device).app_start()
        self.ui_goto(page_common_activity, skip_first_screenshot=True)
        success = self._run_pending(pending)
        if not success:
            self.config.task_delay(success=False)
        return success

    def _run_pending(self, pending, skip_first_screenshot=True) -> bool:
        lang = self.config.Emulator_GameLanguage
        if not lang or lang == "auto":
            lang = "cn"
        ocr = Ocr(COMMON_ACTIVITY_LIST, lang=lang, name="ActivityList")
        timeout = Timer(self.ACTIVITY_NAVIGATION_TIMEOUT_SECONDS, count=180).start()
        previous = None
        before_scroll = None
        search_later = True
        scrolls = 0
        clicked_id = None

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if not pending:
                return True
            if self.ui_page_appear(page_common_activity):
                selected = [event_id for event_id, (_, button, _) in pending.items()
                            if self._activity_selected(button)]
                if len(selected) == 1 and clicked_id in (None, selected[0]):
                    event_id = selected[0]
                    logger.info(f"SpecialActivity: handle selected event {event_id}")
                    # These short child loops own their reward popups and return
                    # to a verified event page. They must not navigate the shared
                    # sidebar: the parent retains the sweep direction and budget.
                    if not pending[event_id][2].run_claim(navigate=False):
                        return False
                    # Local completion is separate from the daily record. Koharu
                    # with no available reward must leave this batch once, while
                    # remaining eligible for another check after later battles.
                    del pending[event_id]
                    if not pending:
                        return True
                    clicked_id = None
                    previous = before_scroll = None
                    timeout.reset()
                    continue

                if len(selected) <= 1:
                    results = sorted(ocr.detect_and_ocr(self.device.image), key=lambda row: row.box[1])
                    matches = {
                        event_id: [row for row in results if keyword in self._activity_text(row.ocr_text)]
                        for event_id, (keyword, _, _) in pending.items()
                    }
                    visible = [event_id for event_id, rows in matches.items() if rows]
                    candidates = [event_id for event_id in visible if len(matches[event_id]) == 1]
                    if clicked_id is not None:
                        candidates = [clicked_id] if len(matches[clicked_id]) == 1 else []
                    target = min(candidates, key=lambda event_id: matches[event_id][0].box[1]) if candidates else None
                    observed = matches[target] if target is not None else results
                    signature = tuple((self._activity_text(row.ocr_text), tuple(row.box)) for row in observed)
                    stable = bool(signature) and self._activity_list_stable(previous, signature)
                    has_previous = previous is not None
                    previous = signature

                    if target is not None and stable:
                        button = pending[target][1]
                        if self.interval_is_reached(button, interval=2):
                            self.device.click(OcrResultButton(matches[target][0], matched_keyword=None))
                            self.interval_reset(button, interval=2)
                            # A tap is not confirmation. Keep retrying this target
                            # until its selected marker arrives; never chase another
                            # visible tab or scroll during a delayed transition.
                            clicked_id = target
                            previous = None
                            continue
                    elif has_previous and not visible and clicked_id is None:
                        if self.interval_is_reached(COMMON_ACTIVITY_LIST, interval=2):
                            limit = self.ACTIVITY_SCROLL_DOWN_LIMIT if search_later else self.ACTIVITY_SCROLL_UP_LIMIT
                            if scrolls >= limit or self._activity_scroll_unchanged(before_scroll, signature):
                                if not search_later:
                                    logger.warning(f"SpecialActivity: sidebar search exhausted, pending={list(pending)}")
                                    return False
                                search_later = False
                                scrolls = 0
                                logger.info("SpecialActivity: reverse shared sidebar search toward earlier entries")
                            left, top, right, bottom = COMMON_ACTIVITY_LIST.area
                            x = (left + right) // 2
                            upper, lower = (x, top + 100), (x, bottom - 100)
                            start, end = (lower, upper) if search_later else (upper, lower)
                            self.device.swipe(start, end, duration=(0.3, 0.4))
                            self.interval_reset(COMMON_ACTIVITY_LIST, interval=2)
                            scrolls += 1
                            before_scroll = signature
                            previous = None
                            continue
            else:
                previous = None

            if timeout.reached():
                logger.warning(f"SpecialActivity: sidebar navigation timeout, pending={list(pending)}")
                return False
            if self.handle_network_error():
                previous = None
