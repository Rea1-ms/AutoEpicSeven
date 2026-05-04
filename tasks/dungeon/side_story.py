from module.base.timer import Timer
from module.logger import logger
from tasks.base.assets.assets_base_page import MAIN_GOTO_SIDE_STORY
from tasks.dungeon.assets.assets_dungeon_action import CHOOSE_TEAM
from tasks.dungeon.assets.assets_dungeon_configs_side_story_entry import (
    EPISODE_PREVIEW_CHECK,
    EPISODE_SEARCH,
    MAP_CHECK,
    MAP_ENTRY,
    READY_TO_FIGHT,
    SIDE_STORY_CHECK,
    SIDE_STORY_GOTO_SPECIAL_BOOK_OF_TIME,
    SPECIAL_BOOK_OF_TIME_CHECK,
    SPECIAL_BOOK_OF_TIME_GOTO_EPISODE_PREVIEW,
    SUPPORTER_CHECK,
)
from tasks.dungeon.assets.assets_dungeon_configs_side_story_special_book_of_time import (
    SAINT_MEMORIAL_CARD,
    SAINT_MEMORIAL_SELECTED,
)
from tasks.dungeon.assets.assets_dungeon_repeat_window import (
    EQUIP_LVL15_GREEN,
    EQUIP_LVL15_WHITE,
    EQUIP_SEARCH,
    EQUIP_SELECTED,
    FAST_CHOOSE,
    PACKAGE_CHECK,
    PACKAGE_ENTRY,
    SELL,
    SORT,
    WINDOW_CHECK,
)


class SideStoryResultMixin:
    SIDE_STORY_CLEANUP_TIMEOUT_SECONDS = 25

    def _combat_is_saint37(self) -> bool:
        session = self._combat_runtime_session()
        if session.get("domain"):
            return session.get("domain") == "Saint37"
        return self._dungeon_domain() == "Saint37"

    def _is_package_page(self) -> bool:
        return self.match_template_luma(PACKAGE_CHECK, similarity=self.COMBAT_CHECK_SIMILARITY)

    def _has_sellable_equips(self) -> bool:
        return self.appear(EQUIP_LVL15_GREEN) or self.appear(EQUIP_LVL15_WHITE)

    def _cleanup_saint37_reward_items(self, skip_first_screenshot=True) -> bool:
        """
        Sell low-level equipment from the repeat combat result bag.

        Flow:
            result window → package → (sort → fast choose → sell → confirm)
            → ad_buff_x_close back to main

        Pages:
            in: repeat result window (WINDOW_CHECK)
            out: main
        """
        logger.info("SideStory: cleanup reward items")
        timeout = Timer(self.SIDE_STORY_CLEANUP_TIMEOUT_SECONDS, count=100).start()
        stage = "window"

        EQUIP_LVL15_GREEN.load_search(EQUIP_SEARCH.area)
        EQUIP_LVL15_WHITE.load_search(EQUIP_SEARCH.area)

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning(f"SideStory: cleanup timeout at stage={stage}")
                return False

            if stage == "window":
                if self._is_repeat_result_window():
                    if self.appear_then_click(PACKAGE_ENTRY, interval=2):
                        logger.info("SideStory: open temp package")
                        stage = "package"
                        timeout.reset()
                        continue

            if stage == "package":
                if self._is_package_page():
                    if self._has_sellable_equips():
                        logger.info("SideStory: sellable equips found, start sell flow")
                        stage = "sort"
                    else:
                        logger.info("SideStory: no sellable equips, close")
                        stage = "close"
                    timeout.reset()
                    continue

            if stage == "sort":
                if self.appear(FAST_CHOOSE, interval=0):
                    stage = "fast_choose"
                    timeout.reset()
                    continue
                if self.appear_then_click(SORT, interval=1):
                    timeout.reset()
                    continue

            if stage == "fast_choose":
                if self.appear_then_click(FAST_CHOOSE, interval=1):
                    logger.info("SideStory: fast choose all")
                    stage = "check_selected"
                    timeout.reset()
                    continue

            if stage == "check_selected":
                if self.appear(EQUIP_SELECTED, interval=0):
                    stage = "sell"
                    timeout.reset()
                    continue

            if stage == "sell":
                if self.appear_then_click(SELL, interval=1):
                    logger.info("SideStory: sell selected items")
                    stage = "confirm"
                    timeout.reset()
                    continue

            if stage == "confirm":
                if self.handle_popup_confirm(interval=1):
                    logger.info("SideStory: sell confirmed")
                    stage = "close"
                    timeout.reset()
                    continue

            if stage == "close":
                if self.is_in_main(interval=0):
                    logger.info("SideStory: cleanup finished")
                    return True
                if self.handle_ad_buff_x_close(interval=0.5):
                    timeout.reset()
                    continue

            if self._handle_dungeon_additional():
                timeout.reset()
                continue


