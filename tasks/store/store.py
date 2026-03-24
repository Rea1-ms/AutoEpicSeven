from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Literal

from module.base.button import ButtonWrapper, ClickButton
from module.base.timer import Timer
from module.base.utils import save_image
from module.logger import logger
from tasks.base.page import page_store
from tasks.base.ui import UI
from tasks.store.purchase import (
    ItemPurchasePlan,
    PurchaseCounterPreset,
    PurchaseResult,
    counter_to_text,
    is_valid_purchase_counter,
    ocr_purchase_counter,
    ocr_remaining_buy_times,
    normalize_config_purchase_quantity,
    plan_purchase_selection,
    resolve_period_purchase_quantity,
)
from tasks.store.assets.assets_store import (
    ARENA_FLAG,
    BUY,
    BUY_CONFIRM_MULTI,
    BUY_CONFIRM_SINGLE,
    BUY_MAX,
    BUY_MIN,
    BUY_TIMES_MINUS,
    BUY_TIMES_PLUS,
    COMMON_INHERITANCE_STONE,
    CONQUEST_POINTS_STORE,
    CONQUEST_POINTS_STORE_CHECK,
    DAILY_FREE_ITEM,
    FRIENDSHIP_POINTS,
    FRIENDSHIP_POINTS_STORE,
    GOLDEN_INHERITANCE_STONE,
    INHERITANCE_STONE_STORE,
    MOBILITY_40,
    MOROGORA,
    OCR_BUY_TIMES,
    OCR_REMAINING_BUY_TIMES,
    POTENTIAL_FRAGMENTS,
    STORE_ITEMS_SEARCH,
    SUB_STORE_SEARCH,
)

PurchasePopupLayout = Literal['unknown', 'single', 'multi']


@dataclass(frozen=True)
class SubStorePlan:
    name: str
    entry: ButtonWrapper
    check: Callable[[], bool]
    items: tuple[ItemPurchasePlan, ...]


