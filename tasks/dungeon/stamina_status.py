from dataclasses import dataclass

from module.base.timer import Timer
from module.logger import logger
from tasks.base.assets.assets_base_popup import AD_BUFF_X_CLOSE
from tasks.base.resource_bar import (
    RESOURCE_KIND_INT,
    OcrResourceBar,
    ResourceBarSpec,
    ResourceBarValue,
    parse_resource_bar_text,
)
from tasks.dungeon.assets.assets_dungeon_repeat_entry import REPEAT_COMBAT_MENU
from tasks.dungeon.assets.assets_dungeon_stamina_status import (
    LEIF_ICON,
    OCR_LEIF,
    OCR_SKYSTONE,
    OCR_SPECTRAL_CORE,
    OCR_STAMINA,
    SKYSTONE_ICON,
    SPECTRAL_CORE_ICON,
    STAMINA_ICON,
)


PET_REPEAT_RESOURCE_LAYOUT = ("spectral_core", "stamina", "leif", "skystone")
PET_REPEAT_RESOURCE_FIELDS = {
    "spectral_core": (SPECTRAL_CORE_ICON, OCR_SPECTRAL_CORE),
    "stamina": (STAMINA_ICON, OCR_STAMINA),
    "leif": (LEIF_ICON, OCR_LEIF),
    "skystone": (SKYSTONE_ICON, OCR_SKYSTONE),
}
PET_REPEAT_RESOURCE_SPECS = {
    key: ResourceBarSpec(key=key, kind=RESOURCE_KIND_INT)
    for key in PET_REPEAT_RESOURCE_LAYOUT
}


@dataclass(frozen=True)
class PetRepeatResourceInspectResult:
    values: dict[str, ResourceBarValue] | None
    icon_matches: list[str]


class CombatStaminaStatusMixin:
    def _ocr_pet_repeat_resources(self) -> PetRepeatResourceInspectResult:
        """Read four fixed resource fields from the pet-repeat panel."""
        icon_matches = []
        for key in PET_REPEAT_RESOURCE_LAYOUT:
            icon, _ = PET_REPEAT_RESOURCE_FIELDS[key]
            if icon.match_template_luma(
                self.device.image,
                similarity=self.COMBAT_CHECK_SIMILARITY,
            ):
                icon_matches.append(f"{key}=matched")
            else:
                icon_matches.append(f"{key}=miss")

        if any(match.endswith("=miss") for match in icon_matches):
            logger.attr("PetRepeatResourceIcons", icon_matches)
            return PetRepeatResourceInspectResult(values=None, icon_matches=icon_matches)

        values = {}
        raw_texts = []
        for key in PET_REPEAT_RESOURCE_LAYOUT:
            _, ocr_button = PET_REPEAT_RESOURCE_FIELDS[key]
            text = OcrResourceBar(
                ocr_button,
                lang=self._ocr_lang(),
                name=f"PetRepeatResource.{key}",
            ).ocr_single_line(self.device.image)
            raw_texts.append(f"{key}={text}")
            value = parse_resource_bar_text(text, PET_REPEAT_RESOURCE_SPECS[key])
            if value is None:
                logger.attr("PetRepeatResourceIcons", icon_matches)
                logger.attr("PetRepeatResourceTexts", raw_texts)
                return PetRepeatResourceInspectResult(values=None, icon_matches=icon_matches)
            values[key] = value

        logger.attr("PetRepeatResourceIcons", icon_matches)
        logger.attr("PetRepeatResourceTexts", raw_texts)
        return PetRepeatResourceInspectResult(
            values=values,
            icon_matches=icon_matches,
        )

    def _update_prepare_resource_snapshot(self, skip_first_screenshot=True):
        """Read the pet-repeat resource panel and return to combat prepare.

        Pages:
            in: combat prepare
            out: combat prepare

        Returns:
            dict | None: All four parsed resources, or None after a clean exit
                when the panel could not be read.
        """
        logger.hr("Combat Prepare Resources", level=2)
        timeout = Timer(self.COMBAT_PREPARE_TIMEOUT_SECONDS, count=90).start()
        ocr_timeout = Timer(self.RESOURCE_BAR_TIMEOUT_SECONDS, count=self.RESOURCE_BAR_TIMEOUT_COUNT).clear()
        parsed = None
        close_pending = False

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning("Combat: prepare resource snapshot timeout")
                if self.appear(AD_BUFF_X_CLOSE):
                    close_pending = True
                else:
                    return None

            if self._is_prepare_page():
                if close_pending:
                    return parsed

                if not self._ensure_fast_combat_state(enabled=False):
                    timeout.reset()
                    continue

                if self.appear_then_click(REPEAT_COMBAT_MENU, interval=1):
                    logger.info("Combat: open pet repeat resource panel")
                    ocr_timeout.reset()
                    timeout.reset()
                    continue

            if self.appear(AD_BUFF_X_CLOSE):
                if not close_pending:
                    inspected = self._ocr_pet_repeat_resources()
                    if inspected.values is not None:
                        parsed = inspected.values
                        close_pending = True
                    elif ocr_timeout.started() and ocr_timeout.reached():
                        logger.warning(
                            "Combat: pet repeat resource OCR timeout, "
                            f"icons={inspected.icon_matches}"
                        )
                        close_pending = True

                if close_pending and self.appear_then_click(AD_BUFF_X_CLOSE, interval=1):
                    logger.info("Combat: close pet repeat resource panel")
                    timeout.reset()
                    continue

                continue

            if self._handle_repeat_count_overlay_additional():
                timeout.reset()
                continue
