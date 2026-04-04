from dataclasses import dataclass

from module.base.button import ButtonWrapper, ClickButton
from module.base.timer import Timer
from module.logger import logger
from tasks.base.page import page_knights
from tasks.knights.assets.assets_knights_main_page import KNIGHTS_ACTIVITY_ENTRY
from tasks.knights.assets.assets_knights_activity_support_active import (
    ACTION_SEARCH,
    BEGGINER_PENGUIN,
    HIT_BOTTOM,
    ITEM_SEARCH,
    SUPPORT_ACTION,
    LOWER_LEVEL_FAIRY_FLOWER,
    SUPPORT_COMPLETE,
)
from tasks.knights.assets.assets_knights_activity_support_entry import SUPPORT_CHECK
from tasks.knights.assets.assets_knights_activity_support_passive import (
    REQUEST_ACTION,
    REQUEST_BEGGINER_PENGUIN,
    REQUEST_BEGGINER_PENGUIN_SELECTED,
    REQUEST_FOR_SUPPORT_ENTRY,
    REQUEST_LOWER_LEVEL_FAIRY_FLOWER,
    REQUEST_LOWER_LEVEL_FAIRY_FLOWER_SELECTED,
)


@dataclass(frozen=True)
class SupportPlan:
    name: str
    asset: ButtonWrapper
    enabled: bool
    selected_asset: ButtonWrapper | None = None


