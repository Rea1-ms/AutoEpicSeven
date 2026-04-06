from datetime import datetime
from pathlib import Path
from typing import Literal

from module.base.button import ButtonWrapper, ClickButton
from module.base.timer import Timer
from module.base.utils import save_image
from module.logger import logger
from tasks.base.page import (
    page_common_store,
    page_conquest_points_store,
    page_free_store,
    page_inheritance_stone_store,
    page_store,
)
from tasks.base.ui import UI
from tasks.store.purchase import (
    ItemPurchasePlan,
    PurchaseCounterPreset,
    PurchaseResult,
    counter_to_text,
    is_valid_purchase_counter,
    normalize_config_purchase_quantity,
    ocr_purchase_counter,
    ocr_remaining_buy_times,
    plan_purchase_selection,
    resolve_period_purchase_quantity,
)
from tasks.store.assets.assets_store_actions import (
    BUY_CONFIRM_MULTI,
    BUY_CONFIRM_SINGLE,
    BUY_MAX,
    BUY_MIN,
    BUY_TIMES_MINUS,
    BUY_TIMES_PLUS,
    OCR_BUY_TIMES,
    OCR_REMAINING_BUY_TIMES,
)
from tasks.store.assets.assets_store_current_items import (
    ARENA_FLAG,
    DAILY_FREE_ITEM,
    EQUIPMENT_REFORGING_STONE_SELECTION_CHEST,
    ITEM_IN_CD,
    LESSER_ARTIFACT_CHARM,
    MOBILITY_40,
    MOROGORA,
    STORE_ITEMS_SEARCH,
)

PurchasePopupLayout = Literal['unknown', 'single', 'multi']


