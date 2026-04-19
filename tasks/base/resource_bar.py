import re
from dataclasses import dataclass

from module.base.timer import Timer
from module.base.utils import area_offset
from module.logger import logger
from module.ocr.ocr import OcrWhiteLetterOnComplexBackground
from tasks.base.assets.assets_base_resource_bar import (
    ARENA_FLAG_ICON,
    CONQUEST_POINT_ICON,
    GOLD_ICON,
    OCR_RESOURCE_BAR,
    SKYSTONE_ICON,
    STAMINA_ICON,
)


RESOURCE_KIND_INT = "int"
RESOURCE_KIND_COUNTER = "counter"

RESOURCE_BAR_LAYOUT_MAIN = ("arena_flag", "stamina", "skystone")
RESOURCE_BAR_LAYOUT_SECRET_SHOP = ("gold", "skystone")
RESOURCE_BAR_LAYOUT_COMBAT = ("stamina", "gold", "skystone")
RESOURCE_BAR_LAYOUT_ARENA_BATTLE_PASS = ("arena_flag", "conquest_point", "gold", "skystone")
RESOURCE_BAR_SEGMENT_LEFT_PADDING = 2
RESOURCE_BAR_SEGMENT_RIGHT_PADDING = 6

# Max horizontal reach, in pixels from the last currency icon's right edge,
# used as the OCR right boundary when the last field in a layout has no
# "next icon" to clamp against. All four current layouts end in skystone.
#
# Calibrated against the REPEAT_COMBAT_CHECK auto-combat marker that appears
# to the right of skystone on certain pages. Measured pixel distances from
# the skystone icon's asset right edge:
#   - 7-digit skystone digits reach icon.right + 90 (observed max)
#   - REPEAT_COMBAT_CHECK body sits ~45px past the 7-digit end
#   - moving-element ring extends 0-21px outward from the REPEAT asset edge
#     (so 24-45px past the last digit)
#   - halo extends 21-31px outward from the REPEAT asset edge
#     (so 14-24px past the last digit)
#   - only the inner 0-14px past the last digit is fully empty
# 100 covers 7-digit skystone with a 10px buffer and stops 4px before the
# halo begins. Beyond 104 we'd start OCR-ing dynamic glow pixels, which is
# how the "196园" / "196#" trailing-noise artifact was produced before.
RESOURCE_BAR_TAIL_MAX_WIDTH = 100


@dataclass(frozen=True)
class ResourceBarSpec:
    key: str
    kind: str
    stored_attr: str | None = None
    fixed_total: int = 0


@dataclass(frozen=True)
class ResourceBarCandidate:
    box: tuple[int, int, int, int]
    text: str


@dataclass(frozen=True)
class ResourceBarValue:
    spec: ResourceBarSpec
    text: str
    value: int
    total: int = 0


@dataclass
class ResourceBarInspectResult:
    layout_name: str
    icon_matches: list[str]
    icon_offsets: dict[str, tuple[int, int]]
    candidates: list[ResourceBarCandidate]
    parsed_by_icon: dict[str, ResourceBarValue] | None
    parsed_by_candidates: dict[str, ResourceBarValue] | None
    parsed_by_candidate_split: dict[str, ResourceBarValue] | None
    final: dict[str, ResourceBarValue] | None
    final_source: str = ""


RESOURCE_BAR_SPECS = {
    "gold": ResourceBarSpec(
        key="gold",
        kind=RESOURCE_KIND_INT,
        stored_attr="Gold",
    ),
    "skystone": ResourceBarSpec(
        key="skystone",
        kind=RESOURCE_KIND_INT,
        stored_attr="Skystone",
    ),
    "stamina": ResourceBarSpec(
        key="stamina",
        kind=RESOURCE_KIND_COUNTER,
        stored_attr="Stamina",
    ),
    "arena_flag": ResourceBarSpec(
        key="arena_flag",
        kind=RESOURCE_KIND_COUNTER,
        stored_attr="ArenaFlag",
        fixed_total=5,
    ),
    "conquest_point": ResourceBarSpec(
        key="conquest_point",
        kind=RESOURCE_KIND_INT,
        stored_attr="ConquestPoint",
    ),
}