class SideStoryNavigateMixin(SideStoryResultMixin):
    SIDE_STORY_NAVIGATE_TIMEOUT_SECONDS = 45
    SIDE_STORY_CARD_SCROLL_INTERVAL_SECONDS = 1.2
    SIDE_STORY_CARD_SCROLL_SETTLE_SECONDS = 0.8
    SIDE_STORY_MAX_CARD_SCROLLS = 8
    SIDE_STORY_CARD_SCROLL_X = 1100
    SIDE_STORY_CARD_SCROLL_START_Y = 450
    SIDE_STORY_CARD_SCROLL_END_Y = 300

    def _is_side_story_page(self) -> bool:
        return self.match_template_luma(SIDE_STORY_CHECK, similarity=self.COMBAT_CHECK_SIMILARITY)

    def _is_time_book_page(self) -> bool:
        return self.match_template_luma(SPECIAL_BOOK_OF_TIME_CHECK, similarity=self.COMBAT_CHECK_SIMILARITY)

    def _is_episode_preview_page(self) -> bool:
        return self.match_template_luma(EPISODE_PREVIEW_CHECK, similarity=self.COMBAT_CHECK_SIMILARITY)

    def _is_side_story_map_page(self) -> bool:
        return self.match_template_luma(MAP_CHECK, similarity=self.COMBAT_CHECK_SIMILARITY)

    def _is_supporter_page(self) -> bool:
        return self.match_template_luma(SUPPORTER_CHECK, similarity=self.COMBAT_CHECK_SIMILARITY)

    def _scroll_time_book_card_list(self) -> None:
        self.device.swipe(
            (self.SIDE_STORY_CARD_SCROLL_X, self.SIDE_STORY_CARD_SCROLL_START_Y),
            (self.SIDE_STORY_CARD_SCROLL_X, self.SIDE_STORY_CARD_SCROLL_END_Y),
            duration=(0.2, 0.3),
        )

    def _navigate_side_story(self, skip_first_screenshot=True) -> bool:
        """
        Navigate from main/side story pages to the prepare page for Saint 3-7.

        Flow:
            main → side_story → time_book → (select card) →
            episode_preview → map → (READY_TO_FIGHT) →
            supporter → (CHOOSE_TEAM) → prepare

        Pages:
            in: main or any side story page
            out: prepare page
        """
        logger.hr("Side Story Navigate", level=2)
        logger.info("SideStory: navigate to Saint 3-7 prepare page")
        timeout = Timer(self.SIDE_STORY_NAVIGATE_TIMEOUT_SECONDS, count=160).start()
        scroll_timer = Timer(self.SIDE_STORY_CARD_SCROLL_INTERVAL_SECONDS, count=0).start()
        scroll_settle = Timer(self.SIDE_STORY_CARD_SCROLL_SETTLE_SECONDS, count=0).clear()
        card_scroll_count = 0

        SAINT_MEMORIAL_CARD.load_search(EPISODE_SEARCH.area)

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning("SideStory: navigate timeout")
                return False

            if self._is_prepare_page():
                logger.info("SideStory: reached prepare page")
                return True

            if self._is_supporter_page():
                if self.appear_then_click(CHOOSE_TEAM, interval=1):
                    logger.info("SideStory: choose team")
                    timeout.reset()
                    continue

            if self._is_side_story_map_page():
                if self.appear_then_click(READY_TO_FIGHT, interval=2):
                    logger.info("SideStory: ready to fight")
                    timeout.reset()
                    continue

            if self._is_episode_preview_page():
                if self.appear_then_click(MAP_ENTRY, interval=2):
                    logger.info("SideStory: enter map")
                    timeout.reset()
                    continue

            if self._is_time_book_page():
                if scroll_settle.started() and not scroll_settle.reached():
                    continue

                if self.appear(SAINT_MEMORIAL_SELECTED, interval=0):
                    if self.appear_then_click(SPECIAL_BOOK_OF_TIME_GOTO_EPISODE_PREVIEW, interval=2):
                        logger.info("SideStory: card already selected, enter episode preview")
                        timeout.reset()
                    continue

                if self.appear_then_click(SAINT_MEMORIAL_CARD, interval=1):
                    logger.info("SideStory: select Saint Memorial card")
                    timeout.reset()
                    scroll_timer.reset()
                    continue

                if scroll_timer.reached():
                    if card_scroll_count >= self.SIDE_STORY_MAX_CARD_SCROLLS:
                        logger.warning("SideStory: Saint Memorial card not found after scrolling")
                        return False
                    logger.info(
                        f"SideStory: scroll card list "
                        f"({card_scroll_count + 1}/{self.SIDE_STORY_MAX_CARD_SCROLLS})"
                    )
                    self._scroll_time_book_card_list()
                    card_scroll_count += 1
                    scroll_timer.reset()
                    scroll_settle.reset()
                    timeout.reset()
                    continue

            if self._is_side_story_page():
                if self.appear_then_click(SIDE_STORY_GOTO_SPECIAL_BOOK_OF_TIME, interval=2):
                    logger.info("SideStory: enter special book of time")
                    timeout.reset()
                    continue

            if self.is_in_main(interval=0):
                if self.appear_then_click(MAIN_GOTO_SIDE_STORY, interval=2):
                    logger.info("SideStory: enter side story from main")
                    timeout.reset()
                    continue

            if self._handle_dungeon_additional():
                timeout.reset()
                scroll_settle.clear()
                continue