class KnightsSupportMixin:
    SUPPORT_ENTRY_TIMEOUT_SECONDS = 20
    SUPPORT_ENTRY_CLICK_INTERVAL_SECONDS = 1.2
    SUPPORT_ACTION_Y_TOLERANCE = 80
    SUPPORT_DONATE_TIMEOUT_SECONDS = 30
    SUPPORT_DONATE_SCROLL_INTERVAL_SECONDS = 0.8
    SUPPORT_DONATE_ACTION_INTERVAL_SECONDS = 0.8
    SUPPORT_DONATE_WAIT_TOUCH_TIMEOUT_SECONDS = 2
    SUPPORT_DONATE_EMPTY_CONFIRM_SECONDS = 1
    SUPPORT_DONATE_COMPLETE_COUNT = 4
    SUPPORT_REQUEST_TIMEOUT_SECONDS = 15
    SUPPORT_SCROLL_START = (640, 620)
    SUPPORT_SCROLL_END = (640, 320)
    REQUEST_FOR_SUPPORT_ENTRY_LUMA_SIMILARITY = 0.8
    REQUEST_FOR_SUPPORT_ENTRY_COLOR_THRESHOLD = 10

    @staticmethod
    def _button_center_y(button: ClickButton) -> float:
        return (button.area[1] + button.area[3]) / 2

    def _prepare_support_search_area(self) -> None:
        for item in [LOWER_LEVEL_FAIRY_FLOWER, BEGGINER_PENGUIN]:
            item.load_search(ITEM_SEARCH.area)
        SUPPORT_ACTION.load_search(ACTION_SEARCH.area)
        SUPPORT_COMPLETE.load_search(ACTION_SEARCH.area)

    def _build_donate_plans(self) -> list[SupportPlan]:
        return [
            SupportPlan(
                name="lower_level_fairy_flower",
                asset=LOWER_LEVEL_FAIRY_FLOWER,
                enabled=getattr(
                    self.config,
                    "KnightsDonate_DonateLowerLevelFairyFlower",
                    getattr(self.config, "Knights_DonateLowerLevelFairyFlower", True),
                ),
            ),
            SupportPlan(
                name="beginner_penguin",
                asset=BEGGINER_PENGUIN,
                enabled=getattr(
                    self.config,
                    "KnightsDonate_DonateBeginnerPenguin",
                    getattr(self.config, "Knights_DonateBeginnerPenguin", True),
                ),
            ),
        ]

    def _resolve_request_plan(self) -> SupportPlan | None:
        request_item = getattr(
            self.config,
            "KnightsDonate_RequestItem",
            getattr(self.config, "Knights_RequestItem", None),
        )
        if request_item == "LowerLevelFairyFlower":
            return SupportPlan(
                name="request_lower_level_fairy_flower",
                asset=REQUEST_LOWER_LEVEL_FAIRY_FLOWER,
                enabled=True,
                selected_asset=REQUEST_LOWER_LEVEL_FAIRY_FLOWER_SELECTED,
            )
        if request_item == "BeginnerPenguin":
            return SupportPlan(
                name="request_beginner_penguin",
                asset=REQUEST_BEGGINER_PENGUIN,
                enabled=True,
                selected_asset=REQUEST_BEGGINER_PENGUIN_SELECTED,
            )
        return None

    @staticmethod
    def _enabled_plans(plans: list[SupportPlan]) -> list[SupportPlan]:
        return [plan for plan in plans if plan.enabled]

    def _is_request_for_support_entry_enabled(self, interval=0) -> bool:
        """
        REQUEST_FOR_SUPPORT_ENTRY is dimmed after the daily request is consumed.
        Use luma to locate the button first, then color to confirm it is still enabled.
        """
        self.device.stuck_record_add(REQUEST_FOR_SUPPORT_ENTRY)

        if interval and not self.interval_is_reached(REQUEST_FOR_SUPPORT_ENTRY, interval=interval):
            return False

        appear = False
        if REQUEST_FOR_SUPPORT_ENTRY.match_template_luma(
            self.device.image,
            similarity=self.REQUEST_FOR_SUPPORT_ENTRY_LUMA_SIMILARITY,
        ):
            if REQUEST_FOR_SUPPORT_ENTRY.match_color(
                self.device.image,
                threshold=self.REQUEST_FOR_SUPPORT_ENTRY_COLOR_THRESHOLD,
            ):
                appear = True

        if appear and interval:
            self.interval_reset(REQUEST_FOR_SUPPORT_ENTRY, interval=interval)

        return appear

    def _is_request_for_support_entry_present(self, interval=0) -> bool:
        return self.match_template_luma(
            REQUEST_FOR_SUPPORT_ENTRY,
            interval=interval,
            similarity=self.REQUEST_FOR_SUPPORT_ENTRY_LUMA_SIMILARITY,
        )

    def _find_donate_action_pairs(
        self,
        plans: list[SupportPlan],
        action_buttons: list[ClickButton] | None = None,
    ) -> list[tuple[str, ClickButton]]:
        if action_buttons is None:
            action_buttons = SUPPORT_ACTION.match_multi_template(self.device.image, threshold=40)
        if not action_buttons:
            return []
        action_buttons = sorted(action_buttons, key=self._button_center_y)

        pairs: list[tuple[str, ClickButton]] = []
        used_action_index: set[int] = set()

        for plan in plans:
            item_buttons = plan.asset.match_multi_template(self.device.image, threshold=30)
            if not item_buttons:
                continue
            item_buttons = sorted(item_buttons, key=self._button_center_y)

            best_action_index = -1
            best_distance = 9999.0
            for item_button in item_buttons:
                item_y = self._button_center_y(item_button)
                for idx, action_button in enumerate(action_buttons):
                    if idx in used_action_index:
                        continue
                    distance = abs(self._button_center_y(action_button) - item_y)
                    if distance < best_distance:
                        best_distance = distance
                        best_action_index = idx

            if best_action_index >= 0 and best_distance <= self.SUPPORT_ACTION_Y_TOLERANCE:
                used_action_index.add(best_action_index)
                pairs.append((plan.name, action_buttons[best_action_index]))

        pairs.sort(key=lambda row: row[1].area[1])
        return pairs

    def _enter_support(self, skip_first_screenshot=True) -> bool:
        logger.info("Knights: enter support")
        timeout = Timer(self.SUPPORT_ENTRY_TIMEOUT_SECONDS, count=60).start()

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning("Knights support entry timeout")
                return False

            if self.appear(SUPPORT_CHECK, interval=1):
                logger.info("Knights support page reached")
                return True

            if self.appear(page_knights.check_button):
                if self.appear_then_click(KNIGHTS_ACTIVITY_ENTRY, interval=self.SUPPORT_ENTRY_CLICK_INTERVAL_SECONDS):
                    logger.info("Knights: open activity support")
                    timeout.reset()
                    continue

            if self.is_in_main(interval=0):
                logger.warning("Knights support entry exited to main page unexpectedly")
                return False

            if self.handle_network_error():
                timeout.reset()
                continue

    def _run_support_donate(self, skip_first_screenshot=True) -> bool:
        plans = self._enabled_plans(self._build_donate_plans())
        if not plans:
            logger.info("Knights support donate disabled by config")
            return True

        logger.info("Knights support: donate")
        timeout = Timer(self.SUPPORT_DONATE_TIMEOUT_SECONDS, count=90).start()
        scroll_timer = Timer(self.SUPPORT_DONATE_SCROLL_INTERVAL_SECONDS, count=0).start()
        wait_touch_timeout = Timer(self.SUPPORT_DONATE_WAIT_TOUCH_TIMEOUT_SECONDS, count=6)
        empty_list_confirm = Timer(self.SUPPORT_DONATE_EMPTY_CONFIRM_SECONDS, count=4).clear()
        hit_bottom_confirm = 0
        skip_hit_bottom_once = False
        wait_touch_close = False

        self.interval_clear(SUPPORT_ACTION, interval=self.SUPPORT_DONATE_ACTION_INTERVAL_SECONDS)
        self.device.click_record_clear()

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning("Knights support donate timeout, stop scanning")
                return True

            if not self.appear(SUPPORT_CHECK):
                if self.appear(page_knights.check_button):
                    logger.warning("Knights support donate exited to knights page unexpectedly")
                    return False
                if self.is_in_main(interval=0):
                    logger.warning("Knights support donate exited to main page unexpectedly")
                    return False
                if self.handle_touch_to_close(interval=0.5):
                    timeout.reset()
                    continue
                if self.handle_network_error():
                    timeout.reset()
                    continue
                continue

            if wait_touch_close:
                if self.handle_touch_to_close(interval=0.5):
                    wait_touch_close = False
                    self.device.click_record_clear()
                    empty_list_confirm.clear()
                    timeout.reset()
                    continue
                if self.handle_network_error():
                    empty_list_confirm.clear()
                    timeout.reset()
                    continue
                if wait_touch_timeout.reached():
                    wait_touch_close = False
                else:
                    continue

            if self.handle_touch_to_close(interval=0.5):
                empty_list_confirm.clear()
                timeout.reset()
                continue
            if self.handle_network_error():
                empty_list_confirm.clear()
                timeout.reset()
                continue

            action_buttons = SUPPORT_ACTION.match_multi_template(self.device.image, threshold=40)
            complete_buttons = SUPPORT_COMPLETE.match_multi_template(self.device.image, threshold=40)

            if action_buttons or complete_buttons:
                empty_list_confirm.clear()
            else:
                if not empty_list_confirm.started():
                    logger.info("Knights support donate: no visible entries, confirm empty list")
                    empty_list_confirm.start()
                elif empty_list_confirm.reached():
                    logger.info("Knights support donate list is empty, skip scan")
                    return True
                continue

            pairs = self._find_donate_action_pairs(plans, action_buttons=action_buttons)
            if pairs:
                if not self.interval_is_reached(SUPPORT_ACTION, interval=self.SUPPORT_DONATE_ACTION_INTERVAL_SECONDS):
                    continue
                plan_name, action_button = pairs[0]
                logger.info(f"Knights support donate: {plan_name} -> {action_button}")
                self.device.click(action_button)
                self.interval_reset(SUPPORT_ACTION, interval=self.SUPPORT_DONATE_ACTION_INTERVAL_SECONDS)
                wait_touch_close = True
                wait_touch_timeout.reset()
                empty_list_confirm.clear()
                hit_bottom_confirm = 0
                timeout.reset()
                continue

            complete_count = len(complete_buttons)
            if complete_count >= self.SUPPORT_DONATE_COMPLETE_COUNT:
                logger.info(f"Knights support donate completed on current page: {complete_count}")
                return True

            if skip_hit_bottom_once:
                skip_hit_bottom_once = False
                hit_bottom_confirm = 0
            else:
                if self.appear(HIT_BOTTOM):
                    hit_bottom_confirm += 1
                    if hit_bottom_confirm >= 2:
                        logger.info("Knights support donate reached bottom")
                        return True
                else:
                    hit_bottom_confirm = 0

            if scroll_timer.reached():
                self.device.swipe(self.SUPPORT_SCROLL_START, self.SUPPORT_SCROLL_END, duration=(0.2, 0.3))
                scroll_timer.reset()
                empty_list_confirm.clear()
                skip_hit_bottom_once = True
                timeout.reset()
                continue

    def _run_support_request(self, skip_first_screenshot=True) -> bool:
        plan = self._resolve_request_plan()
        if not plan or not plan.enabled:
            logger.info("Knights support request disabled by config")
            return True

        logger.info("Knights support: request")
        timeout = Timer(self.SUPPORT_REQUEST_TIMEOUT_SECONDS, count=45).start()
        open_retry = Timer(4, count=12).start()
        choose_retry = Timer(6, count=18).start()
        panel_opened = False
        selected = False
        submitted = False

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning("Knights support request timeout")
                return True

            if submitted and self.appear(SUPPORT_CHECK):
                logger.info("Knights support request finished")
                return True

            if self.handle_touch_to_close(interval=0.5):
                timeout.reset()
                continue
            if self.handle_network_error():
                timeout.reset()
                continue

            if not panel_opened:
                if self.appear(REQUEST_ACTION):
                    panel_opened = True
                    selected = False
                    choose_retry.reset()
                    timeout.reset()
                    continue

                if self._is_request_for_support_entry_enabled(interval=1):
                    self.device.click(REQUEST_FOR_SUPPORT_ENTRY)
                    timeout.reset()
                    continue
                if self._is_request_for_support_entry_present(interval=1):
                    logger.info("Knights support request already used today, skip")
                    return True

                if self.is_in_main(interval=0):
                    logger.warning("Knights support request exited to main page unexpectedly")
                    return False
                if self.appear(SUPPORT_CHECK) and open_retry.reached():
                    logger.info("Knights support request unavailable, skip")
                    return True
                continue

            if not selected:
                if plan.selected_asset and self.appear(plan.selected_asset):
                    selected = True
                    logger.info(f"Knights support request selected: {plan.name}")
                    timeout.reset()
                    continue

                if self.appear_then_click(plan.asset, interval=1):
                    timeout.reset()
                    continue

                if self.appear(SUPPORT_CHECK):
                    panel_opened = False
                    selected = False
                    continue
                if choose_retry.reached():
                    logger.warning("Knights support request item selection not confirmed, skip")
                    return True
                continue

            if self.appear_then_click(REQUEST_ACTION, interval=1):
                submitted = True
                timeout.reset()
                continue

            if self.appear(SUPPORT_CHECK):
                logger.info("Knights support request returned to support page")
                return True

    def _back_to_knights_from_support(self, skip_first_screenshot=True) -> bool:
        self.ui_goto(page_knights, skip_first_screenshot=skip_first_screenshot)
        return True

    def run_support(self, skip_first_screenshot=True, run_donate=True, run_request=True) -> bool:
        logger.hr("Knights Support", level=2)

        if not self._enter_support(skip_first_screenshot=skip_first_screenshot):
            return False

        self._prepare_support_search_area()
        success = True

        if run_donate:
            success = self._run_support_donate(skip_first_screenshot=True) and success

        if run_request:
            if success:
                success = self._run_support_request(skip_first_screenshot=True) and success
            else:
                logger.warning("Knights support request skipped due to donate flow failure")

        return success