class CurrentStore(UI):
    CONFIRM_SIMILARITY = 0.85
    INHERITANCE_SCROLL_START = (1060, 392)
    INHERITANCE_SCROLL_END = (720, 392)
    PURCHASE_SWITCH_COOLDOWN_SECONDS = 1
    POST_PURCHASE_SETTLE_TIMEOUT_SECONDS = 4
    POST_PURCHASE_STABLE_FRAMES = 2
    INHERITANCE_SCROLL_INTERVAL_SECONDS = 1
    # Purchase popup counter area is shared across store items.
    # If only one purchase remains, the `x/y` counter may disappear.
    BUY_COUNTER_AREA = OCR_BUY_TIMES.area
    REMAINING_BUY_TIMES_AREA = OCR_REMAINING_BUY_TIMES.area

    def __init__(self, config, device, task='Store'):
        super().__init__(config, device=device, task=task)
        self.buy_confirm_multi_asset: ButtonWrapper = BUY_CONFIRM_MULTI
        self.buy_confirm_single_asset: ButtonWrapper = BUY_CONFIRM_SINGLE
        self.buy_max_asset: ButtonWrapper = BUY_MAX
        self.buy_min_asset: ButtonWrapper = BUY_MIN
        self.buy_times_minus_asset: ButtonWrapper = BUY_TIMES_MINUS
        self.buy_times_plus_asset: ButtonWrapper = BUY_TIMES_PLUS
        self._purchase_switch_cooldown = Timer(self.PURCHASE_SWITCH_COOLDOWN_SECONDS, count=0)
        self.purchase_stats: dict[str, int] = {}

    def _build_free_store_items(self) -> list[ItemPurchasePlan]:
        return [
            ItemPurchasePlan(
                name='daily_free_item',
                asset=DAILY_FREE_ITEM,
                desired_quantity=1 if self.config.StoreDaily_BuyDailyFreeItem else 0,
                direct_click=True,
            ),
            ItemPurchasePlan(
                name='free_mobility_40',
                asset=MOBILITY_40,
                desired_quantity=1 if self.config.StoreDaily_BuyFriendshipMobility40 else 0,
                quantity_strategy='once',
                direct_click=True,
            ),
            ItemPurchasePlan(
                name='free_arena_flag',
                asset=ARENA_FLAG,
                desired_quantity=1 if self.config.StoreDaily_BuyFriendshipArenaFlag else 0,
                quantity_strategy='once',
                direct_click=True,
            ),
            ItemPurchasePlan(
                name='free_lesser_artifact_charm',
                asset=LESSER_ARTIFACT_CHARM,
                # Keep using the legacy friendship field until the shared store
                # config schema can be renamed for the new oversea UI.
                desired_quantity=normalize_config_purchase_quantity(
                    self.config.StoreWeekly_BuyFriendshipArtifactEnhancementStone,
                    maximum=3,
                ),
                quantity_strategy='target',
                direct_click=True,
                counter_preset=PurchaseCounterPreset(
                    name='StoreFreeLesserArtifactCharmCounter',
                    area=self.BUY_COUNTER_AREA,
                ),
                purchase_limit=3,
                remaining_counter_preset=PurchaseCounterPreset(
                    name='StoreFreeLesserArtifactCharmRemainingTimes',
                    area=self.REMAINING_BUY_TIMES_AREA,
                ),
            ),
        ]

    def _build_inheritance_store_items(self) -> list[ItemPurchasePlan]:
        return [
            ItemPurchasePlan(
                name='inheritance_reforging_stone_selection_chest',
                asset=EQUIPMENT_REFORGING_STONE_SELECTION_CHEST,
                # Temporary mapping from the legacy potential-fragments option.
                desired_quantity=normalize_config_purchase_quantity(
                    self.config.StoreWeekly_BuyInheritancePotentialFragments,
                    maximum=1,
                ),
                quantity_strategy='once',
                direct_click=True,
            ),
            ItemPurchasePlan(
                name='inheritance_morogora',
                asset=MOROGORA,
                desired_quantity=normalize_config_purchase_quantity(
                    self.config.StoreWeekly_BuyInheritanceMorogora,
                    maximum=2,
                ),
                quantity_strategy='target',
                direct_click=True,
                counter_preset=PurchaseCounterPreset(
                    name='StoreInheritanceMorogoraCounter',
                    area=self.BUY_COUNTER_AREA,
                ),
                purchase_limit=2,
                remaining_counter_preset=PurchaseCounterPreset(
                    name='StoreInheritanceMorogoraRemainingTimes',
                    area=self.REMAINING_BUY_TIMES_AREA,
                ),
            ),
        ]

    def _build_conquest_store_items(self) -> list[ItemPurchasePlan]:
        return [
            ItemPurchasePlan(
                name='conquest_morogora',
                asset=MOROGORA,
                desired_quantity=1 if self.config.StoreWeekly_BuyConquestMorogora else 0,
                quantity_strategy='once',
                direct_click=True,
            ),
            ItemPurchasePlan(
                name='conquest_mobility_40',
                asset=MOBILITY_40,
                desired_quantity=normalize_config_purchase_quantity(
                    self.config.StoreDaily_BuyConquestMobility40,
                    maximum=3,
                ),
                quantity_strategy='target',
                direct_click=True,
                counter_preset=PurchaseCounterPreset(
                    name='StoreConquestMobility40Counter',
                    area=self.BUY_COUNTER_AREA,
                ),
                purchase_limit=3,
                remaining_counter_preset=PurchaseCounterPreset(
                    name='StoreConquestMobility40RemainingTimes',
                    area=self.REMAINING_BUY_TIMES_AREA,
                ),
            ),
        ]

    @staticmethod
    def _enabled_items(items: list[ItemPurchasePlan]) -> list[ItemPurchasePlan]:
        return [item for item in items if item.desired_quantity > 0]

    def _load_shared_item_search(self):
        for button in (
            ARENA_FLAG,
            DAILY_FREE_ITEM,
            EQUIPMENT_REFORGING_STONE_SELECTION_CHEST,
            ITEM_IN_CD,
            LESSER_ARTIFACT_CHARM,
            MOBILITY_40,
            MOROGORA,
        ):
            button.load_search(STORE_ITEMS_SEARCH.area)

    def _save_debug_image(self, tag: str):
        now = datetime.now()
        folder = Path('log/store_debug') / now.strftime('%Y%m%d')
        folder.mkdir(parents=True, exist_ok=True)
        image_path = folder / f"{now.strftime('%Y%m%d_%H%M%S_%f')}_{tag}.png"
        save_image(self.device.image, str(image_path))
        logger.info(f'Store debug image: {image_path}')

    def _log_purchase_debug(self, item_name: str, item_asset: ButtonWrapper, reason: str):
        item_matches = item_asset.match_multi_template(self.device.image, threshold=30)
        item_purchaseable = item_asset.match_template_color(self.device.image, threshold=30)
        single_confirm = self.appear(
            self.buy_confirm_single_asset,
            interval=0,
            similarity=self.CONFIRM_SIMILARITY,
        )
        multi_confirm = self.appear(
            self.buy_confirm_multi_asset,
            interval=0,
            similarity=self.CONFIRM_SIMILARITY,
        )
        layout = self._detect_purchase_popup_layout()
        logger.warning(
            f'[StoreDebug] {item_name} {reason}: item_matches={len(item_matches)}, '
            f'item_purchaseable={item_purchaseable}, single_confirm={single_confirm}, '
            f'multi_confirm={multi_confirm}, layout={layout}, in_store={self._is_on_any_store_page()}'
        )
        if item_matches:
            logger.info(f'[StoreDebug] {item_name} item_areas={ [m.area for m in item_matches[:3]] }')
        self._save_debug_image(f'{item_name}_{reason}')

    @staticmethod
    def _button_center_x(button: ClickButton) -> float:
        return (button.area[0] + button.area[2]) / 2

    @staticmethod
    def _button_center_y(button: ClickButton) -> float:
        return (button.area[1] + button.area[3]) / 2

    @staticmethod
    def _has_item(item_asset: ButtonWrapper, image) -> bool:
        return len(item_asset.match_multi_template(image, threshold=30)) > 0

    @staticmethod
    def _first_item_button(item_asset: ButtonWrapper, image, axis='y') -> ClickButton | None:
        matches = item_asset.match_multi_template(image, threshold=30)
        if not matches:
            return None

        if axis == 'x':
            return sorted(matches, key=lambda x: (x.area[0], x.area[1]))[0]
        return sorted(matches, key=lambda x: (x.area[1], x.area[0]))[0]

    def _is_on_any_store_page(self) -> bool:
        for page in (
            page_free_store,
            page_inheritance_stone_store,
            page_conquest_points_store,
            page_common_store,
            page_store,
        ):
            if self.ui_page_appear(page, interval=0):
                return True
        return False

    def _match_purchaseable_item(self, item_asset: ButtonWrapper) -> bool:
        """
        Current oversea store enters the purchase popup by clicking the item card
        itself. A sold-out card usually stays in place but turns gray, so plain
        template presence is not enough here:

        1. `match_template*()` answers "did this card exist in the viewport?"
        2. `match_template_color()` answers "did we locate this exact card and
           is it still in the active color state?"

        That matches the new UI semantics much better than the legacy
        "item + BUY button" pairing logic.
        """
        return item_asset.match_template_color(self.device.image, threshold=30)

    def _item_ready_for_purchase(self, item: ItemPurchasePlan) -> bool:
        return self._match_purchaseable_item(item.asset)

    def _click_purchase_target(self, item: ItemPurchasePlan) -> bool:
        if not self._match_purchaseable_item(item.asset):
            return False

        logger.info(f'{item.name} -> {item.asset.button}')
        self.device.click(item.asset)
        self._clear_purchase_popup_intervals()
        return True

    def _clear_purchase_popup_intervals(self) -> None:
        self.interval_clear(self.buy_confirm_single_asset)
        self.interval_clear(self.buy_confirm_multi_asset)
        self.interval_clear(self.buy_min_asset)
        self.interval_clear(self.buy_max_asset)
        self.interval_clear(self.buy_times_minus_asset)
        self.interval_clear(self.buy_times_plus_asset)

    def _click_purchase_quantity_action(self, action: str) -> bool:
        if action == 'min':
            return self.appear_then_click(self.buy_min_asset, interval=1, similarity=0.8)
        if action == 'max':
            return self.appear_then_click(self.buy_max_asset, interval=1, similarity=0.8)
        if action == 'plus':
            return self.appear_then_click(self.buy_times_plus_asset, interval=1, similarity=0.8)
        if action == 'minus':
            return self.appear_then_click(self.buy_times_minus_asset, interval=1, similarity=0.8)
        return False

    @staticmethod
    def _needs_remaining_target_resolution(item: ItemPurchasePlan) -> bool:
        return (
            item.quantity_strategy == 'target'
            and item.purchase_limit > 1
            and item.remaining_counter_preset is not None
        )

    @staticmethod
    def _requires_quantity_adjustment(strategy: str, desired_quantity: int) -> bool:
        return strategy == 'max' or (strategy == 'target' and desired_quantity > 1)

    def _has_purchase_quantity_controls(self) -> bool:
        return (
            self.appear(self.buy_max_asset, interval=0, similarity=0.8)
            or self.appear(self.buy_min_asset, interval=0, similarity=0.8)
            or self.appear(self.buy_times_plus_asset, interval=0, similarity=0.8)
            or self.appear(self.buy_times_minus_asset, interval=0, similarity=0.8)
        )

    def _detect_purchase_popup_layout(self) -> PurchasePopupLayout:
        if self._has_purchase_quantity_controls():
            return 'multi'
        if self.appear(
            self.buy_confirm_multi_asset,
            interval=0,
            similarity=self.CONFIRM_SIMILARITY,
        ):
            return 'multi'
        if self.appear(
            self.buy_confirm_single_asset,
            interval=0,
            similarity=self.CONFIRM_SIMILARITY,
        ):
            return 'single'
        return 'unknown'

    def _click_purchase_confirm(self, layout: PurchasePopupLayout, interval=1) -> bool:
        if layout == 'multi':
            return self.appear_then_click(
                self.buy_confirm_multi_asset,
                interval=interval,
                similarity=self.CONFIRM_SIMILARITY,
            )
        if layout == 'single':
            return self.appear_then_click(
                self.buy_confirm_single_asset,
                interval=interval,
                similarity=self.CONFIRM_SIMILARITY,
            )
        if self.appear_then_click(
            self.buy_confirm_multi_asset,
            interval=interval,
            similarity=self.CONFIRM_SIMILARITY,
        ):
            return True
        return self.appear_then_click(
            self.buy_confirm_single_asset,
            interval=interval,
            similarity=self.CONFIRM_SIMILARITY,
        )

    def _close_purchase_popup_without_confirm(self, interval=1) -> bool:
        """
        Explicitly cancel the current store purchase popup.

        In store purchase popups, `POPUP_CANCEL` is the only valid "do not buy"
        action. After the click is sent, the popup should naturally transition
        back to the current sub-store unless a network error interrupts it.
        Generic `TOUCH_TO_CLOSE` belongs to free-item reward popups handled by
        global UI logic, not to ordinary store purchase cancellation.
        """
        return self.handle_popup_cancel(interval=interval)

    def _ocr_purchase_counter(self, item: ItemPurchasePlan) -> tuple[int, int, int]:
        if item.counter_preset is None:
            return 0, 0, 0

        counter = ocr_purchase_counter(self.device.image, self.config, item.counter_preset)
        if is_valid_purchase_counter(counter):
            if item.purchase_limit > 1 and counter[2] > item.purchase_limit:
                logger.warning(
                    f'{item.name}: purchase counter total={counter[2]} exceeds '
                    f'purchase_limit={item.purchase_limit}'
                )
                return 0, 0, 0
            logger.attr(f'{item.name}.BuyCounter', counter_to_text(counter))
        else:
            logger.warning(f'{item.name}: invalid purchase counter OCR')
        return counter

    def _ocr_remaining_purchase_times(self, item: ItemPurchasePlan) -> int:
        if item.remaining_counter_preset is None:
            return 0

        remaining_times = ocr_remaining_buy_times(
            self.device.image,
            self.config,
            item.remaining_counter_preset,
        )
        if remaining_times <= 0:
            return 0
        if remaining_times > item.purchase_limit:
            logger.warning(
                f'{item.name}: remaining buy times OCR={remaining_times} exceeds '
                f'purchase_limit={item.purchase_limit}'
            )
            return 0
        logger.attr(f'{item.name}.RemainingBuyTimes', remaining_times)
        return remaining_times

    def _resolve_remaining_purchase_times(
        self,
        item: ItemPurchasePlan,
        layout: PurchasePopupLayout,
    ) -> tuple[int, tuple[int, int, int], str]:
        if layout == 'single':
            logger.info(f'{item.name}: single popup layout, remaining buy times=1')
            return 1, (0, 0, 0), 'single_layout'
        if layout != 'multi':
            return 0, (0, 0, 0), 'pending'

        remaining_times = self._ocr_remaining_purchase_times(item)
        if remaining_times > 0:
            return remaining_times, (0, 0, 0), 'remaining_counter'

        purchase_counter = self._ocr_purchase_counter(item)
        if is_valid_purchase_counter(purchase_counter):
            _, _, total = purchase_counter
            logger.info(
                f'{item.name}: fallback remaining buy times from purchase counter total={total}'
            )
            return total, purchase_counter, 'purchase_counter_total'

        return 0, (0, 0, 0), 'pending'

    def _record_purchase_result(self, item: ItemPurchasePlan, result: PurchaseResult) -> None:
        if not result.success:
            return

        self.purchase_stats[item.name] = self.purchase_stats.get(item.name, 0) + result.quantity
        logger.info(
            f'{item.name} purchased quantity={result.quantity} '
            f'(source={result.quantity_source})'
        )
        if is_valid_purchase_counter(result.counter):
            logger.info(f'{item.name} counter={counter_to_text(result.counter)}')

    def _purchase_item(self, item: ItemPurchasePlan) -> PurchaseResult:
        logger.info(f'Purchase {item.name}')
        timeout = Timer(12, count=30).start()
        clicked_target = False
        clicked_confirm = False
        clicked_cancel = False
        layout: PurchasePopupLayout = 'unknown'
        needs_target_resolution = self._needs_remaining_target_resolution(item)
        effective_desired_quantity = item.desired_quantity
        pending_target_resolution = needs_target_resolution
        pending_quantity_adjustment = (
            False
            if pending_target_resolution
            else self._requires_quantity_adjustment(item.quantity_strategy, effective_desired_quantity)
        )
        quantity_resolved = (not pending_target_resolution) and (not pending_quantity_adjustment)
        purchase_counter = (0, 0, 0)
        purchase_quantity = 1
        quantity_source = 'default'
        skipping_purchase = False
        target_retry = Timer(0.8, count=2).start()

        while 1:
            self.device.screenshot()

            if timeout.reached():
                logger.warning(f'Purchase {item.name} timeout')
                self._log_purchase_debug(item.name, item.asset, 'timeout')
                return PurchaseResult(success=False)

            if self.ui_additional():
                timeout.reset()
                continue
            if self.handle_network_error():
                timeout.reset()
                continue

            current_layout = self._detect_purchase_popup_layout() if clicked_target else 'unknown'
            if current_layout != 'unknown' and current_layout != layout:
                layout = current_layout
                logger.info(f'{item.name}: purchase popup layout={layout}')
                timeout.reset()
            popup_active = current_layout != 'unknown'
            if skipping_purchase and self._is_on_any_store_page() and not popup_active:
                logger.info(f'{item.name}: target total already reached, skip purchase')
                return PurchaseResult(success=False, quantity=0, quantity_source='target_reached')

            if clicked_confirm or clicked_cancel:
                store_visible = self._is_on_any_store_page()
                item_visible = self._has_item(item.asset, self.device.image)

                if clicked_cancel and store_visible and not popup_active:
                    logger.info(f'{item.name}: target total already reached, skip purchase')
                    return PurchaseResult(success=False, quantity=0, quantity_source='target_reached')
                if clicked_confirm and store_visible and not popup_active and (
                    item.direct_click or not item_visible
                ):
                    return PurchaseResult(
                        success=True,
                        quantity=purchase_quantity,
                        counter=purchase_counter,
                        quantity_source=quantity_source,
                    )

            if not clicked_target:
                stale_cleared = False
                if self._click_purchase_confirm('unknown', interval=1):
                    stale_cleared = True
                if stale_cleared:
                    timeout.reset()
                    continue

                if self._click_purchase_target(item):
                    clicked_target = True
                    layout = 'unknown'
                    pending_target_resolution = needs_target_resolution
                    effective_desired_quantity = item.desired_quantity
                    pending_quantity_adjustment = (
                        False
                        if pending_target_resolution
                        else self._requires_quantity_adjustment(
                            item.quantity_strategy,
                            effective_desired_quantity,
                        )
                    )
                    quantity_resolved = (not pending_target_resolution) and (not pending_quantity_adjustment)
                    purchase_counter = (0, 0, 0)
                    purchase_quantity = 1
                    quantity_source = 'default'
                    target_retry.reset()
                    timeout.reset()
                    continue

                logger.warning(f'{item.name} not found in current store page')
                self._log_purchase_debug(item.name, item.asset, 'not_found')
                return PurchaseResult(success=False)

            progress = False

            if layout == 'unknown':
                if target_retry.reached() and not popup_active:
                    if self._click_purchase_target(item):
                        layout = 'unknown'
                        target_retry.reset()
                        timeout.reset()
                        continue
                timeout.reset()
                continue

            if pending_target_resolution:
                remaining_times, resolved_counter, remaining_source = self._resolve_remaining_purchase_times(
                    item,
                    layout,
                )
                if remaining_times <= 0:
                    timeout.reset()
                    continue

                already_bought, effective_desired_quantity = resolve_period_purchase_quantity(
                    desired_total=item.desired_quantity,
                    purchase_limit=item.purchase_limit,
                    remaining_times=remaining_times,
                )
                logger.info(
                    f'{item.name}: desired_total={item.desired_quantity}, '
                    f'purchase_limit={item.purchase_limit}, remaining_times={remaining_times}, '
                    f'already_bought={already_bought}, need_to_buy={effective_desired_quantity}'
                )
                purchase_counter = resolved_counter
                pending_target_resolution = False
                purchase_quantity = effective_desired_quantity
                quantity_source = remaining_source
                pending_quantity_adjustment = (
                    layout == 'multi'
                    and self._requires_quantity_adjustment(
                        item.quantity_strategy,
                        effective_desired_quantity,
                    )
                )
                quantity_resolved = not pending_quantity_adjustment

                if effective_desired_quantity <= 0:
                    # Once the configured period target is already satisfied, do
                    # not touch confirm again. Store purchase popups have an
                    # explicit "do not buy" button, so after entering this state
                    # we only send POPUP_CANCEL once and then wait for the
                    # sub-store page to settle again.
                    skipping_purchase = True
                    purchase_quantity = 0
                    quantity_source = 'target_reached'
                    quantity_resolved = False
                    pending_quantity_adjustment = False
                    if not clicked_cancel and self._close_purchase_popup_without_confirm(interval=1):
                        clicked_cancel = True
                        progress = True
                    if progress:
                        timeout.reset()
                    continue

                timeout.reset()
                if progress or quantity_resolved:
                    continue

            if skipping_purchase:
                if not clicked_cancel and self._close_purchase_popup_without_confirm(interval=1):
                    clicked_cancel = True
                    timeout.reset()
                    continue
                continue

            if pending_quantity_adjustment:
                if layout != 'multi':
                    timeout.reset()
                    continue
                if not is_valid_purchase_counter(purchase_counter):
                    purchase_counter = self._ocr_purchase_counter(item)
                if is_valid_purchase_counter(purchase_counter):
                    selection = plan_purchase_selection(
                        strategy=item.quantity_strategy,
                        counter=purchase_counter,
                        desired_quantity=effective_desired_quantity,
                        fallback=1,
                    )
                    if selection.action != 'none':
                        logger.info(
                            f'{item.name}: adjust quantity action={selection.action} '
                            f'target={selection.quantity}'
                        )
                        if self._click_purchase_quantity_action(selection.action):
                            progress = True
                        timeout.reset()
                        purchase_counter = (0, 0, 0)
                        continue

                    purchase_quantity = selection.quantity
                    if quantity_source == 'default':
                        quantity_source = selection.source
                    pending_quantity_adjustment = False
                    quantity_resolved = True
                else:
                    timeout.reset()
                    continue

            if quantity_resolved and self._click_purchase_confirm(layout, interval=1):
                clicked_confirm = True
                progress = True

            if progress:
                timeout.reset()
                continue

            current_layout = self._detect_purchase_popup_layout()
            popup_active = current_layout != 'unknown'
            item_visible = self._has_item(item.asset, self.device.image)

            if clicked_confirm and not popup_active and not item_visible:
                return PurchaseResult(
                    success=True,
                    quantity=purchase_quantity,
                    counter=purchase_counter,
                    quantity_source=quantity_source,
                )
            if clicked_confirm and self._is_on_any_store_page() and not popup_active:
                return PurchaseResult(
                    success=True,
                    quantity=purchase_quantity,
                    counter=purchase_counter,
                    quantity_source=quantity_source,
                )

            # If nothing settled and no confirm is visible, retry the first step once more.
            if (
                (not clicked_confirm)
                and (not popup_active)
                and target_retry.reached()
            ):
                if self._click_purchase_target(item):
                    layout = 'unknown'
                    target_retry.reset()
                    timeout.reset()
                    continue

    def _record_purchase_time(self) -> None:
        self._purchase_switch_cooldown.reset()

    def _wait_purchase_cooldown_before_switch(self) -> None:
        """
        Ensure enough gap from last successful purchase before entering another store page.
        This avoids first-item detection being blocked by lingering purchase-success layers.
        """
        if not self._purchase_switch_cooldown.started():
            return

        logger.info(f'Wait purchase cooldown before store switch: {self.PURCHASE_SWITCH_COOLDOWN_SECONDS}s')
        while 1:
            self.device.screenshot()

            if self.ui_additional():
                continue
            if self.handle_network_error(interval=0.2):
                continue
            if self._detect_purchase_popup_layout() != 'unknown':
                continue
            if not self._is_on_any_store_page():
                continue
            if self._purchase_switch_cooldown.reached():
                return

    def _wait_store_ready_after_purchase(self) -> bool:
        """
        Wait until the store view is genuinely reusable after a successful buy.

        This follows the same idea used in StarRailCopilot reward/popup flows:
        an action can finish first, while its follow-up reward layer appears a
        little later. Reusing the very first "store visible" frame is unsafe,
        because that late `TOUCH_TO_CLOSE` layer may still cover the next item
        scan. Require a couple of clean store frames in a row so later item
        searches always start from an unobscured screenshot.
        """
        timeout = Timer(self.POST_PURCHASE_SETTLE_TIMEOUT_SECONDS, count=12).start()
        stable_count = 0

        while 1:
            self.device.screenshot()

            if timeout.reached():
                logger.warning('Store: post-purchase settle timeout')
                return False

            if self.ui_additional():
                timeout.reset()
                stable_count = 0
                continue
            if self.handle_network_error(interval=0.2):
                timeout.reset()
                stable_count = 0
                continue
            if self._detect_purchase_popup_layout() != 'unknown':
                timeout.reset()
                stable_count = 0
                continue
            if not self._is_on_any_store_page():
                stable_count = 0
                continue

            stable_count += 1
            if stable_count >= self.POST_PURCHASE_STABLE_FRAMES:
                return True

    def _enter_free_store(self, skip_first_screenshot=True) -> bool:
        logger.info('Store: enter free store')
        self.ui_goto(page_free_store, skip_first_screenshot=skip_first_screenshot)
        return True

    def _goto_inheritance_stone_store(self, skip_first_screenshot=True) -> bool:
        logger.info('Store: switch to inheritance stone store')
        self.ui_goto(page_inheritance_stone_store, skip_first_screenshot=skip_first_screenshot)
        return True

    def _enter_conquest_points_store(self, skip_first_screenshot=True) -> bool:
        logger.info('Store: enter conquest points store')
        self.ui_goto(page_conquest_points_store, skip_first_screenshot=skip_first_screenshot)
        return True

    def _scroll_inheritance_store_once(self):
        # Swipe left to reveal later cards in the horizontal inheritance list.
        self.device.swipe(
            self.INHERITANCE_SCROLL_START,
            self.INHERITANCE_SCROLL_END,
            duration=(0.3, 0.35),
        )

    def _wait_inheritance_scroll_settle(self, item_asset: ButtonWrapper) -> bool:
        timeout = Timer(2.5, count=6).start()
        stable_count = 0
        last_signature = None

        while 1:
            self.device.screenshot()

            if timeout.reached():
                return False

            if self.ui_additional():
                timeout.reset()
                stable_count = 0
                last_signature = None
                continue
            if self.handle_network_error():
                timeout.reset()
                stable_count = 0
                last_signature = None
                continue

            target = self._first_item_button(item_asset, self.device.image, axis='x')
            end_marker = self._first_item_button(ITEM_IN_CD, self.device.image, axis='x')

            if target is not None:
                signature = ('target', round(self._button_center_x(target)))
            elif end_marker is not None:
                signature = ('end', round(self._button_center_x(end_marker)))
            else:
                signature = ('none', 0)

            if signature == last_signature:
                stable_count += 1
            else:
                stable_count = 1
                last_signature = signature

            if stable_count >= 2:
                return True

    def _reset_inheritance_store_view(self):
        # Re-enter through the sibling tab so the horizontal list restarts from
        # a predictable position before searching for the next item.
        self._enter_free_store(skip_first_screenshot=True)
        self._goto_inheritance_stone_store(skip_first_screenshot=True)

    def _locate_inheritance_item(self, item: ItemPurchasePlan) -> bool:
        logger.info(f'Locate {item.name} in inheritance stone store')
        timeout = Timer(12, count=30).start()
        scroll_retry = Timer(self.INHERITANCE_SCROLL_INTERVAL_SECONDS, count=0).start()

        while 1:
            self.device.screenshot()

            if timeout.reached():
                logger.warning(f'Locate {item.name} timeout')
                self._save_debug_image(f'locate_{item.name}_timeout')
                return False

            if self.ui_additional():
                timeout.reset()
                continue
            if self.handle_network_error():
                timeout.reset()
                continue

            # Horizontal inheritance cards have three meaningful states:
            # 1. active-color target card -> can click and buy now
            # 2. target card still visible but gray -> quota already consumed
            # 3. ITEM_IN_CD enters the shared search window -> reached tail
            #
            # We must distinguish (1) from (2); plain template presence alone
            # is not enough for this store.
            if self._item_ready_for_purchase(item):
                return True
            if self._has_item(item.asset, self.device.image):
                logger.info(f'{item.name}: visible but gray, treat as sold out')
                return False
            if self._has_item(ITEM_IN_CD, self.device.image):
                logger.info(f'{item.name}: reached inheritance store tail without match')
                return False

            if scroll_retry.reached():
                self._scroll_inheritance_store_once()
                self._wait_inheritance_scroll_settle(item.asset)
                scroll_retry.reset()
                timeout.reset()
                continue

    def _run_store_page_items(self, name: str, enter, items: list[ItemPurchasePlan]) -> None:
        items = self._enabled_items(items)
        if not items:
            logger.info(f'Skip {name} by config')
            return

        self._wait_purchase_cooldown_before_switch()
        enter(skip_first_screenshot=True)

        for item in items:
            self.device.screenshot()
            if not self._item_ready_for_purchase(item):
                logger.info(f'{item.name}: not available in current viewport')
                continue
            result = self._purchase_item(item)
            if result.success:
                self._record_purchase_result(item, result)
                self._record_purchase_time()
                self._wait_store_ready_after_purchase()

    def _run_inheritance_store(self):
        items = self._enabled_items(self._build_inheritance_store_items())
        if not items:
            logger.info('Skip inheritance stone store by config')
            return

        for index, item in enumerate(items):
            self._wait_purchase_cooldown_before_switch()
            if index == 0:
                self._goto_inheritance_stone_store(skip_first_screenshot=True)
            else:
                self._reset_inheritance_store_view()

            if not self._locate_inheritance_item(item):
                continue

            result = self._purchase_item(item)
            if result.success:
                self._record_purchase_result(item, result)
                self._record_purchase_time()
                self._wait_store_ready_after_purchase()

    def run(self):
        logger.hr('Store', level=1)
        if not self.device.app_is_running():
            from tasks.login.login import Login

            Login(self.config, device=self.device).app_start()

        self.ui_goto(page_store)
        self._load_shared_item_search()

        self._run_store_page_items(
            name='free store',
            enter=self._enter_free_store,
            items=self._build_free_store_items(),
        )
        self._run_inheritance_store()
        self._run_store_page_items(
            name='conquest points store',
            enter=self._enter_conquest_points_store,
            items=self._build_conquest_store_items(),
        )

        if self.purchase_stats:
            logger.hr('Store purchase summary', level=2)
            for name, quantity in sorted(self.purchase_stats.items()):
                logger.attr(name, quantity)

        self.config.task_call('DataUpdate', force_call=False)
        self.config.task_delay(server_update=True)
        return True
