from module.base.timer import Timer
from module.logger import logger
from tasks.base.assets.assets_base_page import BACK
from tasks.combat.assets.assets_combat_saint37 import (
    SAINT37_ADVENTURE,
    SAINT37_DETAIL_CHECK,
    SAINT37_EPISODE,
    SAINT37_MAP_CHECK,
    SAINT37_PREPARE,
    SAINT37_SELECT_TEAM,
    SAINT37_SIDE_STORY_CHECK,
    SAINT37_SIDE_STORY_ENTRY,
    SAINT37_STAGE,
    SAINT37_SUPPORTER_CHECK,
    SAINT37_TIME_BOOK_CHECK,
    SAINT37_TIME_BOOK_ENTRY,
    SAINT37_TIME_BOOK_MEMORIAL_CARD,
    SAINT37_TIME_BOOK_MEMORIAL_SELECTED,
)
from tasks.combat.assets.assets_combat_saint37_cleanup import (
    SAINT37_CLEANUP_AFTER_SELL_WINDOW,
    SAINT37_CLEANUP_QUICK_SELECT,
    SAINT37_CLEANUP_RESULT_BAG,
    SAINT37_CLEANUP_REWARD_MANAGE,
    SAINT37_CLEANUP_REWARD_WINDOW,
    SAINT37_CLEANUP_SELL_CONFIRM,
    SAINT37_CLEANUP_SELL_SELECTED_CHECK,
    SAINT37_CLEANUP_SELL_TAB,
    SAINT37_CLEANUP_TOUCH_TO_CLOSE,
)


