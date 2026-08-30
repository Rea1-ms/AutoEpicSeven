from datetime import datetime, timedelta

import module.config.server as server
from module.base.button import ButtonWrapper, ClickButton
from module.base.timer import Timer
from module.exception import RequestHumanTakeover
from module.logger import logger
from module.ocr.ocr import Digit, DigitCounter, Duration
from tasks.base.assets.assets_base_page import BACK
from tasks.base.assets.assets_base_popup import AD_BUFF_X_CLOSE
from tasks.base.resource_bar import (
    RESOURCE_KIND_INT,
    OcrResourceBar,
    ResourceBarSpec,
    ResourceBarValue,
    parse_resource_bar_text,
)
from tasks.dungeon.assets.assets_dungeon_repeat_battle import (
    OCR_REPEAT_COMBAT_TIMES,
    PRIORITIZE_OWNED_STAMINA_OFF,
    PRIORITIZE_OWNED_STAMINA_ON,
    REPEAT_COMBAT_TIMES_MAX,
    REPEAT_COMBAT_TIMES_MINIMUM,
    REPEAT_COMBAT_TIMES_MINUS,
    REPEAT_COMBAT_TIMES_PLUS,
)
from tasks.dungeon.assets.assets_dungeon_repeat_common import (
    OCR_REMAINING_TIME,
    REPEAT_COMBAT_CHECK,
    REPEAT_COMBAT_MENU,
    REPEAT_COMBAT_MENU_CHECK,
    REPEAT_COMBAT_OVER,
    REPEAT_COMBAT_UNAVAILABLE,
    REPEAT_START,
    TAB_AUTO_BATTLE_SETTINGS,
    TAB_AUTO_BATTLE_SETTINGS_CHECK,
    TAB_AUTO_GEAR_SORT_SETTINGS,
    TAB_AUTO_GEAR_SORT_SETTINGS_CHECK,
)
from tasks.dungeon.assets.assets_dungeon_repeat_gear import (
    EQUIPMENT_SCORE_SLIDER,
    EXCLUDE_OTHERWORLD_LEGENDARY_OFF,
    EXCLUDE_OTHERWORLD_LEGENDARY_ON,
    FLAT_MAIN_STAT_EXCEPT_SPEED_OFF,
    FLAT_MAIN_STAT_EXCEPT_SPEED_ON,
    GEAR_EXTRACT_SELECTED,
    GEAR_EXTRACT_UNSELECTED,
    GEAR_SELL_SELECTED,
    GEAR_SELL_UNSELECTED,
    HERO_SPEED_FILTER_OFF,
    HERO_SPEED_FILTER_ON,
    HERO_SPEED_SLIDER,
    LEGENDARY_SPEED_FILTER_OFF,
    LEGENDARY_SPEED_FILTER_ON,
    LEGENDARY_SPEED_SLIDER,
    OCR_EQUIPMENT_SCORE,
    OCR_HERO_SPEED,
    OCR_LEGENDARY_SPEED,
)
from tasks.dungeon.assets.assets_dungeon_repeat_resource import (
    LEIF_ICON,
    OCR_STAMINA_BAR,
    SPECTRAL_CORE_ICON,
    STAMINA_ICON,
)
from tasks.dungeon.assets.assets_dungeon_repeat_settlement import (
    SETTLEMENT_CLOSE,
    SETTLEMENT_PROCESSING,
    SETTLEMENT_SETTLE,
    SETTLEMENT_WINDOW_CHECK,
)


SERVER_REPEAT_RESOURCE_SPECS = {
    "stamina": ResourceBarSpec(key="stamina", kind=RESOURCE_KIND_INT),
    "spectral_core": ResourceBarSpec(key="spectral_core", kind=RESOURCE_KIND_INT),
    "leif": ResourceBarSpec(key="leif", kind=RESOURCE_KIND_INT),
}
SERVER_REPEAT_RESOURCE_ICONS = {
    "stamina": STAMINA_ICON,
    "spectral_core": SPECTRAL_CORE_ICON,
    "leif": LEIF_ICON,
}


