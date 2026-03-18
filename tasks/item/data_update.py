import re

from module.logger import logger
from module.ocr.ocr import Digit, DigitCounter
from tasks.arena.dashboard import ArenaDashboardMixin
from tasks.base.page import (
    page_arena,
    page_combat_season,
    page_inventory_equipment,
    page_main,
    page_secret_shop,
)
from tasks.base.resource_bar import (
    RESOURCE_BAR_LAYOUT_COMBAT,
    RESOURCE_BAR_LAYOUT_SECRET_SHOP,
)
from tasks.base.ui import UI
from tasks.combat.assets.assets_combat_configs_entry import OCR_SEASON_CHECK
from tasks.item.assets.assets_item_inventory import OCR_EQUIPMENT_COUNT


class E7Digit(Digit):
    def after_process(self, result):
        result = result.replace("O", "0").replace("o", "0")
        result = result.replace("I", "1").replace("l", "1").replace("|", "1")
        result = result.replace(" ", "")
        return super().after_process(result)


def normalize_e7_counter_text(result: str) -> str:
    result = result.replace("O", "0").replace("o", "0")
    result = result.replace("I", "1").replace("l", "1").replace("|", "1")
    result = result.replace(" ", "")
    result = result.replace(",", "").replace("，", "")
    result = result.replace("／", "/")
    return result


def parse_e7_counter_text(result: str) -> tuple[int, int] | None:
    result = normalize_e7_counter_text(result)
    matched = re.search(r"(\d+)\s*/\s*(\d+)", result)
    if matched is None:
        return None
    return int(matched.group(1)), int(matched.group(2))


class E7DigitCounter(DigitCounter):
    def after_process(self, result):
        return normalize_e7_counter_text(result)


class DataUpdate(ArenaDashboardMixin, UI):
    def _sync_legacy_item_storage(self):
        with self.config.multi_set():
            self.config.stored.Credit.value = self.config.stored.E7Gold.value
            self.config.stored.StallerJade.value = self.config.stored.E7Skystone.value

    def _ocr_equipment_inventory_count(self) -> tuple[int, int, int]:
        current, remain, total = E7DigitCounter(
            OCR_EQUIPMENT_COUNT,
            lang=self._ocr_lang(),
            name="EquipmentInventoryCount",
        ).ocr_single_line(self.device.image)
        if total > 0:
            logger.attr("EquipmentInventoryCount", f"{current}/{total}")
        else:
            logger.warning(f"Equipment inventory OCR invalid: {current}/{total}")
        return current, remain, total

    def _update_equipment_inventory(self, skip_first_screenshot=True) -> bool:
        logger.hr("DataUpdate EquipmentInventory", level=2)
        self.ui_goto(page_inventory_equipment, skip_first_screenshot=skip_first_screenshot)
        current, _, total = self._ocr_equipment_inventory_count()

        updated = False
        if total > 0:
            self.config.stored.E7EquipmentInventory.set(current, total)
            updated = True

        self.ui_goto(page_main, skip_first_screenshot=True)
        return updated

    def _update_secret_shop_resources(self, skip_first_screenshot=True) -> bool:
        logger.hr("DataUpdate SecretShop", level=2)
        self.ui_goto(page_secret_shop, skip_first_screenshot=skip_first_screenshot)
        parsed = self.ocr_resource_bar_status(
            layout=RESOURCE_BAR_LAYOUT_SECRET_SHOP,
            layout_name="SecretShop",
            skip_first_screenshot=True,
        )
        return self.write_resource_bar_status(parsed)

    def _ocr_shadow_commission_level(self) -> int:
        level = E7Digit(
            OCR_SEASON_CHECK,
            lang=self._ocr_lang(),
            name="ShadowCommissionLevel",
        ).ocr_single_line(self.device.image)
        logger.attr("ShadowCommissionLevel", level)
        if 0 < level <= self.config.stored.E7ShadowCommission.FIXED_TOTAL:
            self.config.stored.E7ShadowCommission.set(level)
        return level

    def _update_combat_status(self, skip_first_screenshot=True) -> bool:
        logger.hr("DataUpdate Combat", level=2)
        self.ui_goto(page_combat_season, skip_first_screenshot=skip_first_screenshot)
        updated = False
        parsed = self.ocr_resource_bar_status(
            layout=RESOURCE_BAR_LAYOUT_COMBAT,
            layout_name="Combat",
            skip_first_screenshot=True,
        )
        if self.write_resource_bar_status(parsed):
            updated = True
        if self._ocr_shadow_commission_level() > 0:
            updated = True
        return updated

    def _enter_arena(self, skip_first_screenshot=True) -> bool:
        logger.info("DataUpdate: goto arena page")
        self.ui_goto(page_arena, skip_first_screenshot=skip_first_screenshot)
        return True

    def _update_arena_status(self, skip_first_screenshot=True) -> bool:
        logger.hr("DataUpdate Arena", level=2)
        if not self._enter_arena(skip_first_screenshot=skip_first_screenshot):
            return False
        return self._update_arena_dashboard_snapshot(skip_first_screenshot=True)

    def run(self):
        logger.hr("Data Update", level=1)

        if not self.device.app_is_running():
            from tasks.login.login import Login

            Login(self.config, device=self.device).app_start()

        if not hasattr(self.device, "image") or self.device.image is None:
            self.device.screenshot()

        updated_any = False

        updated_any |= self._update_equipment_inventory(skip_first_screenshot=False)
        updated_any |= self._update_secret_shop_resources(skip_first_screenshot=True)
        updated_any |= self._update_combat_status(skip_first_screenshot=True)
        updated_any |= self._update_arena_status(skip_first_screenshot=True)

        self.ui_goto(page_main, skip_first_screenshot=True)
        self._sync_legacy_item_storage()

        if updated_any:
            self.config.task_delay(server_update=True)
            return True

        self.config.task_delay(success=False)
        return False