RESOURCE_BAR_ICONS = {
    "arena_flag": ARENA_FLAG_ICON,
    "conquest_point": CONQUEST_POINT_ICON,
    "gold": GOLD_ICON,
    "skystone": SKYSTONE_ICON,
    "stamina": STAMINA_ICON,
}


def normalize_resource_bar_text(text: str) -> str:
    text = text.strip()
    text = text.replace(" ", "")
    text = text.replace("$", "")
    text = text.replace(",", "")
    text = text.replace("，", "")
    text = text.replace("。", "")
    text = text.replace("：", ":")
    text = text.replace("／", "/")
    text = text.replace("I", "1").replace("l", "1").replace("|", "1")
    text = text.replace("O", "0").replace("o", "0")
    text = text.replace("S", "5").replace("s", "5")
    return text


def parse_resource_bar_text(
    text: str,
    spec: ResourceBarSpec,
) -> ResourceBarValue | None:
    """
    Parse a single segment's OCR text into a ResourceBarValue, or None on
    malformed input.

    Note on the intentional lack of a `value <= total` check for counters:
    in Epic Seven, the counter `total` represents the passive regeneration
    cap (e.g. 5 arena flags per day, 360 stamina at player level), not a
    hard ceiling. Inventory-held stamina potions and stockpiled event flags
    routinely push `value` well above `total` (observed: stamina 19448/336,
    arena flags 344/5). A previous implementation both rejected `value > total`
    AND tried to "recover" by chopping leading/trailing digits of the
    numerator until it fit under `total`; that recovery silently truncated
    legitimate large readings like 534/336 into 34/336. Both behaviours are
    removed here. The remaining guards (`total <= 0`, `spec.fixed_total`
    mismatch) catch genuine OCR corruption of the denominator without
    corrupting a legitimately-large numerator.
    """
    text = normalize_resource_bar_text(text)

    if spec.kind == RESOURCE_KIND_COUNTER:
        matched = re.search(r"(\d+)\s*/\s*(\d+)", text)
        if matched is None:
            return None
        value = int(matched.group(1))
        total = int(matched.group(2))
        if total <= 0:
            return None
        if spec.fixed_total and total != spec.fixed_total:
            return None
        return ResourceBarValue(
            spec=spec,
            text=text,
            value=value,
            total=total,
        )

    matched = re.search(r"(\d+)", text)
    if matched is None:
        return None
    return ResourceBarValue(
        spec=spec,
        text=text,
        value=int(matched.group(1)),
    )


def get_resource_bar_segment_area(
    layout: tuple[str, ...],
    key: str,
    icon_offsets: dict[str, tuple[int, int]] | None = None,
) -> tuple[int, int, int, int] | None:
    try:
        index = layout.index(key)
    except ValueError:
        return None

    if icon_offsets is None:
        icon_offsets = {}

    icon = RESOURCE_BAR_ICONS[key]
    current_offset = icon_offsets.get(key, (0, 0))
    current_icon_area = area_offset(icon.area, current_offset)
    x1 = max(OCR_RESOURCE_BAR.area[0], current_icon_area[2] - RESOURCE_BAR_SEGMENT_LEFT_PADDING)
    if index + 1 < len(layout):
        next_icon = RESOURCE_BAR_ICONS[layout[index + 1]]
        next_icon_area = area_offset(next_icon.area, icon_offsets.get(layout[index + 1], (0, 0)))
        x2 = min(OCR_RESOURCE_BAR.area[2], next_icon_area[0] - RESOURCE_BAR_SEGMENT_RIGHT_PADDING)
    else:
        # Last field in the layout has no right-hand icon to clamp against.
        # Capping at `icon_right + TAIL_MAX_WIDTH` stops OCR before it can
        # reach the REPEAT_COMBAT_CHECK auto-combat marker (and its halo /
        # moving-element dynamic pixels) that appears to the right of
        # skystone on certain pages. See RESOURCE_BAR_TAIL_MAX_WIDTH for the
        # geometry behind the 100px choice.
        x2 = min(
            OCR_RESOURCE_BAR.area[2],
            current_icon_area[2] + RESOURCE_BAR_TAIL_MAX_WIDTH,
        )

    if x2 <= x1:
        return None
    return (
        x1,
        OCR_RESOURCE_BAR.area[1] + current_offset[1],
        x2,
        OCR_RESOURCE_BAR.area[3] + current_offset[1],
    )


