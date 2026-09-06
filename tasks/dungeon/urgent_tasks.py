from datetime import datetime

import module.config.server as server
from module.base.timer import Timer
from module.config.utils import get_server_last_update
from module.logger import logger
from module.ocr.ocr import DigitCounter
from tasks.base.assets.assets_base_page import BACK, MAIN_GOTO_COMBAT
from tasks.base.resource_bar import (
    RESOURCE_KIND_INT,
    RESOURCE_BAR_ICONS,
    RESOURCE_BAR_SPECS,
    ResourceBarSpec,
)
from tasks.dungeon.assets.assets_dungeon_action import URGENT_TASKS_REWARD_EXCHANGE
from tasks.dungeon.assets.assets_dungeon_configs_combat_entry import (
    URGENT_CHECK,
    URGENT_ENTRY,
    URGENT_LOCKED,
    URGENT_TASKS,
)
from tasks.dungeon.assets.assets_dungeon_configs_urgent_tasks import (
    OCR_URGENT_COMBAT_TIMES_REMAINING,
    OCR_URGENT_TASKS_REMAINING,
    READY_TO_FIGHT,
    URGENT_TASKS_CURRENCY_ICON,
    URGENT_TASKS_NORMAL,
    URGENT_TASKS_NORMAL_SELECTED,
    URGENT_TASKS_SUPERIOR,
    URGENT_TASKS_SUPERIOR_SELECTED,
)
from tasks.dungeon.assets.assets_dungeon_fast_combat import FAST_COMBAT_RESULT_CLOSE
from tasks.dungeon.assets.assets_dungeon_state import COMBAT_PREPARE_PET_EMPTY


URGENT_TASKS_DETAIL_RESOURCE_LAYOUT = ("currency", "stamina", "skystone")
URGENT_TASKS_PREPARE_RESOURCE_LAYOUT = (
    "currency",
    "stamina",
    "gold",
    "skystone",
)
URGENT_TASKS_RESOURCE_ICONS = {
    **RESOURCE_BAR_ICONS,
    "currency": URGENT_TASKS_CURRENCY_ICON,
}
URGENT_TASKS_RESOURCE_SPECS = {
    **RESOURCE_BAR_SPECS,
    "currency": ResourceBarSpec(key="currency", kind=RESOURCE_KIND_INT),
}
URGENT_TASKS_LAST_CHECK_AT_PATH = "Combat.UrgentTasks.LastCheckAt"


