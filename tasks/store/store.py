from datetime import datetime
from pathlib import Path
from module.base.button import ButtonWrapper, ClickButton
from module.base.timer import Timer
from module.base.utils import save_image
from module.logger import logger
from typing import Callable
from tasks.base.page import page_store
from tasks.base.ui import UI
from tasks.store.assets.assets_store import (
    BUY,
    BUY_CONFIRM,
    BUY_MAX,
    ARENA_FLAG,
    SUB_STORE_SEARCH,
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
)

class Store(UI):
    ITEM_BUY_Y_TOLERANCE = 60
    CONFIRM_SIMILARITY = 0.85
    SCROLL_START = (640, 470)
    SCROLL_END = (640, 350)

    def __init__(self, config, device, task='Store'):
        super().__init__(config, device=device, task=task)
        self.buy_asset: ButtonWrapper = BUY
        self.buy_confirm_asset: ButtonWrapper = BUY_CONFIRM
        self.buy_max_asset: ButtonWrapper = BUY_MAX
        self.arena_flag_asset: ButtonWrapper = ARENA_FLAG
        self.friendship_store_check: ButtonWrapper = FRIENDSHIP_POINTS
        self.conquest_store_check: ButtonWrapper = CONQUEST_POINTS_STORE_CHECK
        self.sub_store_search_asset: ButtonWrapper = SUB_STORE_SEARCH
        self.buy_daily_free_item = getattr(self.config, 'StoreDaily_BuyDailyFreeItem', True)
        self.buy_inheritance_morogora = getattr(self.config, 'StoreWeekly_BuyInheritanceMorogora', True)
        self.buy_inheritance_potential_fragments = getattr(
            self.config, 'StoreWeekly_BuyInheritancePotentialFragments', True
        )
        self.buy_friendship_mobility40 = getattr(self.config, 'StoreDaily_BuyFriendshipMobility40', True)
        self.buy_friendship_arena_flag = getattr(self.config, 'StoreDaily_BuyFriendshipArenaFlag', True)
        self.buy_conquest_morogora = getattr(self.config, 'StoreWeekly_BuyConquestMorogora', True)
        self.buy_conquest_mobility40 = getattr(self.config, 'StoreDaily_BuyConquestMobility40', True)

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

    def _find_target_buy_buttons(self, targets: list[tuple[str, ButtonWrapper]]) -> list[tuple[str, ClickButton]]:
        buy_buttons = self.buy_asset.match_multi_template(self.device.image, threshold=40)
        if not buy_buttons:
            return []

        pairs: list[tuple[str, ClickButton]] = []
        used_buy_index: set[int] = set()
        for item_name, item_asset in targets:
            item_buttons = item_asset.match_multi_template(self.device.image, threshold=30)
            if not item_buttons:
                continue

            item_button = item_buttons[0]
            item_y = self._button_center_y(item_button)

            best_index = -1
            best_distance = 9999.0
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

    def _open_sub_store(self, name: str, entry: ButtonWrapper, check: Callable[[], bool]) -> bool:
        logger.info(f'Open {name}')
        timeout = Timer(8, count=20).start()
        reclick = Timer(2, count=0).start()
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

            if clicked_entry and check():
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

    def _purchase_item(
            self,
            item_name: str,
            item_asset: ButtonWrapper,
            use_max=False,
            direct_click=False,
    ) -> bool:
        logger.info(f'Purchase {item_name}')
        timeout = Timer(12, count=30).start()
        not_found_confirm = Timer(4, count=8).start()
        buy_confirm_seen = False
        clicked_buy = False

        while 1:
            self.device.screenshot()

            if timeout.reached():
                logger.warning(f'Purchase {item_name} timeout')
                self._log_purchase_debug(item_name, item_asset, 'timeout')
                return False

            # # Clear stale confirm popup from previous item purchase.
            # if self.appear_then_click(
            #         self.buy_confirm_asset, interval=1, similarity=self.CONFIRM_SIMILARITY):
            #     buy_confirm_seen = True
            #     timeout.reset()
            #     not_found_confirm.reset()
            #     continue
            #
            # if direct_click and not clicked_buy:
            #     item_buttons = item_asset.match_multi_template(self.device.image, threshold=30)
            #     if item_buttons:
            #         target = sorted(item_buttons, key=lambda x: x.area[1])[0]
            #         logger.info(f'{item_name} -> {target}')
            #         self.device.click(target)
            #         clicked_buy = True
            #         self.interval_clear(self.buy_confirm_asset)
            #         timeout.reset()
            #         not_found_confirm.reset()
            #         continue

            if use_max and clicked_buy:
                if self.appear_then_click(self.buy_max_asset, interval=1):
                    timeout.reset()
                    not_found_confirm.reset()
                    continue

            if buy_confirm_seen:
                # Do not finish purchase while confirm popup still exists.
                if self.appear(self.buy_confirm_asset, similarity=self.CONFIRM_SIMILARITY):
                    timeout.reset()
                    not_found_confirm.reset()
                    continue
                if self.handle_touch_to_close(interval=1):
                    timeout.reset()
                    not_found_confirm.reset()
                    continue
                if clicked_buy and self.appear(page_store.check_button):
                    return True

            if direct_click and clicked_buy:
                if not self._has_item(item_asset, self.device.image):
                    return True

            pairs = self._find_target_buy_buttons([(item_name, item_asset)])
            if pairs:
                _, buy_button = pairs[0]
                logger.info(f'{item_name} -> {buy_button}')
                self.device.click(buy_button)
                clicked_buy = True
                self.interval_clear(self.buy_confirm_asset)
                timeout.reset()
                not_found_confirm.reset()
                continue

            if not clicked_buy and not_found_confirm.reached():
                if self.appear(self.buy_confirm_asset, similarity=self.CONFIRM_SIMILARITY):
                    timeout.reset()
                    not_found_confirm.reset()
                    continue
                logger.warning(f'{item_name} not found in current store page')
                self._log_purchase_debug(item_name, item_asset, 'not_found')
                return False

            if self.ui_additional():
                timeout.reset()
                not_found_confirm.reset()
                continue
            if self.handle_network_error():
                timeout.reset()
                not_found_confirm.reset()
                continue

            if buy_confirm_seen and self.appear(page_store.check_button):
                return True

    def _scroll_store_list_once(self):
        self.device.swipe(self.SCROLL_START, self.SCROLL_END, duration=(0.2, 0.3))

    def run(self):
        logger.hr('Store', level=1)
        if not self.device.app_is_running():
            from tasks.login.login import Login
            Login(self.config, device=self.device).app_start()

        self.ui_goto(page_store)
        self._load_shared_search()

        if self.buy_daily_free_item:
            self.device.screenshot()
            if self._has_item(DAILY_FREE_ITEM, self.device.image):
                self._purchase_item('daily_free_item', DAILY_FREE_ITEM, direct_click=True)
            else:
                logger.info('Daily free item not found, skip')
        else:
            logger.info('Skip daily free item by config')

        if self.buy_inheritance_morogora or self.buy_inheritance_potential_fragments:
            if self._open_sub_store('inheritance stone store', INHERITANCE_STONE_STORE, self._is_inheritance_store):
                if self.buy_inheritance_morogora:
                    self._purchase_item('morogora', MOROGORA, use_max=True)
                else:
                    logger.info('Skip inheritance morogora by config')

                if self.buy_inheritance_potential_fragments:
                    self._scroll_store_list_once()
                    self._purchase_item('potential_fragments', POTENTIAL_FRAGMENTS, use_max=True)
                else:
                    logger.info('Skip potential fragments by config')
        else:
            logger.info('Skip inheritance store by config')

        if self.buy_friendship_mobility40 or self.buy_friendship_arena_flag:
            if self._open_sub_store('friendship points store', FRIENDSHIP_POINTS_STORE, self._is_friendship_store):
                if self.buy_friendship_mobility40:
                    self._purchase_item('mobility_40', MOBILITY_40)
                else:
                    logger.info('Skip friendship mobility_40 by config')

                if self.buy_friendship_arena_flag:
                    self._purchase_item('arena_flag', self.arena_flag_asset)
                else:
                    logger.info('Skip friendship arena_flag by config')
        else:
            logger.info('Skip friendship store by config')

        if self.buy_conquest_morogora or self.buy_conquest_mobility40:
            if self._open_sub_store('conquest points store', CONQUEST_POINTS_STORE, self._is_conquest_store):
                if self.buy_conquest_morogora:
                    self._purchase_item('morogora', MOROGORA)
                else:
                    logger.info('Skip conquest morogora by config')

                if self.buy_conquest_mobility40:
                    self._purchase_item('mobility_40', MOBILITY_40, use_max=True)
                else:
                    logger.info('Skip conquest mobility_40 by config')
        else:
            logger.info('Skip conquest store by config')

        self.config.task_delay(server_update=True)
        return True
