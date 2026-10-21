from module.game_info.context import level_cap
from module.logger import logger
from module.ocr.ocr import Digit
from tasks.arena.assets.assets_arena import OCR_BATTLE_PASS_LEVEL
from tasks.base.assets.assets_base_resource_bar import OCR_RESOURCE_BAR
from tasks.base.resource_bar import (
    RESOURCE_BAR_LAYOUT_ARENA_BATTLE_PASS,
    RESOURCE_BAR_SPECS,
    OcrResourceBar,
    ResourceBarMixin,
    get_resource_bar_segment_area,
    parse_resource_bar_text,
)


def estimate_remaining_arena_flags(current: int, total: int, consumed: int) -> tuple[int, int] | None:
    if total <= 0:
        return None
    if consumed <= 0:
        return current, total
    return max(current - consumed, 0), total


class ArenaDigit(Digit):
    def __init__(self, *args, max_level: int, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_level = max_level

    def after_process(self, result):
        result = result.replace("O", "0").replace("o", "0")
        result = result.replace("I", "1").replace("l", "1").replace("|", "1")
        result = result.replace(" ", "")
        return super().after_process(result)

    def format_result(self, result) -> int:
        normalized = result.strip().lower()
        if normalized == "max":
            logger.attr(name=self.name, text=str(result))
            return self.max_level
        return super().format_result(result)


class ArenaDashboardMixin(ResourceBarMixin):
    ARENA_RESOURCE_BAR_TIMEOUT_SECONDS = 1
    ARENA_RESOURCE_BAR_TIMEOUT_COUNT = 2

    def _ocr_arena_rank(self) -> int:
        maximum = level_cap(self.config, "arena_pass")
        ocr = ArenaDigit(
            OCR_BATTLE_PASS_LEVEL,
            lang=self._ocr_lang(),
            name="ArenaRank",
            max_level=maximum,
        )
        level = ocr.ocr_single_line(self.device.image)
        logger.attr("ArenaRank", level)
        if 0 < level <= maximum:
            self.config.stored.ArenaRank.set(level, maximum)
        return level

    def _ocr_arena_resource_bar(self, skip_first_screenshot=True):
        return self.ocr_resource_bar_status(
            layout=RESOURCE_BAR_LAYOUT_ARENA_BATTLE_PASS,
            layout_name="Arena",
            skip_first_screenshot=skip_first_screenshot,
            timeout_seconds=self.ARENA_RESOURCE_BAR_TIMEOUT_SECONDS,
            timeout_count=self.ARENA_RESOURCE_BAR_TIMEOUT_COUNT,
        )

    def _ocr_arena_flag_status(self, skip_first_screenshot=True) -> tuple[int, int] | None:
        """Read only the live flag field, bounded by its adjacent currency icon."""
        if not skip_first_screenshot:
            self.device.screenshot()
        layout = ("arena_flag", "conquest_point")
        offsets, _ = self._match_resource_bar_icons(layout)
        if len(offsets) != len(layout):
            return None
        area = get_resource_bar_segment_area(layout, "arena_flag", icon_offsets=offsets)
        if area is None:
            return None
        text = OcrResourceBar(
            OCR_RESOURCE_BAR, lang=self._ocr_lang(), name="ArenaFlag",
        ).ocr_single_line(self.image_crop(area, copy=False), direct_ocr=True)
        value = parse_resource_bar_text(text, RESOURCE_BAR_SPECS["arena_flag"])
        if value is None:
            return None

        # Manual play invalidates the previous run's estimate. Only publish
        # this independently anchored field; unrelated currencies may be hidden
        # or malformed, and their failure must not revive the old flag count.
        self.write_resource_bar_status({"arena_flag": value})
        logger.attr("ArenaFlagOCR", f"{value.value}/{value.total}")
        return value.value, value.total

    def _update_arena_dashboard_snapshot(self, skip_first_screenshot=True) -> bool:
        parsed = self._ocr_arena_resource_bar(skip_first_screenshot=skip_first_screenshot)
        return self.write_resource_bar_status(parsed)

    def _stored_arena_flag_status(self) -> tuple[int, int] | None:
        stored = self.config.stored.ArenaFlag
        if stored.total <= 0:
            return None
        return stored.value, stored.total

    def _consume_stored_arena_flags(self, consumed: int) -> bool:
        stored = self.config.stored.ArenaFlag
        estimated = estimate_remaining_arena_flags(
            current=stored.value,
            total=stored.total,
            consumed=consumed,
        )
        if estimated is None:
            logger.info("Arena flag estimate skipped: current counter is unknown")
            return False

        value, total = estimated
        if value == stored.value and total == stored.total:
            return False

        stored.set(value, total)
        logger.attr("ArenaFlagEstimated", f"{value}/{total}")
        return True