class UrgentTasksNavigateMixin:
    URGENT_TASKS_NAVIGATE_TIMEOUT_SECONDS = 35
    URGENT_TASKS_DAILY_TOTAL = 5
    URGENT_TASKS_PREPARE_PENDING_SECONDS = 3
    URGENT_TASKS_PET_STABLE_SECONDS = 1
    URGENT_TASKS_RESOURCE_TIMEOUT_SECONDS = 3
    URGENT_TASKS_EXCHANGE_COST = 100
    URGENT_TASKS_EXCHANGE_BATCH_INTERVAL = (0.2, 0.3)
    URGENT_TASKS_EXCHANGE_POST_CLICK_SECONDS = 0.8
    URGENT_TASKS_EXCHANGE_RETRY_SECONDS = 3
    URGENT_TASKS_EXCHANGE_MAX_RETRIES = 3

    def _urgent_tasks_enabled(self) -> bool:
        if self._combat_is_farm_task():
            return False
        return bool(getattr(self.config, "UrgentTasks_Enable", True))

    def _urgent_tasks_last_check_at(self) -> datetime | None:
        value = self.config.cross_get(
            URGENT_TASKS_LAST_CHECK_AT_PATH,
            default=None,
        )
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                return None
        return None

    def _urgent_tasks_checked_today(self) -> bool:
        """Return whether Urgent Tasks settled in this server-day window."""
        checked_at = self._urgent_tasks_last_check_at()
        if checked_at is None:
            return False
        server_update = self.config.cross_get(
            "Combat.Scheduler.ServerUpdate",
            default=self.config.Scheduler_ServerUpdate,
        )
        return checked_at >= get_server_last_update(server_update)

    def _mark_urgent_tasks_checked(self) -> None:
        checked_at = datetime.now().replace(microsecond=0)
        self.config.cross_set(URGENT_TASKS_LAST_CHECK_AT_PATH, checked_at)
        logger.info(f"UrgentTasks: checked for this server day at {checked_at}")

    def _urgent_tasks_difficulty(self) -> str:
        difficulty = getattr(
            self.config,
            "UrgentTasks_Difficulty",
            getattr(self.config, "Combat_UrgentTasksDifficulty", "Superior"),
        )
        if difficulty not in ("Normal", "Superior"):
            logger.warning(
                f"UrgentTasks: invalid difficulty={difficulty}, fallback to Superior"
            )
            return "Superior"
        return difficulty

    def _is_urgent_tasks_entry_available(self) -> bool:
        """Confirm that the Activity tab is enabled, not merely present."""
        if server.lang != "global_cn":
            return False
        return self.match_template_color(
            URGENT_ENTRY,
            similarity=self.COMBAT_CHECK_SIMILARITY,
            threshold=self.COMBAT_STATE_COLOR_THRESHOLD,
        )

    def _is_urgent_tasks_entry_locked(self) -> bool:
        """Recognize the lock icon shown beside a disabled Activity tab."""
        if server.lang != "global_cn":
            return False
        return self.match_template_luma(
            URGENT_LOCKED,
            similarity=self.COMBAT_CHECK_SIMILARITY,
        )

    def _is_urgent_tasks_detail_page(self) -> bool:
        if server.lang != "global_cn":
            return False
        return self.match_template_luma(
            URGENT_CHECK,
            similarity=self.COMBAT_CHECK_SIMILARITY,
        )

    def _is_urgent_tasks_prepare_page(self) -> bool:
        if server.lang != "global_cn":
            return False
        return self._is_prepare_page()

    def _is_urgent_tasks_target_prepare_page(self) -> bool:
        if server.lang != "global_cn":
            return False
        return self._is_prepare_page()

    def _is_in_urgent_tasks_flow_context(self) -> bool:
        return (
            self._is_combat_urgent_board()
            or self._is_urgent_tasks_detail_page()
        )

    def _urgent_tasks_remaining(self) -> int | None:
        current, _, total = DigitCounter(
            OCR_URGENT_TASKS_REMAINING,
            lang=self._ocr_lang(),
            name="UrgentTasksRemaining",
        ).ocr_single_line(self.device.image)
        if total != self.URGENT_TASKS_DAILY_TOTAL or not 0 <= current <= total:
            logger.warning(
                "UrgentTasks: invalid remaining counter "
                f"current={current}, total={total}"
            )
            return None
        logger.attr("UrgentTasksRemaining", current)
        return current

    def _dismiss_urgent_tasks_times_hint(self) -> bool:
        """Close the daily-times hint only after positively recognizing it."""
        current, _, total = DigitCounter(
            OCR_URGENT_COMBAT_TIMES_REMAINING,
            lang=self._ocr_lang(),
            name="UrgentCombatTimesHint",
        ).ocr_single_line(self.device.image)
        if total != self.URGENT_TASKS_DAILY_TOTAL or not 0 <= current <= total:
            return False
        if not self.interval_is_reached(
            OCR_URGENT_COMBAT_TIMES_REMAINING,
            interval=1,
        ):
            return False
        logger.info(f"UrgentTasks: close daily-times hint ({current}/{total})")
        self.device.click(OCR_URGENT_COMBAT_TIMES_REMAINING)
        self.interval_reset(OCR_URGENT_COMBAT_TIMES_REMAINING, interval=1)
        return True

    def _inspect_urgent_tasks_resource(self, key: str):
        """Read one value through the shared resource-bar implementation."""
        layout = (
            URGENT_TASKS_DETAIL_RESOURCE_LAYOUT
            if self._is_urgent_tasks_detail_page()
            else URGENT_TASKS_PREPARE_RESOURCE_LAYOUT
        )
        inspected = self.inspect_resource_bar_status(
            layout=layout,
            layout_name="UrgentTasks",
            icons=URGENT_TASKS_RESOURCE_ICONS,
            specs=URGENT_TASKS_RESOURCE_SPECS,
            icon_similarity=0.8,
            segment_left_paddings={"currency": 0},
        )
        if inspected.final is None:
            return None
        return inspected.final.get(key)

    def _read_urgent_tasks_stamina(self, skip_first_screenshot=True) -> int | None:
        timeout = Timer(
            self.URGENT_TASKS_RESOURCE_TIMEOUT_SECONDS,
            count=12,
        ).start()
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self._is_urgent_tasks_target_prepare_page():
                if self._dismiss_urgent_tasks_times_hint():
                    timeout.reset()
                    continue
                value = self._inspect_urgent_tasks_resource("stamina")
                if value is not None:
                    return value.value

            if timeout.reached():
                logger.warning("UrgentTasks: stamina OCR timeout")
                return None

            if self._handle_dungeon_additional():
                timeout.reset()
                continue

    def _urgent_tasks_prepare_has_pet(self, skip_first_screenshot=True) -> bool:
        """Conservatively decide whether the prepare team has a pet.

        COMBAT_PREPARE_PET_EMPTY is the only explicit state supplied by the
        game.  A populated slot has no dedicated positive asset, so the
        fallback requires the target prepare page to remain stable across
        several fresh frames before treating the slot as populated.  Any
        overlay or page transition resets that confirmation.
        """
        stable = Timer(self.URGENT_TASKS_PET_STABLE_SECONDS, count=3).clear()
        timeout = Timer(4, count=14).start()
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self._is_urgent_tasks_target_prepare_page():
                if self._dismiss_urgent_tasks_times_hint():
                    stable.clear()
                    timeout.reset()
                    continue
                if self.appear(COMBAT_PREPARE_PET_EMPTY):
                    logger.info("UrgentTasks: no pet configured on prepare page")
                    return False
                if not stable.started():
                    stable.start()
                elif stable.reached():
                    logger.info("UrgentTasks: configured pet detected")
                    return True
            else:
                stable.clear()

            if timeout.reached():
                logger.warning("UrgentTasks: pet state timeout, use foreground combat")
                return False

            if self._handle_dungeon_additional():
                stable.clear()
                timeout.reset()
                continue

    def _navigate_urgent_tasks(self, skip_first_screenshot=True) -> bool:
        """
        Enter the configured Urgent Tasks difficulty on global-server Chinese.

        Pages:
            in: main, combat hub, Urgent Tasks detail, or Urgent Tasks prepare
            out: Urgent Tasks prepare, or detail when daily attempts are exhausted
        """
        logger.hr("Urgent Tasks Navigate", level=2)
        difficulty = self._urgent_tasks_difficulty()
        logger.info(f"UrgentTasks: navigate to difficulty={difficulty}")
        timeout = Timer(
            self.URGENT_TASKS_NAVIGATE_TIMEOUT_SECONDS,
            count=120,
        ).start()
        prepare_pending = Timer(
            self.URGENT_TASKS_PREPARE_PENDING_SECONDS,
            count=0,
        ).clear()
        self._urgent_tasks_daily_complete = False
        self._urgent_tasks_unavailable = False
        self._urgent_tasks_remaining_count = None

        target_button, target_selected = (
            (URGENT_TASKS_NORMAL, URGENT_TASKS_NORMAL_SELECTED)
            if difficulty == "Normal"
            else (URGENT_TASKS_SUPERIOR, URGENT_TASKS_SUPERIOR_SELECTED)
        )

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning("UrgentTasks: navigate timeout")
                return False

            if self.appear_then_click(FAST_COMBAT_RESULT_CLOSE, interval=1):
                logger.info("UrgentTasks: close stale fast-combat result")
                timeout.reset()
                continue

            if self._is_urgent_tasks_target_prepare_page():
                if self._dismiss_urgent_tasks_times_hint():
                    timeout.reset()
                    continue
                if self._urgent_tasks_remaining_count is not None:
                    logger.info("UrgentTasks: reached target prepare page")
                    return True
                logger.info("UrgentTasks: return to detail to read daily attempts")
                if self.appear_then_click(BACK, interval=1):
                    timeout.reset()
                    continue

            if self._is_urgent_tasks_prepare_page():
                prepare_pending.clear()
                logger.info("UrgentTasks: leave mismatched difficulty prepare page")
                if self.appear_then_click(BACK, interval=1):
                    timeout.reset()
                    continue

            if self._is_urgent_tasks_detail_page():
                # Entering prepare may take several seconds on a busy device.
                # During that transition the old detail frame remains fully
                # recognizable. Do not OCR and click it again until the game
                # had enough time to either expose prepare or reject the tap.
                if prepare_pending.started() and not prepare_pending.reached():
                    continue

                remaining = self._urgent_tasks_remaining()
                if remaining is not None:
                    self._urgent_tasks_remaining_count = remaining
                if remaining == 0:
                    logger.info("UrgentTasks: daily attempts exhausted")
                    self._urgent_tasks_daily_complete = True
                    return True

                if remaining is not None:
                    if self.match_color(
                        target_selected,
                        threshold=self.COMBAT_STATE_COLOR_THRESHOLD,
                    ):
                        if self.appear_then_click(READY_TO_FIGHT, interval=2):
                            logger.info("UrgentTasks: enter prepare page")
                            prepare_pending.reset()
                            timeout.reset()
                            continue
                    elif self.appear_then_click(target_button, interval=1):
                        logger.info(
                            f"UrgentTasks: select difficulty={difficulty}"
                        )
                        timeout.reset()
                        continue

            if self._is_combat_urgent_board():
                if self.appear_then_click(URGENT_TASKS, interval=2):
                    logger.info("UrgentTasks: open task detail")
                    timeout.reset()
                    continue

            if self._is_combat_general_board() or self._is_combat_season_board():
                if self._is_urgent_tasks_entry_locked():
                    logger.info("UrgentTasks: activity is currently locked")
                    self._urgent_tasks_unavailable = True
                    return True
                if (
                    self._is_urgent_tasks_entry_available()
                    and self.interval_is_reached(URGENT_ENTRY, interval=2)
                ):
                    logger.info("UrgentTasks: switch to activity tab")
                    self.device.click(URGENT_ENTRY)
                    self.interval_reset(URGENT_ENTRY, interval=2)
                    timeout.reset()
                    continue

            if self.is_in_main(interval=0):
                if self.appear_then_click(MAIN_GOTO_COMBAT, interval=1):
                    logger.info("UrgentTasks: enter combat from main")
                    timeout.reset()
                    continue

            if self._handle_dungeon_additional():
                timeout.reset()
                continue

    def _exchange_urgent_tasks_rewards(self, skip_first_screenshot=True) -> bool:
        """Exchange every affordable 100-currency reward on the detail page."""
        logger.hr("Urgent Tasks Reward Exchange", level=2)
        timeout = Timer(20, count=80).start()
        post_click_settle = Timer(
            self.URGENT_TASKS_EXCHANGE_POST_CLICK_SECONDS,
            count=2,
        ).clear()
        batch_retry = Timer(
            self.URGENT_TASKS_EXCHANGE_RETRY_SECONDS,
            count=8,
        ).clear()
        batch_currency = None
        batch_retries = 0

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning("UrgentTasks: reward exchange timeout")
                return False

            if self._is_urgent_tasks_detail_page():
                if post_click_settle.started() and not post_click_settle.reached():
                    continue

                value = self._inspect_urgent_tasks_resource("currency")
                if value is not None:
                    currency = value.value
                    exchanges = currency // self.URGENT_TASKS_EXCHANGE_COST
                    logger.attr("UrgentTasksExchangeCount", exchanges)

                    # One batch is deliberately tied to one OCR snapshot. The
                    # exchange button accepts rapid repeated taps, so scanning
                    # and clicking once per reward only wastes screenshots and
                    # fills Device.click_record with the same button. A
                    # multi_click batch performs one control check, then the
                    # next OCR snapshot proves whether the game consumed it.
                    if batch_currency is not None:
                        if currency < batch_currency:
                            logger.info(
                                "UrgentTasks: reward exchange progressed "
                                f"{batch_currency} -> {currency}"
                            )
                            self.device.click_record_clear()
                            batch_currency = None
                            batch_retries = 0
                            batch_retry.clear()
                            post_click_settle.clear()
                        elif not batch_retry.reached():
                            continue
                        else:
                            batch_retries += 1
                            if batch_retries >= self.URGENT_TASKS_EXCHANGE_MAX_RETRIES:
                                logger.warning(
                                    "UrgentTasks: reward exchange batch did not "
                                    f"progress after {batch_retries} retries"
                                )
                                return False
                            logger.warning(
                                "UrgentTasks: reward exchange batch made no "
                                f"progress at currency={currency}, retry "
                                f"{batch_retries}/{self.URGENT_TASKS_EXCHANGE_MAX_RETRIES}"
                            )
                            batch_currency = None
                            batch_retry.clear()
                            post_click_settle.clear()

                    if exchanges == 0:
                        logger.info(
                            f"UrgentTasks: reward exchange complete, remainder={currency}"
                        )
                        return True

                    if self.appear(URGENT_TASKS_REWARD_EXCHANGE):
                        logger.info(
                            "UrgentTasks: exchange reward batch, "
                            f"count={exchanges}"
                        )
                        self.device.multi_click(
                            URGENT_TASKS_REWARD_EXCHANGE,
                            n=exchanges,
                            interval=self.URGENT_TASKS_EXCHANGE_BATCH_INTERVAL,
                        )
                        batch_currency = currency
                        post_click_settle.reset()
                        batch_retry.reset()
                        timeout.reset()
                        continue

            if self._handle_dungeon_additional():
                timeout.reset()
                continue

    def _run_urgent_tasks_daily(self, skip_first_screenshot=True) -> tuple[bool, int]:
        """Complete every remaining daily Urgent Tasks attempt.

        Pages:
            in: any page accepted by ``_navigate_urgent_tasks``
            out: Urgent Tasks detail page with zero remaining attempts

        The game-owned remaining counter is re-read after every settled mode.
        Fast combat is capped to that counter.  A configured pet may hand the
        remainder to the ordinary server-repeat implementation after the
        stage has been cleared once; without a pet, foreground combat remains
        fully automatic through the shared normal-combat loop.
        """
        completed = 0
        first = skip_first_screenshot
        self._urgent_tasks_repeat_started = False

        while 1:
            if not self._navigate_urgent_tasks(skip_first_screenshot=first):
                return False, completed
            first = True

            if self._urgent_tasks_unavailable:
                self._mark_urgent_tasks_checked()
                return True, completed

            if self._urgent_tasks_daily_complete:
                success = self._exchange_urgent_tasks_rewards(
                    skip_first_screenshot=True
                )
                if success:
                    self._mark_urgent_tasks_checked()
                return success, completed

            remaining = self._urgent_tasks_remaining_count
            if remaining is None:
                logger.warning("UrgentTasks: daily remaining count unavailable")
                return False, completed

            stamina = self._read_urgent_tasks_stamina(skip_first_screenshot=True)
            stamina_cost = self._combat_stage_stamina_cost()

            if self._combat_should_use_fast() and stamina is not None:
                fast_stamina = stamina
                if stamina_cost is not None:
                    fast_stamina = min(stamina, remaining * stamina_cost)
                fast_prepare, fast_count = self._prepare_fast_combat(
                    stamina=fast_stamina,
                    use_max=True,
                    skip_first_screenshot=True,
                )
                if fast_prepare == "ready":
                    if not self._run_fast_combat(skip_first_screenshot=True):
                        return False, completed
                    completed += fast_count
                    remaining -= fast_count
                    if stamina_cost is not None:
                        stamina = max(stamina - fast_count * stamina_cost, 0)
                    if remaining <= 0:
                        first = True
                        continue
                elif fast_prepare not in ("fallback", "no_stamina"):
                    return False, completed

            has_pet = self._urgent_tasks_prepare_has_pet(
                skip_first_screenshot=True
            )
            repeat_ready = has_pet and not self._is_repeat_combat_unavailable()
            if repeat_ready and remaining > 1:
                target_leif_count = self._server_repeat_target_leif_count(stamina)
                if self._prepare_repeat_combat(
                    leif_count=target_leif_count,
                    skip_first_screenshot=True,
                ) and self._run_repeat_combat(skip_first_screenshot=True):
                    self._urgent_tasks_repeat_started = True
                    return True, completed
                return False, completed

            if stamina is not None and stamina_cost is not None and stamina < stamina_cost:
                logger.info(
                    f"UrgentTasks: insufficient stamina ({stamina}/{stamina_cost})"
                )
                return False, completed

            if not self._run_normal_combat(
                completion_check=self._is_urgent_tasks_detail_page,
                skip_first_screenshot=True,
            ):
                return False, completed
            completed += 1
            first = True