class RepeatCombatDigit(Digit):
    def after_process(self, result):
        result = result.replace("O", "0").replace("o", "0")
        result = result.replace("I", "1").replace("l", "1").replace("|", "1")
        result = result.replace(" ", "").replace(",", "").replace("，", "")
        return super().after_process(result)


class RepeatCombatCounter(DigitCounter):
    def after_process(self, result):
        result = result.replace("O", "0").replace("o", "0")
        result = result.replace("I", "1").replace("l", "1").replace("|", "1")
        result = result.replace(" ", "")
        return super().after_process(result)


class CombatRepeatMixin:
    REPEAT_MENU_TIMEOUT_SECONDS = 30
    REPEAT_SETTING_COLOR_THRESHOLD = 12
    REPEAT_AVAILABILITY_COLOR_THRESHOLD = 8
    REPEAT_SLIDER_MAX_PASSES = 2
    REPEAT_FINISH_BUFFER_MINUTES = 1

    REPEAT_SLIDER_GEOMETRY = {
        "equipment_score": (EQUIPMENT_SCORE_SLIDER, 20, 23, 6),
        "hero_speed": (HERO_SPEED_SLIDER, 18, 14, 3),
        "legendary_speed": (LEGENDARY_SPEED_SLIDER, 18, 18, 4),
    }

    def _uses_server_repeat_combat(self) -> bool:
        return server.lang == "global_cn"

    def _repeat_combat_leif_count(self) -> int:
        value = getattr(self.config, "Combat_RepeatCombatLeifCount", 1)
        return self._sanitize_combat_count(
            value,
            default=1,
            max_value=50,
            name="RepeatCombatLeifCount",
        )

    def _repeat_prioritize_stamina(self) -> bool:
        return bool(getattr(self.config, "Combat_RepeatCombatPrioritizeStamina", True))

    def _repeat_gear_mode(self) -> str:
        value = str(getattr(self.config, "Combat_RepeatCombatGearMode", "Extract"))
        if value not in ("Sell", "Extract"):
            logger.warning(
                f"Combat: invalid RepeatCombatGearMode={value}, fallback to Extract"
            )
            return "Extract"
        return value

    def _repeat_equipment_score(self) -> int:
        return self._repeat_setting_int("RepeatCombatEquipmentScore", 28)

    def _repeat_hero_speed(self) -> int:
        return self._repeat_setting_int("RepeatCombatHeroSpeed", 4)

    def _repeat_legendary_speed(self) -> int:
        return self._repeat_setting_int("RepeatCombatLegendarySpeed", 4)

    def _repeat_setting_int(self, name: str, default: int) -> int:
        value = getattr(self.config, f"Combat_{name}", default)
        try:
            return int(value)
        except (TypeError, ValueError):
            logger.warning(f"Combat: invalid {name}={value}, fallback to {default}")
            return default

    def _is_prepare_page(self) -> bool:
        if not self._uses_server_repeat_combat():
            return super()._is_prepare_page()
        return self.match_template_luma(
            REPEAT_COMBAT_MENU,
            similarity=self.COMBAT_CHECK_SIMILARITY,
        ) or self.match_template_luma(
            REPEAT_COMBAT_UNAVAILABLE,
            similarity=self.COMBAT_CHECK_SIMILARITY,
        )

    def _is_repeat_combat_unavailable(self) -> bool:
        if not self._uses_server_repeat_combat():
            return False
        return self.match_color(
            REPEAT_COMBAT_UNAVAILABLE,
            threshold=self.REPEAT_AVAILABILITY_COLOR_THRESHOLD,
        )

    def _raise_if_repeat_combat_unavailable(self) -> None:
        if not self._is_repeat_combat_unavailable():
            return
        message = (
            "Combat: server repeat combat unavailable. "
            "Complete this stage manually at least once before enabling repeat combat."
        )
        logger.critical(message)
        raise RequestHumanTakeover(message)

    def _is_repeat_menu_open(self) -> bool:
        return self.match_template_luma(
            REPEAT_COMBAT_MENU_CHECK,
            similarity=self.COMBAT_CHECK_SIMILARITY,
        )

    def _is_repeat_battle_tab(self) -> bool:
        return self.match_template_luma(
            TAB_AUTO_BATTLE_SETTINGS_CHECK,
            similarity=self.COMBAT_CHECK_SIMILARITY,
        )

    def _is_repeat_gear_tab(self) -> bool:
        return self.match_template_luma(
            TAB_AUTO_GEAR_SORT_SETTINGS_CHECK,
            similarity=self.COMBAT_CHECK_SIMILARITY,
        )

    def _ensure_repeat_toggle(
        self,
        enabled: bool,
        on_button: ButtonWrapper,
        off_button: ButtonWrapper,
        label: str,
    ) -> bool:
        expected = on_button if enabled else off_button
        opposite = off_button if enabled else on_button
        if self.match_color(expected, threshold=self.REPEAT_SETTING_COLOR_THRESHOLD):
            return True
        if self.match_color(opposite, threshold=self.REPEAT_SETTING_COLOR_THRESHOLD):
            if self.interval_is_reached(opposite, interval=1):
                logger.info(f"Combat: set {label}={enabled}")
                self.device.click(opposite)
                self.interval_reset(opposite, interval=1)
            return False
        return False

    def _ensure_repeat_gear_mode(self) -> bool:
        mode = self._repeat_gear_mode()
        if mode == "Sell":
            selected, unselected = GEAR_SELL_SELECTED, GEAR_SELL_UNSELECTED
        else:
            selected, unselected = GEAR_EXTRACT_SELECTED, GEAR_EXTRACT_UNSELECTED

        if self.match_color(selected, threshold=self.REPEAT_SETTING_COLOR_THRESHOLD):
            return True
        if self.match_color(unselected, threshold=self.REPEAT_SETTING_COLOR_THRESHOLD):
            if self.interval_is_reached(unselected, interval=1):
                logger.info(f"Combat: set repeat gear mode={mode}")
                self.device.click(unselected)
                self.interval_reset(unselected, interval=1)
            return False
        return False

    @classmethod
    def _repeat_slider_points(cls, key: str) -> list[ClickButton]:
        slider, left_inset, right_inset, count = cls.REPEAT_SLIDER_GEOMETRY[key]
        x1, y1, x2, y2 = slider.area
        start = x1 + left_inset
        end = x2 - right_inset
        center_y = (y1 + y2) // 2
        points = []
        for index in range(count):
            divisor = count - 1
            x = start + ((end - start) * index + divisor // 2) // divisor
            points.append(
                ClickButton(
                    area=(x - 4, center_y - 4, x + 5, center_y + 5),
                    name=f"REPEAT_{key.upper()}_{index}",
                )
            )
        return points

    def _ensure_repeat_slider(
        self,
        key: str,
        target: int,
        ocr_button: ButtonWrapper,
        scan_state: dict[str, int],
    ) -> bool:
        settle = getattr(self, "_repeat_slider_settle", None)
        if settle is not None and not settle.reached():
            return False

        value = RepeatCombatDigit(
            ocr_button,
            lang=self._ocr_lang(),
            name=f"RepeatCombat.{key}",
        ).ocr_single_line(self.device.image)
        if value == target:
            logger.attr(f"RepeatCombat.{key}.Ready", value)
            scan_state.pop(key, None)
            return True

        points = self._repeat_slider_points(key)
        attempt = scan_state.get(key, 0)
        maximum_attempts = len(points) * self.REPEAT_SLIDER_MAX_PASSES
        if attempt >= maximum_attempts:
            message = f"Combat: repeat {key} slider has no selectable value {target}"
            logger.critical(message)
            raise RequestHumanTakeover(message)

        point = points[attempt % len(points)]
        if self.interval_is_reached(point, interval=0.6):
            logger.info(
                f"Combat: scan repeat {key} slider "
                f"({attempt % len(points) + 1}/{len(points)}), target={target}"
            )
            self.device.click(point)
            self.interval_reset(point, interval=0.6)
            scan_state[key] = attempt + 1
            self._repeat_slider_settle = Timer(0.6, count=2).start()
        return False

    def _ocr_repeat_leif_counter(self) -> tuple[int, int]:
        current, _, total = RepeatCombatCounter(
            OCR_REPEAT_COMBAT_TIMES,
            lang=self._ocr_lang(),
            name="RepeatCombatLeifCount",
        ).ocr_single_line(self.device.image)
        logger.attr("RepeatCombatLeifCurrent", current)
        logger.attr("RepeatCombatLeifMaximum", total)
        return current, total

    def _ensure_repeat_leif_count(self) -> bool:
        current, maximum = self._ocr_repeat_leif_counter()
        if maximum <= 0:
            return False

        target = min(self._repeat_combat_leif_count(), maximum)
        if current == target:
            logger.attr("RepeatCombatLeifTarget", target)
            return True

        if target == 1 and self.appear_then_click(
            REPEAT_COMBAT_TIMES_MINIMUM, interval=1
        ):
            logger.info("Combat: set repeat leif count to minimum")
            return False
        if target == maximum and self.appear_then_click(
            REPEAT_COMBAT_TIMES_MAX, interval=1
        ):
            logger.info("Combat: set repeat leif count to maximum")
            return False

        diff = target - current
        button = REPEAT_COMBAT_TIMES_PLUS if diff > 0 else REPEAT_COMBAT_TIMES_MINUS
        if self.appear(button) and self.interval_is_reached(button, interval=0.8):
            logger.info(f"Combat: adjust repeat leif count {current}->{target}")
            self.device.multi_click(button, n=abs(diff), interval=(0.2, 0.3))
            self.interval_reset(button, interval=0.8)
        return False

    def _ocr_repeat_remaining_duration(self) -> timedelta:
        duration = Duration(
            OCR_REMAINING_TIME,
            lang=self._ocr_lang(),
            name="RepeatCombatRemainingTime",
        ).ocr_single_line(self.device.image)
        logger.attr("RepeatCombatRemainingSeconds", int(duration.total_seconds()))
        return duration

    def _match_repeat_resource_icon(self, key: str) -> tuple[int, int] | None:
        icon = SERVER_REPEAT_RESOURCE_ICONS[key]
        icon.load_search(OCR_STAMINA_BAR.area)
        # These are intentionally tiny, unique glyphs. A 0.75 threshold keeps
        # matching stable across the bright and dim menu variants while the
        # strict left-to-right layout check below prevents slot confusion.
        if not icon.match_template(self.device.image, similarity=0.75):
            return None
        return tuple(int(value) for value in icon.button_offset)

    def _ocr_server_repeat_resources(self) -> dict[str, ResourceBarValue] | None:
        offsets = {
            key: self._match_repeat_resource_icon(key)
            for key in ("stamina", "spectral_core", "leif")
        }
        primary = [
            key for key in ("stamina", "spectral_core") if offsets[key] is not None
        ]
        icon_log = [
            f"{key}={offsets[key] if offsets[key] is not None else 'miss'}"
            for key in offsets
        ]
        if len(primary) != 1 or offsets["leif"] is None:
            logger.attr("RepeatCombatResourceIcons", icon_log)
            return None

        layout = (primary[0], "leif")
        parsed = {}
        raw_texts = []
        for index, key in enumerate(layout):
            icon = SERVER_REPEAT_RESOURCE_ICONS[key]
            offset = offsets[key]
            assert offset is not None
            icon_area = tuple(
                value + delta for value, delta in zip(icon.area, (*offset, *offset))
            )
            left = max(OCR_STAMINA_BAR.area[0], icon_area[2] - 2)
            if index + 1 < len(layout):
                next_key = layout[index + 1]
                next_icon = SERVER_REPEAT_RESOURCE_ICONS[next_key]
                next_offset = offsets[next_key]
                assert next_offset is not None
                next_area = tuple(
                    value + delta
                    for value, delta in zip(
                        next_icon.area, (*next_offset, *next_offset)
                    )
                )
                right = next_area[0] - 6
            else:
                right = OCR_STAMINA_BAR.area[2]

            if right <= left:
                logger.attr("RepeatCombatResourceIcons", icon_log)
                return None

            crop = self.image_crop(
                (left, OCR_STAMINA_BAR.area[1], right, OCR_STAMINA_BAR.area[3]),
                copy=False,
            )
            text = OcrResourceBar(
                OCR_STAMINA_BAR,
                lang=self._ocr_lang(),
                name=f"RepeatCombatResource.{key}",
            ).ocr_single_line(crop, direct_ocr=True)
            raw_texts.append(f"{key}={text}")
            value = parse_resource_bar_text(text, SERVER_REPEAT_RESOURCE_SPECS[key])
            if value is None:
                logger.attr("RepeatCombatResourceIcons", icon_log)
                logger.attr("RepeatCombatResourceTexts", raw_texts)
                return None
            parsed[key] = value

        logger.attr("RepeatCombatResourceIcons", icon_log)
        logger.attr("RepeatCombatResourceTexts", raw_texts)
        return parsed

    def _update_prepare_resource_snapshot(self, skip_first_screenshot=True):
        if not self._uses_server_repeat_combat():
            return super()._update_prepare_resource_snapshot(
                skip_first_screenshot=skip_first_screenshot
            )

        logger.hr("Combat Prepare Resources", level=2)
        timeout = Timer(self.COMBAT_PREPARE_TIMEOUT_SECONDS, count=90).start()
        parsed = None
        close_pending = False

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning("Combat: server repeat resource snapshot timeout")
                return None

            if self._is_prepare_page():
                self._raise_if_repeat_combat_unavailable()
                if close_pending:
                    return parsed
                if self.appear_then_click(REPEAT_COMBAT_MENU, interval=1):
                    logger.info("Combat: open server repeat menu for resource snapshot")
                    timeout.reset()
                    continue

            if self._is_repeat_menu_open():
                if not close_pending:
                    parsed = self._ocr_server_repeat_resources()
                    if parsed is not None:
                        close_pending = True
                if close_pending and self.appear_then_click(
                    AD_BUFF_X_CLOSE, interval=1
                ):
                    logger.info("Combat: close server repeat resource panel")
                    timeout.reset()
                    continue

            if self._handle_repeat_count_overlay_additional():
                timeout.reset()
                continue

    def _prepare_repeat_combat(
        self,
        skip_first_screenshot=True,
        use_max=False,
        clamp_to_counter=False,
        affordable_count: int | None = None,
        completed_count: int = 0,
    ) -> bool:
        if not self._uses_server_repeat_combat():
            return super()._prepare_repeat_combat(
                skip_first_screenshot=skip_first_screenshot,
                use_max=use_max,
                clamp_to_counter=clamp_to_counter,
                affordable_count=affordable_count,
                completed_count=completed_count,
            )

        logger.hr("Combat Prepare Server Repeat", level=2)
        timeout = Timer(self.REPEAT_MENU_TIMEOUT_SECONDS, count=120).start()
        duration_retry = Timer(3, count=8).clear()
        stage = "open"
        slider_scan: dict[str, int] = {}
        self._repeat_combat_estimated_duration = timedelta()

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning(
                    f"Combat: configure server repeat timeout at stage={stage}"
                )
                return False

            if self._is_prepare_page():
                self._raise_if_repeat_combat_unavailable()
                if self.appear_then_click(REPEAT_COMBAT_MENU, interval=1):
                    logger.info("Combat: open server repeat settings")
                    timeout.reset()
                    continue

            if not self._is_repeat_menu_open():
                if self._handle_repeat_count_overlay_additional():
                    timeout.reset()
                continue

            if self._handle_repeat_count_overlay_additional():
                timeout.reset()
                continue

            if stage == "open":
                stage = "battle_tab"

            if stage == "battle_tab":
                if self._is_repeat_battle_tab():
                    stage = "stamina"
                    continue
                if self.appear_then_click(TAB_AUTO_BATTLE_SETTINGS, interval=1):
                    timeout.reset()
                    continue

            if stage == "stamina":
                if self._ensure_repeat_toggle(
                    self._repeat_prioritize_stamina(),
                    PRIORITIZE_OWNED_STAMINA_ON,
                    PRIORITIZE_OWNED_STAMINA_OFF,
                    "prioritize owned stamina",
                ):
                    stage = "leif"
                continue

            if stage == "leif":
                if self._ensure_repeat_leif_count():
                    stage = "gear_tab"
                continue

            if stage == "gear_tab":
                if self._is_repeat_gear_tab():
                    stage = "gear_mode"
                    continue
                if self.appear_then_click(TAB_AUTO_GEAR_SORT_SETTINGS, interval=1):
                    timeout.reset()
                    continue

            if stage == "gear_mode":
                if self._ensure_repeat_gear_mode():
                    stage = "equipment_score"
                continue

            if stage == "equipment_score":
                if self._ensure_repeat_slider(
                    "equipment_score",
                    self._repeat_equipment_score(),
                    OCR_EQUIPMENT_SCORE,
                    slider_scan,
                ):
                    stage = "hero_filter"
                continue

            if stage == "hero_filter":
                enabled = bool(
                    getattr(self.config, "Combat_RepeatCombatHeroSpeedFilter", True)
                )
                if self._ensure_repeat_toggle(
                    enabled,
                    HERO_SPEED_FILTER_ON,
                    HERO_SPEED_FILTER_OFF,
                    "hero speed filter",
                ):
                    stage = "hero_speed" if enabled else "legendary_filter"
                continue

            if stage == "hero_speed":
                if self._ensure_repeat_slider(
                    "hero_speed",
                    self._repeat_hero_speed(),
                    OCR_HERO_SPEED,
                    slider_scan,
                ):
                    stage = "legendary_filter"
                continue

            if stage == "legendary_filter":
                enabled = bool(
                    getattr(
                        self.config, "Combat_RepeatCombatLegendarySpeedFilter", True
                    )
                )
                if self._ensure_repeat_toggle(
                    enabled,
                    LEGENDARY_SPEED_FILTER_ON,
                    LEGENDARY_SPEED_FILTER_OFF,
                    "legendary speed filter",
                ):
                    stage = "legendary_speed" if enabled else "flat_main_stat"
                continue

            if stage == "legendary_speed":
                if self._ensure_repeat_slider(
                    "legendary_speed",
                    self._repeat_legendary_speed(),
                    OCR_LEGENDARY_SPEED,
                    slider_scan,
                ):
                    stage = "flat_main_stat"
                continue

            if stage == "flat_main_stat":
                enabled = bool(
                    getattr(
                        self.config, "Combat_RepeatCombatFlatMainStatExceptSpeed", True
                    )
                )
                if self._ensure_repeat_toggle(
                    enabled,
                    FLAT_MAIN_STAT_EXCEPT_SPEED_ON,
                    FLAT_MAIN_STAT_EXCEPT_SPEED_OFF,
                    "flat main stat except speed",
                ):
                    stage = "otherworld_legendary"
                continue

            if stage == "otherworld_legendary":
                enabled = bool(
                    getattr(
                        self.config,
                        "Combat_RepeatCombatExcludeOtherworldLegendary",
                        False,
                    )
                )
                if self._ensure_repeat_toggle(
                    enabled,
                    EXCLUDE_OTHERWORLD_LEGENDARY_ON,
                    EXCLUDE_OTHERWORLD_LEGENDARY_OFF,
                    "exclude otherworld legendary",
                ):
                    stage = "duration_tab"
                continue

            if stage == "duration_tab":
                if self._is_repeat_battle_tab():
                    duration_retry.reset()
                    stage = "duration"
                    continue
                if self.appear_then_click(TAB_AUTO_BATTLE_SETTINGS, interval=1):
                    timeout.reset()
                continue

            if stage == "duration":
                duration = self._ocr_repeat_remaining_duration()
                if duration.total_seconds() > 0:
                    self._repeat_combat_estimated_duration = duration
                    logger.info(f"Combat: server repeat estimated duration={duration}")
                    return True
                if duration_retry.reached():
                    logger.warning(
                        "Combat: repeat remaining time OCR failed, use short polling"
                    )
                    return True
                continue

    def _run_repeat_combat(self, skip_first_screenshot=True) -> bool:
        if not self._uses_server_repeat_combat():
            return super()._run_repeat_combat(
                skip_first_screenshot=skip_first_screenshot
            )

        logger.info("Combat: start server repeat combat")
        timeout = Timer(self.COMBAT_RUN_TIMEOUT_SECONDS, count=240).start()
        start_clicked = False

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning("Combat: start server repeat combat timeout")
                return False

            self._raise_if_package_full()

            if start_clicked and self._has_repeat_combat_check():
                duration = getattr(
                    self, "_repeat_combat_estimated_duration", timedelta()
                )
                if duration.total_seconds() > 0:
                    self._repeat_combat_expected_finish_at = (
                        datetime.now()
                        + duration
                        + timedelta(minutes=self.REPEAT_FINISH_BUFFER_MINUTES)
                    )
                else:
                    self._repeat_combat_expected_finish_at = None
                logger.info("Combat: server repeat combat accepted")
                return True

            if self._is_repeat_menu_open():
                if self.appear_then_click(
                    REPEAT_START, interval=self.COMBAT_START_INTERVAL_SECONDS
                ):
                    logger.info("Combat: request server repeat combat")
                    start_clicked = True
                    timeout.reset()
                    continue

            if self.handle_popup_confirm(interval=1):
                logger.info("Combat: confirm server repeat combat")
                start_clicked = True
                timeout.reset()
                continue

            if self._handle_dungeon_network_error(interval=1):
                timeout.reset()
                continue

            if self.handle_ui_recovery():
                timeout.reset()
                continue

    def _is_repeat_result_window(self) -> bool:
        if not self._uses_server_repeat_combat():
            return super()._is_repeat_result_window()
        return self.match_template_luma(
            SETTLEMENT_WINDOW_CHECK,
            similarity=self.COMBAT_CHECK_SIMILARITY,
        )

    def _is_repeat_combat_over(self) -> bool:
        if not self._uses_server_repeat_combat():
            return super()._is_repeat_combat_over()
        return self.match_template_luma(
            REPEAT_COMBAT_OVER,
            similarity=self.COMBAT_CHECK_SIMILARITY,
        )

    def _has_repeat_combat_check(self) -> bool:
        if not self._uses_server_repeat_combat():
            return super()._has_repeat_combat_check()
        return self.match_template_luma(
            REPEAT_COMBAT_CHECK,
            similarity=self.COMBAT_CHECK_SIMILARITY,
        )

    def _watch_repeat_combat(self, skip_first_screenshot=True) -> str:
        if not self._uses_server_repeat_combat():
            return super()._watch_repeat_combat(
                skip_first_screenshot=skip_first_screenshot
            )

        logger.info("Combat: watch server repeat combat")
        timeout = Timer(self.COMBAT_WATCH_TIMEOUT_SECONDS, count=60).start()
        stage = "watch"
        finish_confirm = Timer(0.4, count=2).clear()
        missing_check_confirm = Timer(
            self.COMBAT_MISSING_CHECK_CONFIRM_SECONDS, count=2
        ).clear()

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning(
                    "Combat: server repeat watch timeout, keep session active"
                )
                return "running"

            if self._handle_dungeon_network_error(interval=1):
                return "running"

            if stage == "watch":
                if self.appear_then_click(REPEAT_COMBAT_OVER, interval=1):
                    logger.info("Combat: server repeat complete, open settlement")
                    stage = "settlement"
                    timeout.reset()
                    continue
                if self._is_repeat_result_window():
                    stage = "settlement"
                    timeout.reset()
                    continue
                if self._is_repeat_combat_running():
                    logger.info("Combat: server repeat combat still running")
                    return "running"
                if self.is_in_main(interval=0):
                    if not missing_check_confirm.started():
                        missing_check_confirm.start()
                    elif missing_check_confirm.reached():
                        logger.warning("Combat: server repeat marker disappeared")
                        return "lost"
                else:
                    missing_check_confirm.clear()
                if self._handle_dungeon_additional():
                    timeout.reset()
                    continue

            if stage == "settlement":
                if self.appear_then_click(SETTLEMENT_SETTLE, interval=1):
                    logger.info("Combat: settle server repeat rewards")
                    timeout.reset()
                    continue
                if self.appear(SETTLEMENT_PROCESSING):
                    continue
                if self.appear_then_click(SETTLEMENT_CLOSE, interval=1):
                    logger.info("Combat: close server repeat settlement")
                    stage = "finish"
                    timeout.reset()
                    continue
                if self.appear_then_click(REPEAT_COMBAT_OVER, interval=1):
                    timeout.reset()
                    continue
                if self._handle_dungeon_additional():
                    timeout.reset()
                    continue

            if stage == "finish":
                if self._is_repeat_result_window():
                    if self.appear_then_click(SETTLEMENT_CLOSE, interval=1):
                        timeout.reset()
                    continue
                if self.is_in_main(interval=0):
                    if not finish_confirm.started():
                        finish_confirm.start()
                    elif finish_confirm.reached():
                        logger.info("Combat: server repeat settlement finished")
                        return "finished"
                else:
                    finish_confirm.clear()
                if self._handle_dungeon_additional():
                    timeout.reset()
                    continue
                if self._is_in_dungeon_context() and self.appear_then_click(
                    BACK, interval=1
                ):
                    timeout.reset()
                    continue

    def _combat_runtime_build(self) -> dict:
        session = super()._combat_runtime_build()
        if not self._uses_server_repeat_combat():
            return session
        session["mode"] = "repeat_server"
        expected = getattr(self, "_repeat_combat_expected_finish_at", None)
        if isinstance(expected, datetime):
            session["expected_finish_at"] = expected.isoformat(timespec="seconds")
        return session

    def _combat_runtime_build_detected_existing(self) -> dict:
        session = super()._combat_runtime_build_detected_existing()
        if self._uses_server_repeat_combat():
            session["mode"] = "repeat_server"
        return session

    def _delay_running_repeat_combat(self) -> None:
        if not self._uses_server_repeat_combat():
            self.config.task_delay(minute=self.COMBAT_BACKGROUND_CHECK_MINUTES)
            return

        raw = self._combat_runtime_session().get("expected_finish_at")
        if isinstance(raw, str):
            try:
                target = datetime.fromisoformat(raw)
            except ValueError:
                target = None
            if target is not None and target > datetime.now():
                logger.attr("CombatRepeatExpectedFinishAt", target)
                self.config.task_delay(target=target)
                return

        self.config.task_delay(minute=self.COMBAT_BACKGROUND_CHECK_MINUTES)