class Store(UI):
    ITEM_BUY_Y_TOLERANCE = 60
    CONFIRM_SIMILARITY = 0.85
    SCROLL_START = (640, 470)
    SCROLL_END = (640, 350)
    SCROLL_SETTLE_Y_TOLERANCE = 8
    PURCHASE_SWITCH_COOLDOWN_SECONDS = 1
    SUB_STORE_SCROLL_INTERVAL_SECONDS = 1
    # Purchase popup counter area is shared across store items.
    # If only one purchase remains, the `x/y` counter may disappear.
    BUY_COUNTER_AREA = OCR_BUY_TIMES.area
    REMAINING_BUY_TIMES_AREA = OCR_REMAINING_BUY_TIMES.area

    def __init__(self, config, device, task='Store'):
        super().__init__(config, device=device, task=task)
        self.buy_asset: ButtonWrapper = BUY
        self.buy_confirm_multi_asset: ButtonWrapper = BUY_CONFIRM_MULTI
        self.buy_confirm_single_asset: ButtonWrapper = BUY_CONFIRM_SINGLE
        self.buy_max_asset: ButtonWrapper = BUY_MAX
        self.buy_min_asset: ButtonWrapper = BUY_MIN
        self.buy_times_minus_asset: ButtonWrapper = BUY_TIMES_MINUS
        self.buy_times_plus_asset: ButtonWrapper = BUY_TIMES_PLUS
        self.arena_flag_asset: ButtonWrapper = ARENA_FLAG
        self.friendship_store_check: ButtonWrapper = FRIENDSHIP_POINTS
        self.conquest_store_check: ButtonWrapper = CONQUEST_POINTS_STORE_CHECK
        self.sub_store_search_asset: ButtonWrapper = SUB_STORE_SEARCH
        self._purchase_switch_cooldown = Timer(self.PURCHASE_SWITCH_COOLDOWN_SECONDS, count=0)
        self.purchase_stats: dict[str, int] = {}

    def _load_shared_search(self):
        buttons = [
            MOROGORA,
            POTENTIAL_FRAGMENTS,
            MOBILITY_40,
            self.arena_flag_asset,
        ]
        for button in buttons:
            button.load_search(STORE_ITEMS_SEARCH.area)

        for sub_store in [INHERITANCE_STONE_STORE, FRIENDSHIP_POINTS_STORE, CONQUEST_POINTS_STORE]:
            sub_store.load_search(self.sub_store_search_asset.area)

    def _build_sub_store_plans(self) -> list[SubStorePlan]:
        return [
            SubStorePlan(
                name='inheritance stone store',
                entry=INHERITANCE_STONE_STORE,
                check=self._is_inheritance_store,
                items=(
                    ItemPurchasePlan(
                        name='inheritance_morogora',
                        asset=MOROGORA,
                        desired_quantity=normalize_config_purchase_quantity(
                            self.config.StoreWeekly_BuyInheritanceMorogora,
                            maximum=2,
                        ),
                        quantity_strategy='target',
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
                    ItemPurchasePlan(
                        name='potential_fragments',
                        asset=POTENTIAL_FRAGMENTS,
                        desired_quantity=normalize_config_purchase_quantity(
                            self.config.StoreWeekly_BuyInheritancePotentialFragments,
                            maximum=2,
                        ),
                        quantity_strategy='target',
                        scroll_before=True,
                        counter_preset=PurchaseCounterPreset(
                            name='StoreInheritancePotentialFragmentsCounter',
                            area=self.BUY_COUNTER_AREA,
                        ),
                        purchase_limit=2,
                        remaining_counter_preset=PurchaseCounterPreset(
                            name='StoreInheritancePotentialFragmentsRemainingTimes',
                            area=self.REMAINING_BUY_TIMES_AREA,
                        ),
                    ),
                ),
            ),
            SubStorePlan(
                name='friendship points store',
                entry=FRIENDSHIP_POINTS_STORE,
                check=self._is_friendship_store,
                items=(
                    ItemPurchasePlan(
                        name='friendship_mobility_40',
                        asset=MOBILITY_40,
                        desired_quantity=1 if self.config.StoreDaily_BuyFriendshipMobility40 else 0,
                        quantity_strategy='once',
                        counter_preset=PurchaseCounterPreset(
                            name='StoreFriendshipMobility40Counter',
                            area=self.BUY_COUNTER_AREA,
                        ),
                    ),
                    ItemPurchasePlan(
                        name='friendship_arena_flag',
                        asset=self.arena_flag_asset,
                        desired_quantity=1 if self.config.StoreDaily_BuyFriendshipArenaFlag else 0,
                        quantity_strategy='once',
                        counter_preset=PurchaseCounterPreset(
                            name='StoreFriendshipArenaFlagCounter',
                            area=self.BUY_COUNTER_AREA,
                        ),
                    ),
                ),
            ),
            SubStorePlan(
                name='conquest points store',
                entry=CONQUEST_POINTS_STORE,
                check=self._is_conquest_store,
                items=(
                    ItemPurchasePlan(
                        name='conquest_morogora',
                        asset=MOROGORA,
                        desired_quantity=1 if self.config.StoreWeekly_BuyConquestMorogora else 0,
                        quantity_strategy='once',
                        counter_preset=PurchaseCounterPreset(
                            name='StoreConquestMorogoraCounter',
                            area=self.BUY_COUNTER_AREA,
                        ),
                    ),
                    ItemPurchasePlan(
                        name='conquest_mobility_40',
                        asset=MOBILITY_40,
                        desired_quantity=normalize_config_purchase_quantity(
                            self.config.StoreDaily_BuyConquestMobility40,
                            maximum=3,
                        ),
                        quantity_strategy='target',
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
                ),
            ),
        ]

    @staticmethod
    def _enabled_items(sub_store: SubStorePlan) -> list[ItemPurchasePlan]:
        return [item for item in sub_store.items if item.desired_quantity > 0]

    def _save_debug_image(self, tag: str):
        now = datetime.now()
        folder = Path('log/store_debug') / now.strftime('%Y%m%d')
        folder.mkdir(parents=True, exist_ok=True)
        image_path = folder / f"{now.strftime('%Y%m%d_%H%M%S_%f')}_{tag}.png"
        save_image(self.device.image, str(image_path))
        logger.info(f'Store debug image: {image_path}')

    def _log_purchase_debug(self, item_name: str, item_asset: ButtonWrapper, reason: str):
        item_matches = item_asset.match_multi_template(self.device.image, threshold=30)
        buy_matches = self.buy_asset.match_multi_template(self.device.image, threshold=40)
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
        in_store = self.appear(page_store.check_button)
        layout = self._detect_purchase_popup_layout()
        logger.warning(
            f'[StoreDebug] {item_name} {reason}: item_matches={len(item_matches)}, '
            f'buy_matches={len(buy_matches)}, single_confirm={single_confirm}, '
            f'multi_confirm={multi_confirm}, layout={layout}, in_store={in_store}'
        )
        if item_matches:
            logger.info(f'[StoreDebug] {item_name} item_areas={ [m.area for m in item_matches[:3]] }')
        if buy_matches:
            logger.info(f'[StoreDebug] {item_name} buy_areas={ [m.area for m in buy_matches[:3]] }')
        self._save_debug_image(f'{item_name}_{reason}')

    @staticmethod
    def _button_center_y(button: ClickButton) -> float:
        return (button.area[1] + button.area[3]) / 2

    @staticmethod
    def _has_item(item_asset: ButtonWrapper, image) -> bool:
        return len(item_asset.match_multi_template(image, threshold=30)) > 0

    @staticmethod
    def _first_item_button(item_asset: ButtonWrapper, image) -> ClickButton | None:
        matches = item_asset.match_multi_template(image, threshold=30)
        if not matches:
            return None
        return sorted(matches, key=lambda x: x.area[1])[0]

    def _wait_item_settle_after_scroll(self, item: ItemPurchasePlan) -> bool:
        """
        Wait briefly for post-swipe inertia to settle before scanning item/buy pair.
        """
        timeout = Timer(2.5, count=6).start()
        stable_count = 0
        last_y = None

        while 1:
            self.device.screenshot()

            if timeout.reached():
                return False

            if self.ui_additional():
                timeout.reset()
                stable_count = 0
                last_y = None
                continue
            if self.handle_network_error():
                timeout.reset()
                stable_count = 0
                last_y = None
                continue

            target = self._first_item_button(item.asset, self.device.image)
            if target is None:
                # Sold out or not in current viewport. Do not block follow-up flow.
                return False

            current_y = self._button_center_y(target)
            if last_y is None:
                stable_count = 1
            elif abs(current_y - last_y) <= self.SCROLL_SETTLE_Y_TOLERANCE:
                stable_count += 1
            else:
                stable_count = 1
            last_y = current_y

            if stable_count >= 2:
                return True

    def _find_target_buy_buttons(self, targets: list[tuple[str, ButtonWrapper]]) -> list[tuple[str, ClickButton]]:
        buy_buttons = self.buy_asset.match_multi_template(self.device.image, threshold=40)
        if not buy_buttons:
            return []
        buy_buttons = sorted(buy_buttons, key=self._button_center_y)

        pairs: list[tuple[str, ClickButton]] = []
        used_buy_index: set[int] = set()

        for item_name, item_asset in targets:
            item_buttons = item_asset.match_multi_template(self.device.image, threshold=30)
            if not item_buttons:
                continue
            item_buttons = sorted(item_buttons, key=self._button_center_y)

            best_index = -1
            best_distance = 9999.0
            for item_button in item_buttons:
                item_y = self._button_center_y(item_button)
                for idx, buy_button in enumerate(buy_buttons):
                    if idx in used_buy_index:
                        continue
                    dist = abs(self._button_center_y(buy_button) - item_y)
                    if dist < best_distance:
                        best_distance = dist
                        best_index = idx

            if best_index >= 0 and best_distance <= self.ITEM_BUY_Y_TOLERANCE:
                used_buy_index.add(best_index)
                pairs.append((item_name, buy_buttons[best_index]))

        pairs.sort(key=lambda x: x[1].area[1])
        return pairs

    def _is_inheritance_store(self) -> bool:
        image = self.device.image
        return (
            GOLDEN_INHERITANCE_STONE.match_template_luma(image)
            and COMMON_INHERITANCE_STONE.match_template_luma(image)
        )

    def _is_friendship_store(self) -> bool:
        return self.appear(self.friendship_store_check)

    def _is_conquest_store(self) -> bool:
        return self.appear(self.conquest_store_check)

    def _scroll_sub_store_list_once(self, downward=True):
        """
        Scroll right-side sub-store list to search entries that are out of viewport.
        """
        x1, y1, x2, y2 = self.sub_store_search_asset.area
        x = (x1 + x2) // 2
        top = y1 + 80
        bottom = y2 - 80
        if downward:
            start = (x, bottom)
            end = (x, top)
        else:
            start = (x, top)
            end = (x, bottom)
        self.device.swipe(start, end, duration=(0.2, 0.3))

    def _open_sub_store(self, name: str, entry: ButtonWrapper, check: Callable[[], bool]) -> bool:
        logger.info(f'Open {name}')
        timeout = Timer(12, count=30).start()
        reclick = Timer(2, count=0).start()
        scroll_retry = Timer(self.SUB_STORE_SCROLL_INTERVAL_SECONDS, count=0).start()
        scroll_downward = True
        clicked_entry = False

        while 1:
            self.device.screenshot()

            if timeout.reached():
                logger.warning(f'Open {name} timeout')
                logger.warning(
                    f'[StoreDebug] open {name}: entry={self.appear(entry)}, check={check()}, '
                    f'in_store={self.appear(page_store.check_button)}'
                )
                self._save_debug_image(f'open_{name.replace(" ", "_")}_timeout')
                return False

            if check():
                return True

            # Clear late popups before clicking the next sub-store entry.
            if self.ui_additional():
                timeout.reset()
                continue
            if self.handle_network_error():
                timeout.reset()
                continue

            if (not clicked_entry or reclick.reached()) and self.appear_then_click(entry, interval=1):
                clicked_entry = True
                reclick.reset()
                timeout.reset()
                continue

            if not clicked_entry and scroll_retry.reached():
                logger.info(f'Open {name}: entry not visible, scroll sub-store list')
                self._scroll_sub_store_list_once(downward=scroll_downward)
                scroll_downward = not scroll_downward
                scroll_retry.reset()
                timeout.reset()
                continue

    def _click_purchase_target(self, item: ItemPurchasePlan) -> bool:
        if item.direct_click:
            item_buttons = item.asset.match_multi_template(self.device.image, threshold=30)
            if not item_buttons:
                return False
            target = sorted(item_buttons, key=lambda x: x.area[1])[0]
            logger.info(f'{item.name} -> {target}')
            self.device.click(target)
            self._clear_purchase_popup_intervals()
            return True

        pairs = self._find_target_buy_buttons([(item.name, item.asset)])
        if not pairs:
            return False

        _, buy_button = pairs[0]
        logger.info(f'{item.name} -> {buy_button}')
        self.device.click(buy_button)
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
        touched_close = False
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
            if clicked_confirm or clicked_cancel or touched_close:
                store_visible = self.appear(page_store.check_button)
                item_visible = self._has_item(item.asset, self.device.image)

                if clicked_cancel and store_visible and not popup_active:
                    logger.info(f'{item.name}: target total already reached, skip purchase')
                    return PurchaseResult(success=False, quantity=0, quantity_source='target_reached')
                if touched_close and store_visible and not popup_active:
                    return PurchaseResult(
                        success=True,
                        quantity=purchase_quantity,
                        counter=purchase_counter,
                        quantity_source=quantity_source,
                    )
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
                if self.handle_touch_to_close(interval=1):
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
                    if self.handle_popup_cancel(interval=1):
                        clicked_cancel = True
                        progress = True
                    timeout.reset()
                    if progress:
                        continue
                    continue

                timeout.reset()
                if progress or quantity_resolved:
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
            if self.handle_touch_to_close(interval=1):
                touched_close = True
                progress = True

            if progress:
                timeout.reset()
                continue

            current_layout = self._detect_purchase_popup_layout()
            popup_active = current_layout != 'unknown'
            item_visible = self._has_item(item.asset, self.device.image)

            if touched_close and self.appear(page_store.check_button) and not popup_active:
                return PurchaseResult(
                    success=True,
                    quantity=purchase_quantity,
                    counter=purchase_counter,
                    quantity_source=quantity_source,
                )
            if clicked_confirm and not popup_active and not item_visible:
                return PurchaseResult(
                    success=True,
                    quantity=purchase_quantity,
                    counter=purchase_counter,
                    quantity_source=quantity_source,
                )
            if clicked_confirm and self.appear(page_store.check_button) and not popup_active:
                return PurchaseResult(
                    success=True,
                    quantity=purchase_quantity,
                    counter=purchase_counter,
                    quantity_source=quantity_source,
                )
            if item.direct_click and not item_visible:
                return PurchaseResult(
                    success=True,
                    quantity=purchase_quantity,
                    counter=purchase_counter,
                    quantity_source=quantity_source,
                )

            # If nothing settled and no confirm is visible, retry the first step once more.
            if (
                (not clicked_confirm)
                and (not touched_close)
                and (not popup_active)
                and target_retry.reached()
            ):
                if self._click_purchase_target(item):
                    layout = 'unknown'
                    target_retry.reset()
                    timeout.reset()
                    continue

    def _scroll_store_list_once(self):
        self.device.swipe(self.SCROLL_START, self.SCROLL_END, duration=(0.2, 0.3))

    def _record_purchase_time(self) -> None:
        self._purchase_switch_cooldown.reset()

    def _wait_purchase_cooldown_before_switch(self) -> None:
        """
        Ensure enough gap from last successful purchase before entering another sub store.
        This avoids first-item detection being blocked by lingering purchase-success layers.
        """
        if not self._purchase_switch_cooldown.started():
            return

        logger.info(f'Wait purchase cooldown before sub-store switch: {self.PURCHASE_SWITCH_COOLDOWN_SECONDS}s')
        while 1:
            self.device.screenshot()

            if self.ui_additional():
                continue
            if self.handle_network_error(interval=0.2):
                continue
            if self._detect_purchase_popup_layout() != 'unknown':
                continue
            if not self.appear(page_store.check_button):
                continue
            if self._purchase_switch_cooldown.reached():
                return

    def _run_sub_store(self, sub_store: SubStorePlan):
        items = self._enabled_items(sub_store)
        if not items:
            logger.info(f'Skip {sub_store.name} by config')
            return

        self._wait_purchase_cooldown_before_switch()
        if not self._open_sub_store(sub_store.name, sub_store.entry, sub_store.check):
            return

        for item in items:
            if item.scroll_before:
                self._scroll_store_list_once()
                if self._wait_item_settle_after_scroll(item):
                    logger.info(f'{item.name} settled after scroll')
                else:
                    logger.info(f'{item.name} settle skipped (not visible or timeout)')
            result = self._purchase_item(item)
            if result.success:
                self._record_purchase_result(item, result)
                self._record_purchase_time()

    def run(self):
        logger.hr('Store', level=1)
        if not self.device.app_is_running():
            from tasks.login.login import Login

            Login(self.config, device=self.device).app_start()

        self.ui_goto(page_store)
        self._load_shared_search()

        daily_free_item = ItemPurchasePlan(
            name='daily_free_item',
            asset=DAILY_FREE_ITEM,
            desired_quantity=1 if self.config.StoreDaily_BuyDailyFreeItem else 0,
            direct_click=True,
        )
        if daily_free_item.desired_quantity > 0:
            result = self._purchase_item(daily_free_item)
            if result.success:
                self._record_purchase_result(daily_free_item, result)
                self._record_purchase_time()
        else:
            logger.info('Skip daily free item by config')

        for sub_store in self._build_sub_store_plans():
            self._run_sub_store(sub_store)

        if self.purchase_stats:
            logger.hr('Store purchase summary', level=2)
            for name, quantity in sorted(self.purchase_stats.items()):
                logger.attr(name, quantity)

        self.config.task_call('DataUpdate', force_call=False)
        self.config.task_delay(server_update=True)
        return True