class CombatSaint37Mixin:
    SAINT37_ENTRY_TIMEOUT_SECONDS = 45
    SAINT37_SCROLL_INTERVAL_SECONDS = 1.2
    SAINT37_MAX_CARD_SCROLLS = 8
    SAINT37_CARD_SCROLL_X = 1100
    SAINT37_CARD_SCROLL_START_Y = 655
    SAINT37_CARD_SCROLL_END_Y = 250
    SAINT37_CLEANUP_TIMEOUT_SECONDS = 25

    def _combat_is_saint37(self) -> bool:
        return self._combat_plan().name == "Saint37"

    def _is_saint37_side_story_page(self) -> bool:
        return self.match_template_luma(SAINT37_SIDE_STORY_CHECK, similarity=self.COMBAT_CHECK_SIMILARITY)

    def _is_saint37_time_book_page(self) -> bool:
        return self.match_template_luma(SAINT37_TIME_BOOK_CHECK, similarity=self.COMBAT_CHECK_SIMILARITY)

    def _is_saint37_detail_page(self) -> bool:
        return self.match_template_luma(SAINT37_DETAIL_CHECK, similarity=self.COMBAT_CHECK_SIMILARITY)

    def _is_saint37_map_page(self) -> bool:
        return self.match_template_luma(SAINT37_MAP_CHECK, similarity=self.COMBAT_CHECK_SIMILARITY)

    def _is_saint37_supporter_page(self) -> bool:
        return self.match_template_luma(SAINT37_SUPPORTER_CHECK, similarity=self.COMBAT_CHECK_SIMILARITY)

    def _scroll_saint37_time_book_list(self) -> None:
        self.device.swipe(
            (self.SAINT37_CARD_SCROLL_X, self.SAINT37_CARD_SCROLL_START_Y),
            (self.SAINT37_CARD_SCROLL_X, self.SAINT37_CARD_SCROLL_END_Y),
            duration=(0.2, 0.3),
        )

    def _enter_saint37_prepare_page(self, skip_first_screenshot=True) -> bool:
        logger.info("Combat Saint37: enter 3-7 from lobby")
        timeout = Timer(self.SAINT37_ENTRY_TIMEOUT_SECONDS, count=160).start()
        scroll_timer = Timer(self.SAINT37_SCROLL_INTERVAL_SECONDS, count=0).start()
        card_scroll_count = 0

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning("Combat Saint37: enter 3-7 prepare page timeout")
                return False

            if self._is_prepare_page():
                logger.info("Combat Saint37: reached prepare page")
                return True

            if self._is_saint37_supporter_page():
                logger.info("Combat Saint37: supporter page detected, select team")
                if self.appear_then_click(SAINT37_SELECT_TEAM, interval=1):
                    timeout.reset()
                    continue
                logger.warning("Combat Saint37: supporter page detected but select-team button missing")

            if self._is_saint37_map_page():
                if self.appear_then_click(SAINT37_PREPARE, interval=1):
                    logger.info("Combat Saint37: click prepare battle")
                    timeout.reset()
                    continue
                if self.appear_then_click(SAINT37_STAGE, interval=1):
                    logger.info("Combat Saint37: click stage 3-7")
                    timeout.reset()
                    continue
                logger.warning("Combat Saint37: map page detected but 3-7 or prepare button missing")

            if self._is_saint37_detail_page():
                if self.appear_then_click(SAINT37_ADVENTURE, interval=1):
                    logger.info("Combat Saint37: click adventure")
                    timeout.reset()
                    continue
                logger.warning("Combat Saint37: detail page detected but adventure button missing")

            if self._is_saint37_time_book_page():
                if self.appear(SAINT37_TIME_BOOK_MEMORIAL_SELECTED, interval=0):
                    if self.appear_then_click(SAINT37_EPISODE, interval=1):
                        logger.info("Combat Saint37: click episode")
                        timeout.reset()
                        continue
                    logger.warning("Combat Saint37: memorial selected but episode button missing")
                if self.appear_then_click(SAINT37_TIME_BOOK_MEMORIAL_CARD, interval=1):
                    logger.info("Combat Saint37: click Saint Memorial card")
                    timeout.reset()
                    continue
                if scroll_timer.reached():
                    if card_scroll_count >= self.SAINT37_MAX_CARD_SCROLLS:
                        logger.warning("Combat Saint37: Saint Memorial card not found after scrolling")
                    else:
                        logger.info(
                            f"Combat Saint37: scroll time-book list "
                            f"({card_scroll_count + 1}/{self.SAINT37_MAX_CARD_SCROLLS})"
                        )
                        self._scroll_saint37_time_book_list()
                        card_scroll_count += 1
                        scroll_timer.reset()
                        timeout.reset()
                        continue

            if self._is_saint37_side_story_page():
                if self.appear_then_click(SAINT37_TIME_BOOK_ENTRY, interval=1):
                    logger.info("Combat Saint37: click special time book")
                    timeout.reset()
                    continue
                logger.warning("Combat Saint37: side-story page detected but time-book entry missing")

            if self.is_in_main(interval=0):
                if self.appear_then_click(SAINT37_SIDE_STORY_ENTRY, interval=1):
                    logger.info("Combat Saint37: click side story entry")
                    timeout.reset()
                    continue
                logger.warning("Combat Saint37: main page detected but side-story entry missing")

            if self._handle_combat_additional():
                timeout.reset()
                continue

            if self.appear_then_click(BACK, interval=5):
                logger.info("Combat Saint37: fallback back navigation")
                timeout.reset()
                continue

    def _cleanup_saint37_reward_items(self, skip_first_screenshot=True) -> bool:
        logger.info("Combat Saint37: cleanup reward equipment")
        timeout = Timer(self.SAINT37_CLEANUP_TIMEOUT_SECONDS, count=100).start()
        stage = "open_rewards"

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning(f"Combat Saint37: cleanup timeout at stage={stage}")
                return False

            if stage == "open_rewards":
                if self.appear_then_click(SAINT37_CLEANUP_RESULT_BAG, interval=1):
                    logger.info("Combat Saint37: open reward item window")
                    stage = "manage"
                    timeout.reset()
                    continue

                if self.appear(SAINT37_CLEANUP_REWARD_WINDOW, interval=0):
                    stage = "manage"
                    timeout.reset()
                    continue

            if stage == "manage":
                if self.appear_then_click(SAINT37_CLEANUP_REWARD_MANAGE, interval=1):
                    logger.info("Combat Saint37: click reward manage")
                    stage = "quick_select"
                    timeout.reset()
                    continue

                if self.appear(SAINT37_CLEANUP_QUICK_SELECT, interval=0):
                    stage = "quick_select"
                    timeout.reset()
                    continue

            if stage == "quick_select":
                if self.appear_then_click(SAINT37_CLEANUP_QUICK_SELECT, interval=1):
                    logger.info("Combat Saint37: click quick select")
                    stage = "sell"
                    timeout.reset()
                    continue

            if stage == "sell":
                if self.appear(SAINT37_CLEANUP_SELL_SELECTED_CHECK, interval=0):
                    if self.appear_then_click(SAINT37_CLEANUP_SELL_TAB, interval=1):
                        logger.info("Combat Saint37: click sell selected items")
                        stage = "confirm"
                        timeout.reset()
                        continue

                if self.appear_then_click(SAINT37_CLEANUP_SELL_TAB, interval=1):
                    logger.info("Combat Saint37: click sell tab")
                    timeout.reset()
                    continue

            if stage == "confirm":
                if self.appear_then_click(SAINT37_CLEANUP_SELL_CONFIRM, interval=1):
                    logger.info("Combat Saint37: confirm sell")
                    stage = "close"
                    timeout.reset()
                    continue

            if stage == "close":
                if self.appear(SAINT37_CLEANUP_AFTER_SELL_WINDOW, interval=0):
                    if self.appear_then_click(SAINT37_CLEANUP_TOUCH_TO_CLOSE, interval=1):
                        logger.info("Combat Saint37: close reward item window after cleanup")
                        return True

                if self.appear_then_click(SAINT37_CLEANUP_TOUCH_TO_CLOSE, interval=1):
                    logger.info("Combat Saint37: close reward item window")
                    return True

            if self._handle_combat_additional():
                timeout.reset()
                continue
