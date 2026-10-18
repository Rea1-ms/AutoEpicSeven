from dataclasses import dataclass

from module.base.button import ButtonWrapper, ClickButton
from module.base.timer import Timer
from module.logger import logger
from tasks.base.page import page_knights_support
from tasks.knights.assets.assets_knights_activity_support_active import (
    ACTION_SEARCH,
    BEGGINER_PENGUIN,
    ITEM_SEARCH,
    LOWER_LEVEL_FAIRY_FLOWER,
    SUPPORT_ACTION,
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
from tasks.knights.donate import KnightsDonateMixin


@dataclass(frozen=True)
class SupportPlan:
    name: str
    asset: ButtonWrapper
    enabled: bool
    selected_asset: ButtonWrapper | None = None


class KnightsSupportMixin(KnightsDonateMixin):
    SUPPORT_ENTRY_TIMEOUT_SECONDS = 20
    SUPPORT_ENTRY_CLICK_INTERVAL_SECONDS = 1.2
    SUPPORT_ACTION_Y_TOLERANCE = 80
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
                enabled=self.config.Knights_SupportLowerLevelFairyFlower,
            ),
            SupportPlan(
                name="beginner_penguin",
                asset=BEGGINER_PENGUIN,
                enabled=self.config.Knights_SupportBeginnerPenguin,
            ),
        ]

    def _resolve_request_plan(self) -> SupportPlan | None:
        request_item = self.config.Knights_RequestItem
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

            for item_button in item_buttons:
                item_y = self._button_center_y(item_button)
                candidates = [(abs(self._button_center_y(action) - item_y), idx)
                              for idx, action in enumerate(action_buttons)
                              if idx not in used_action_index]
                if not candidates:
                    continue
                distance, idx = min(candidates)
                if distance <= self.SUPPORT_ACTION_Y_TOLERANCE:
                    used_action_index.add(idx)
                    pairs.append((plan.name, action_buttons[idx]))

        pairs.sort(key=lambda row: row[1].area[1])
        return pairs

    def _enter_support(self, skip_first_screenshot=True) -> bool:
        """Use the shared routes, including another open activity tab.

        Pages:
            in: a registered game page.
            out: page_knights_support.
        """
        logger.info("Knights: enter support")
        self.ui_goto(page_knights_support, skip_first_screenshot=skip_first_screenshot)
        return True

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
