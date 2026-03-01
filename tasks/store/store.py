from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from module.base.button import ButtonWrapper, ClickButton
from module.base.timer import Timer
from module.base.utils import save_image
from module.logger import logger
from tasks.base.page import page_store
from tasks.base.ui import UI
from tasks.store.assets.assets_store import (
    ARENA_FLAG,
    BUY,
    BUY_CONFIRM,
    BUY_MAX,
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
    POTENTIAL_FRAGMENTS,
    STORE_ITEMS_SEARCH,
    SUB_STORE_SEARCH,
)


@dataclass(frozen=True)
class ItemPlan:
    name: str
    asset: ButtonWrapper
    enabled: bool
    use_max: bool = False
    direct_click: bool = False
    scroll_before: bool = False


@dataclass(frozen=True)
class SubStorePlan:
    name: str
    entry: ButtonWrapper
    check: Callable[[], bool]
    items: tuple[ItemPlan, ...]


class Store(UI):
    ITEM_BUY_Y_TOLERANCE = 60
    CONFIRM_SIMILARITY = 0.85
    SCROLL_START = (640, 470)
    SCROLL_END = (640, 350)
    SCROLL_SETTLE_Y_TOLERANCE = 8
    PURCHASE_SWITCH_COOLDOWN_SECONDS = 1
    SUB_STORE_SCROLL_INTERVAL_SECONDS = 1

    def __init__(self, config, device, task='Store'):
        super().__init__(config, device=device, task=task)
        self.buy_asset: ButtonWrapper = BUY
        self.buy_confirm_asset: ButtonWrapper = BUY_CONFIRM
        self.buy_max_asset: ButtonWrapper = BUY_MAX
        self.arena_flag_asset: ButtonWrapper = ARENA_FLAG
        self.friendship_store_check: ButtonWrapper = FRIENDSHIP_POINTS
        self.conquest_store_check: ButtonWrapper = CONQUEST_POINTS_STORE_CHECK
        self.sub_store_search_asset: ButtonWrapper = SUB_STORE_SEARCH
        self._purchase_switch_cooldown = Timer(self.PURCHASE_SWITCH_COOLDOWN_SECONDS, count=0)

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
                    ItemPlan(
                        name='morogora',
                        asset=MOROGORA,
                        enabled=self.config.StoreWeekly_BuyInheritanceMorogora,
                        use_max=True,
                    ),
                    ItemPlan(
                        name='potential_fragments',
                        asset=POTENTIAL_FRAGMENTS,
                        enabled=self.config.StoreWeekly_BuyInheritancePotentialFragments,
                        use_max=True,
                        scroll_before=True,
                    ),
                ),
            ),
            SubStorePlan(
                name='friendship points store',
                entry=FRIENDSHIP_POINTS_STORE,
                check=self._is_friendship_store,
                items=(
                    ItemPlan(
                        name='mobility_40',
                        asset=MOBILITY_40,
                        enabled=self.config.StoreDaily_BuyFriendshipMobility40,
                    ),
                    ItemPlan(
                        name='arena_flag',
                        asset=self.arena_flag_asset,
                        enabled=self.config.StoreDaily_BuyFriendshipArenaFlag,
                    ),
                ),
            ),
            SubStorePlan(
                name='conquest points store',
                entry=CONQUEST_POINTS_STORE,
                check=self._is_conquest_store,
                items=(
                    ItemPlan(
                        name='morogora',
                        asset=MOROGORA,
                        enabled=self.config.StoreWeekly_BuyConquestMorogora,
                    ),
                    ItemPlan(
                        name='mobility_40',
                        asset=MOBILITY_40,
                        enabled=self.config.StoreDaily_BuyConquestMobility40,
                        use_max=True,
                    ),
                ),
            ),
        ]

    @staticmethod
    def _enabled_items(sub_store: SubStorePlan) -> list[ItemPlan]:
        return [item for item in sub_store.items if item.enabled]

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
        confirm_appear = self.appear(self.buy_confirm_asset)
        in_store = self.appear(page_store.check_button)
        logger.warning(
            f'[StoreDebug] {item_name} {reason}: item_matches={len(item_matches)}, '
            f'buy_matches={len(buy_matches)}, confirm={confirm_appear}, in_store={in_store}'
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

    def _wait_item_settle_after_scroll(self, item: ItemPlan) -> bool:
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
            GOLDEN_INHERITANCE_STONE.match_template_luma(image, similarity=0.8)
            and COMMON_INHERITANCE_STONE.match_template_luma(image, similarity=0.8)
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

            if (not clicked_entry or reclick.reached()) and self.appear_then_click(entry, interval=1):
                clicked_entry = True
                reclick.reset()
                timeout.reset()
                continue

            if self.ui_additional():
                timeout.reset()
                continue
            if self.handle_network_error():
                timeout.reset()
                continue

            if not clicked_entry and scroll_retry.reached():
                logger.info(f'Open {name}: entry not visible, scroll sub-store list')
                self._scroll_sub_store_list_once(downward=scroll_downward)
                scroll_downward = not scroll_downward
                scroll_retry.reset()
                timeout.reset()
                continue

    def _click_purchase_target(self, item: ItemPlan) -> bool:
        if item.direct_click:
            item_buttons = item.asset.match_multi_template(self.device.image, threshold=30)
            if not item_buttons:
                return False
            target = sorted(item_buttons, key=lambda x: x.area[1])[0]
            logger.info(f'{item.name} -> {target}')
            self.device.click(target)
            self.interval_clear(self.buy_confirm_asset)
            self.interval_clear(self.buy_max_asset)
            return True

        pairs = self._find_target_buy_buttons([(item.name, item.asset)])
        if not pairs:
            return False

        _, buy_button = pairs[0]
        logger.info(f'{item.name} -> {buy_button}')
        self.device.click(buy_button)
        self.interval_clear(self.buy_confirm_asset)
        self.interval_clear(self.buy_max_asset)
        return True

    def _purchase_item(self, item: ItemPlan) -> bool:
        logger.info(f'Purchase {item.name}')
        timeout = Timer(12, count=30).start()
        clicked_target = False
        clicked_confirm = False
        touched_close = False
        max_resolved = not item.use_max
        max_fallback = Timer(1.2, count=3).start()

        while 1:
            self.device.screenshot()

            if timeout.reached():
                logger.warning(f'Purchase {item.name} timeout')
                self._log_purchase_debug(item.name, item.asset, 'timeout')
                return False

            if self.ui_additional():
                timeout.reset()
                continue
            if self.handle_network_error():
                timeout.reset()
                continue

            if not clicked_target:
                stale_cleared = False
                if self.appear_then_click(
                    self.buy_confirm_asset, interval=1, similarity=self.CONFIRM_SIMILARITY
                ):
                    stale_cleared = True
                if self.handle_touch_to_close(interval=1):
                    stale_cleared = True
                if stale_cleared:
                    timeout.reset()
                    continue

                if self._click_purchase_target(item):
                    clicked_target = True
                    max_resolved = not item.use_max
                    max_fallback.reset()
                    timeout.reset()
                    continue

                logger.warning(f'{item.name} not found in current store page')
                self._log_purchase_debug(item.name, item.asset, 'not_found')
                return False

            progress = False
            confirm_visible = self.appear(self.buy_confirm_asset, similarity=self.CONFIRM_SIMILARITY)

            if item.use_max and not max_resolved:
                # Gate confirm by a short MAX phase to avoid buying quantity=1 by mistake.
                if self.appear_then_click(self.buy_max_asset, interval=1, similarity=0.8):
                    max_resolved = True
                    progress = True
                elif confirm_visible:
                    if max_fallback.reached():
                        max_resolved = True
                        logger.info(f'{item.name}: BUY_MAX unavailable, fallback to BUY_CONFIRM')
                    else:
                        timeout.reset()
                        continue

            if max_resolved and self.appear_then_click(
                    self.buy_confirm_asset, interval=1, similarity=self.CONFIRM_SIMILARITY):
                clicked_confirm = True
                progress = True
            if self.handle_touch_to_close(interval=1):
                touched_close = True
                progress = True

            if progress:
                timeout.reset()
                continue

            confirm_visible = self.appear(self.buy_confirm_asset, similarity=self.CONFIRM_SIMILARITY)
            item_visible = self._has_item(item.asset, self.device.image)

            if touched_close and self.appear(page_store.check_button):
                return True
            if clicked_confirm and not confirm_visible and not item_visible:
                return True
            if item.direct_click and not item_visible:
                return True

            # If nothing settled and no confirm is visible, retry the first step once more.
            if (not clicked_confirm) and (not touched_close) and (not confirm_visible):
                if self._click_purchase_target(item):
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
        if self._purchase_switch_cooldown.reached():
            return

        logger.info(f'Wait purchase cooldown before sub-store switch: {self.PURCHASE_SWITCH_COOLDOWN_SECONDS}s')
        while 1:
            if self._purchase_switch_cooldown.reached():
                return

            self.device.screenshot()

            if self.handle_network_error(interval=0.2):
                continue

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
            purchased = self._purchase_item(item)
            if purchased:
                self._record_purchase_time()

    def run(self):
        logger.hr('Store', level=1)
        if not self.device.app_is_running():
            from tasks.login.login import Login

            Login(self.config, device=self.device).app_start()

        self.ui_goto(page_store)
        self._load_shared_search()

        daily_free_item = ItemPlan(
            name='daily_free_item',
            asset=DAILY_FREE_ITEM,
            enabled=self.config.StoreDaily_BuyDailyFreeItem,
            direct_click=True,
        )
        if daily_free_item.enabled:
            self._purchase_item(daily_free_item)
        else:
            logger.info('Skip daily free item by config')

        for sub_store in self._build_sub_store_plans():
            self._run_sub_store(sub_store)

        self.config.task_delay(server_update=True)
        return True
