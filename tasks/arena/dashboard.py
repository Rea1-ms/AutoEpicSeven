from module.logger import logger
from module.ocr.ocr import Digit
from tasks.arena.assets.assets_arena import OCR_BATTLE_PASS_LEVEL
from tasks.base.resource_bar import RESOURCE_BAR_LAYOUT_ARENA_BATTLE_PASS, ResourceBarMixin


def estimate_remaining_arena_flags(current: int, total: int, consumed: int) -> tuple[int, int] | None:
    if total <= 0:
        return None
    if consumed <= 0:
        return current, total
    return max(current - consumed, 0), total


class ArenaDigit(Digit):
    def after_process(self, result):
        result = result.replace("O", "0").replace("o", "0")
        result = result.replace("I", "1").replace("l", "1").replace("|", "1")
        result = result.replace(" ", "")
        return super().after_process(result)


class ArenaDashboardMixin(ResourceBarMixin):
    ARENA_RESOURCE_BAR_TIMEOUT_SECONDS = 1
    ARENA_RESOURCE_BAR_TIMEOUT_COUNT = 2

    def _ocr_arena_rank(self) -> int:
        ocr = ArenaDigit(
            OCR_BATTLE_PASS_LEVEL,
            lang=self._ocr_lang(),
            name="ArenaRank",
        )
        level = ocr.ocr_single_line(self.device.image)
        logger.attr("ArenaRank", level)
        if 0 < level <= self.config.stored.ArenaRank.FIXED_TOTAL:
            self.config.stored.ArenaRank.set(level)
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
        parsed = self._ocr_arena_resource_bar(skip_first_screenshot=skip_first_screenshot)
        if parsed is None:
            return None

        self.write_resource_bar_status(parsed)
        value = parsed.get("arena_flag")
        if value is None:
            return None

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
