from dataclasses import dataclass

from module.base.button import ButtonWrapper, ClickButton
from module.base.timer import Timer
from module.logger import logger
from tasks.base.page import page_knights, page_knights_support
from tasks.knights.assets.assets_knights_support import (
    ACTION_SEARCH,
    BEGGINER_PENGUIN,
    BEGGINER_PENGUIN_SELECTED,
    HIT_BOTTOM,
    ITEM_SEARCH,
    LOWER_LEVEL_FAIRY_FLOWER,
    LOWER_LEVEL_FAIRY_FLOWER_SELECTED,
    REQUEST_ACTION,
    REQUEST_BEGGINER_PENGUIN,
    REQUEST_FOR_SUPPORT,
    REQUEST_LOWER_LEVEL_FAIRY_FLOWER,
    SUPPORT_ACTION,
    SUPPORT_CHECK,
    SUPPORT_COMPLETE,
)


@dataclass(frozen=True)
class SupportPlan:
    name: str
    asset: ButtonWrapper
    enabled: bool
    selected_asset: ButtonWrapper | None = None


class KnightsSupportMixin:
    SUPPORT_ACTION_Y_TOLERANCE = 80
    SUPPORT_DONATE_TIMEOUT_SECONDS = 30
    SUPPORT_DONATE_SCROLL_INTERVAL_SECONDS = 0.8
    SUPPORT_DONATE_ACTION_INTERVAL_SECONDS = 0.8
    SUPPORT_DONATE_WAIT_TOUCH_TIMEOUT_SECONDS = 2
    SUPPORT_DONATE_COMPLETE_COUNT = 4
    SUPPORT_REQUEST_TIMEOUT_SECONDS = 15
    SUPPORT_SCROLL_START = (640, 620)
    SUPPORT_SCROLL_END = (640, 320)
    REQUEST_FOR_SUPPORT_LUMA_SIMILARITY = 0.8
    REQUEST_FOR_SUPPORT_COLOR_THRESHOLD = 10

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
        """
        Resolve one request target from single-select config.

        Backward compatibility:
            If the new select option is not available, fallback to legacy booleans.
        """
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
                selected_asset=LOWER_LEVEL_FAIRY_FLOWER_SELECTED,
            )
        if request_item == "BeginnerPenguin":
            return SupportPlan(
                name="request_beginner_penguin",
                asset=REQUEST_BEGGINER_PENGUIN,
                enabled=True,
                selected_asset=BEGGINER_PENGUIN_SELECTED,
            )
        return None

    @staticmethod
    def _enabled_plans(plans: list[SupportPlan]) -> list[SupportPlan]:
        return [plan for plan in plans if plan.enabled]

    def _is_request_for_support_enabled(self, interval=0) -> bool:
        """
        REQUEST_FOR_SUPPORT requires luma + color:
        - luma: button exists
        - color: button is enabled (not greyed out after daily request is used)
        """
        self.device.stuck_record_add(REQUEST_FOR_SUPPORT)

        if interval and not self.interval_is_reached(REQUEST_FOR_SUPPORT, interval=interval):
            return False

        appear = False
        if REQUEST_FOR_SUPPORT.match_template_luma(self.device.image, similarity=self.REQUEST_FOR_SUPPORT_LUMA_SIMILARITY):
            if REQUEST_FOR_SUPPORT.match_color(self.device.image, threshold=self.REQUEST_FOR_SUPPORT_COLOR_THRESHOLD):
                appear = True

        if appear and interval:
            self.interval_reset(REQUEST_FOR_SUPPORT, interval=interval)

        return appear

    def _is_request_for_support_present(self, interval=0) -> bool:
        return self.match_template_luma(
            REQUEST_FOR_SUPPORT,
            interval=interval,
            similarity=self.REQUEST_FOR_SUPPORT_LUMA_SIMILARITY,
        )

    def _find_donate_action_pairs(self, plans: list[SupportPlan]) -> list[tuple[str, ClickButton]]:
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
        self.ui_goto(page_knights_support, skip_first_screenshot=skip_first_screenshot)
        return True

    def _run_support_donate(self, skip_first_screenshot=True) -> bool:
        plans = self._enabled_plans(self._build_donate_plans())
        if not plans:
            logger.info("Support donate disabled by config")
            return True

        logger.info("Knights support: donate")
        timeout = Timer(self.SUPPORT_DONATE_TIMEOUT_SECONDS, count=90).start()
        scroll_timer = Timer(self.SUPPORT_DONATE_SCROLL_INTERVAL_SECONDS, count=0).start()
        wait_touch_timeout = Timer(self.SUPPORT_DONATE_WAIT_TOUCH_TIMEOUT_SECONDS, count=6)
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
                logger.warning("Support donate timeout, stop scanning")
                return True

            if not self.appear(SUPPORT_CHECK):
                if self.appear(page_knights.check_button):
                    logger.warning("Support donate exited support page unexpectedly")
                    return False
                if self.is_in_main(interval=0):
                    logger.warning("Support donate exited to main page unexpectedly")
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
                    timeout.reset()
                    continue
                if self.handle_network_error():
                    timeout.reset()
                    continue
                if wait_touch_timeout.reached():
                    wait_touch_close = False
                else:
                    continue

            if self.handle_touch_to_close(interval=0.5):
                timeout.reset()
                continue
            if self.handle_network_error():
                timeout.reset()
                continue

            pairs = self._find_donate_action_pairs(plans)
            if pairs:
                if not self.interval_is_reached(SUPPORT_ACTION, interval=self.SUPPORT_DONATE_ACTION_INTERVAL_SECONDS):
                    continue
                plan_name, action_button = pairs[0]
                logger.info(f"Support donate: {plan_name} -> {action_button}")
                self.device.click(action_button)
                self.interval_reset(SUPPORT_ACTION, interval=self.SUPPORT_DONATE_ACTION_INTERVAL_SECONDS)
                wait_touch_close = True
                wait_touch_timeout.reset()
                hit_bottom_confirm = 0
                timeout.reset()
                continue

            complete_count = len(SUPPORT_COMPLETE.match_multi_template(self.device.image, threshold=40))
            if complete_count >= self.SUPPORT_DONATE_COMPLETE_COUNT:
                logger.info(f"Support donate completed on current page: {complete_count}")
                return True

            if skip_hit_bottom_once:
                skip_hit_bottom_once = False
                hit_bottom_confirm = 0
            else:
                if self.appear(HIT_BOTTOM):
                    hit_bottom_confirm += 1
                    if hit_bottom_confirm >= 2:
                        logger.info("Support donate reached bottom")
                        return True
                else:
                    hit_bottom_confirm = 0

            if scroll_timer.reached():
                self.device.swipe(self.SUPPORT_SCROLL_START, self.SUPPORT_SCROLL_END, duration=(0.2, 0.3))
                scroll_timer.reset()
                skip_hit_bottom_once = True
                timeout.reset()
                continue

    def _run_support_request(self, skip_first_screenshot=True) -> bool:
        plan = self._resolve_request_plan()
        if not plan or not plan.enabled:
            logger.info("Support request disabled by config")
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
                logger.warning("Support request timeout")
                return True

            if submitted and self.appear(SUPPORT_CHECK):
                logger.info("Support request finished")
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
                if self._is_request_for_support_enabled(interval=1):
                    self.device.click(REQUEST_FOR_SUPPORT)
                    timeout.reset()
                    continue
                if self._is_request_for_support_present(interval=1):
                    logger.info("Support request already used today, skip")
                    return True
                if self.is_in_main(interval=0):
                    logger.warning("Support request exited to main page unexpectedly")
                    return False
                if self.appear(SUPPORT_CHECK) and open_retry.reached():
                    logger.info("Support request unavailable, skip")
                    return True
                continue

            if not selected:
                if plan.selected_asset and self.appear(plan.selected_asset):
                    selected = True
                    logger.info(f"Support request selected: {plan.name}")
                    timeout.reset()
                    continue

                if self.appear_then_click(plan.asset, interval=1):
                    timeout.reset()
                    continue

                if self.appear(SUPPORT_CHECK):
                    # Request panel did not open; keep open_retry running so this
                    # branch can eventually skip instead of looping forever.
                    panel_opened = False
                    selected = False
                    continue
                if choose_retry.reached():
                    logger.warning("Support request item selection not confirmed, skip")
                    return True
                continue

            if self.appear_then_click(REQUEST_ACTION, interval=1):
                submitted = True
                timeout.reset()
                continue

            if self.appear(SUPPORT_CHECK):
                logger.info("Support request returned to support page")
                return True

    def _back_to_knights_from_support(self, skip_first_screenshot=True) -> bool:
        self.ui_goto(page_knights, skip_first_screenshot=skip_first_screenshot)
        return True

    def run_support(self, skip_first_screenshot=True) -> bool:
        logger.hr("Knights Support", level=2)

        if not self._enter_support(skip_first_screenshot=skip_first_screenshot):
            return False

        self._prepare_support_search_area()
        success = self._run_support_donate(skip_first_screenshot=True)
        if success:
            success = self._run_support_request(skip_first_screenshot=True) and success
        else:
            logger.warning("Support request skipped due to donate flow failure")
        return success