class OcrResourceBar(OcrWhiteLetterOnComplexBackground):
    def after_process(self, result):
        result = super().after_process(result)
        return normalize_resource_bar_text(result)

    def filter_detected(self, result) -> bool:
        text = normalize_resource_bar_text(result.ocr_text)
        return bool(re.search(r"\d", text))


class ResourceBarMixin:
    RESOURCE_BAR_TIMEOUT_SECONDS = 2.5
    RESOURCE_BAR_TIMEOUT_COUNT = 8

    def _ocr_lang(self) -> str:
        lang = getattr(self.config, "Emulator_GameLanguage", "cn")
        if lang in ("auto", "", None, "cn", "global_cn", "zh", "zh_cn"):
            return "cn"
        if lang in ("en", "global_en", "en_us"):
            return "en"
        if lang in ("jp", "ja", "ja_jp"):
            return "jp"
        if lang in ("tw", "zh_tw"):
            return "tw"
        return "cn"

    def _match_resource_bar_icons(
        self,
        layout: tuple[str, ...],
    ) -> tuple[dict[str, tuple[int, int]], list[str]]:
        """
        Match every currency icon in the resource bar band.

        Unlike the earlier implementation, a single icon miss no longer aborts
        the scan. A transient overlay or fade on one currency would otherwise
        pin the whole frame to the fallback path and leak garbage into stored
        data. We match all icons, then reject the whole frame if any missed
        or if the matched icons are not left-to-right in layout order.

        Returns:
            icon_offsets: {key: (dx, dy)} when every icon matched in order.
                          Empty dict when any icon missed or order was wrong.
            matched_icons: per-icon status string, always populated for log.
        """
        icon_offsets: dict[str, tuple[int, int]] = {}
        matched_icons: list[str] = []

        for key in layout:
            icon = RESOURCE_BAR_ICONS[key]
            icon.load_search(OCR_RESOURCE_BAR.area)
            if icon.match_template(self.device.image, similarity=0.85):
                offset = tuple(int(value) for value in icon.button_offset)
                icon_offsets[key] = offset
                matched_icons.append(f"{key}={offset}")
            else:
                matched_icons.append(f"{key}=miss")

        if len(icon_offsets) != len(layout):
            return {}, matched_icons

        # Guard against a template binding to the wrong slot. Template matching
        # without positional check can let e.g. skystone pick up gold's glyph
        # when the bar is partially occluded, producing plausible-but-wrong
        # OCR segments downstream.
        last_x = -1
        for key in layout:
            matched_x = RESOURCE_BAR_ICONS[key].area[0] + icon_offsets[key][0]
            if matched_x <= last_x:
                matched_icons.append(f"order_violation:{key}")
                return {}, matched_icons
            last_x = matched_x

        return icon_offsets, matched_icons

    def _resource_bar_by_icon(
        self,
        layout: tuple[str, ...],
        layout_name: str,
        icon_offsets: dict[str, tuple[int, int]],
        matched_icons: list[str],
    ) -> dict[str, ResourceBarValue] | None:
        parsed: dict[str, ResourceBarValue] = {}
        raw_texts: list[str] = []
        if len(icon_offsets) != len(layout):
            logger.attr(f"{layout_name}ResourceBarIconMatches", matched_icons)
            logger.attr(f"{layout_name}ResourceBarIconSegments", raw_texts)
            return None

        for key in layout:
            area = get_resource_bar_segment_area(layout, key, icon_offsets=icon_offsets)
            if area is None:
                logger.attr(f"{layout_name}ResourceBarIconMatches", matched_icons)
                logger.attr(f"{layout_name}ResourceBarIconSegments", raw_texts)
                return None

            image = self.image_crop(area, copy=False)
            text = OcrResourceBar(
                OCR_RESOURCE_BAR,
                lang=self._ocr_lang(),
                name=f"{layout_name}ResourceBar.{key}",
            ).ocr_single_line(image, direct_ocr=True)
            raw_texts.append(f"{key}={text}")

            value = parse_resource_bar_text(text, spec=RESOURCE_BAR_SPECS[key])
            if value is None:
                logger.attr(f"{layout_name}ResourceBarIconMatches", matched_icons)
                logger.attr(f"{layout_name}ResourceBarIconSegments", raw_texts)
                return None
            parsed[key] = value

        logger.attr(f"{layout_name}ResourceBarIconMatches", matched_icons)
        logger.attr(f"{layout_name}ResourceBarIconSegments", raw_texts)
        return parsed

    @staticmethod
    def format_resource_bar_value(value: ResourceBarValue) -> str:
        if value.spec.kind == RESOURCE_KIND_COUNTER:
            return f"{value.value}/{value.total}"
        return str(value.value)

    def _log_resource_bar_values(
        self,
        layout: tuple[str, ...],
        parsed: dict[str, ResourceBarValue],
    ) -> None:
        for key in layout:
            logger.attr(f"ResourceBar.{key}", self.format_resource_bar_value(parsed[key]))

    def inspect_resource_bar_status(
        self,
        layout: tuple[str, ...],
        layout_name: str,
        log_result: bool = True,
    ) -> ResourceBarInspectResult:
        """
        Inspect a single already-captured frame of the resource bar.

        Single-frame, no retry. Only the icon-anchored path is evaluated.
        Candidate-based fallbacks were removed because their text-fraction
        splitting produced spatially plausible but numerically wrong readings
        that then poisoned stored dashboard values. If the icon path fails
        here, the production caller retries; offline debug prints the miss.

        The legacy `parsed_by_candidates`, `parsed_by_candidate_split`, and
        `candidates` fields on the returned result are kept only so the
        offline debug script's schema does not break; they are always empty
        or None now.
        """
        icon_offsets, matched_icons = self._match_resource_bar_icons(layout)
        parsed_by_icon = self._resource_bar_by_icon(
            layout=layout,
            layout_name=layout_name,
            icon_offsets=icon_offsets,
            matched_icons=matched_icons,
        )

        final = parsed_by_icon
        final_source = "icon" if final is not None else ""

        if final is not None and log_result:
            self._log_resource_bar_values(layout, final)

        return ResourceBarInspectResult(
            layout_name=layout_name,
            icon_matches=matched_icons,
            icon_offsets=icon_offsets,
            candidates=[],
            parsed_by_icon=parsed_by_icon,
            parsed_by_candidates=None,
            parsed_by_candidate_split=None,
            final=final,
            final_source=final_source,
        )

    def ocr_resource_bar_status(
        self,
        layout: tuple[str, ...],
        layout_name: str,
        skip_first_screenshot=True,
        timeout_seconds: float | None = None,
        timeout_count: int | None = None,
    ) -> dict[str, ResourceBarValue] | None:
        if timeout_seconds is None:
            timeout_seconds = self.RESOURCE_BAR_TIMEOUT_SECONDS
        if timeout_count is None:
            timeout_count = self.RESOURCE_BAR_TIMEOUT_COUNT

        timeout = Timer(timeout_seconds, count=timeout_count).start()

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            inspected = self.inspect_resource_bar_status(
                layout=layout,
                layout_name=layout_name,
                log_result=True,
            )
            parsed = inspected.final
            if parsed is not None:
                return parsed

            if timeout.reached():
                logger.warning(
                    f"Resource bar OCR timeout on {layout_name}: "
                    f"icons={inspected.icon_matches}"
                )
                return None

    def write_resource_bar_status(self, parsed: dict[str, ResourceBarValue] | None) -> bool:
        if not parsed:
            return False

        updated = False
        with self.config.multi_set():
            for value in parsed.values():
                attr = value.spec.stored_attr
                if not attr or not hasattr(self.config.stored, attr):
                    continue

                stored = getattr(self.config.stored, attr)
                if value.spec.kind == RESOURCE_KIND_COUNTER:
                    stored.set(value.value, value.total)
                else:
                    stored.value = value.value
                updated = True

        return updated
