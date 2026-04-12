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


def normalize_resource_bar_counter_text(
    text: str,
    spec: ResourceBarSpec,
) -> str:
    text = normalize_resource_bar_text(text)
    matched = re.search(r"(\d+)\s*/\s*(\d+)", text)
    if matched is None:
        return text

    current_text = matched.group(1)
    total_text = matched.group(2)
    total = int(total_text)

    candidate_currents: list[str] = [current_text]
    if current_text.startswith("5") and len(current_text) > 1:
        candidate_currents.append(current_text[1:])
    if current_text.endswith("10") and len(current_text) > 2:
        candidate_currents.append(current_text[:-2])
    if current_text.endswith("0") and len(current_text) > 1:
        candidate_currents.append(current_text[:-1])
    if current_text.endswith("1") and len(current_text) > 1:
        candidate_currents.append(current_text[:-1])

    valid_currents = [
        current
        for current in candidate_currents
        if current.isdigit() and current and int(current) <= total
    ]
    if not valid_currents:
        return text

    corrected_current = max(valid_currents, key=lambda item: int(item))
    return f"{corrected_current}/{total_text}"


def expand_resource_bar_candidates(
    candidates: list[ResourceBarCandidate],
) -> list[ResourceBarCandidate]:
    expanded: list[ResourceBarCandidate] = []
    token_pattern = re.compile(r"\d+\s*/\s*\d+|\d+")

    for candidate in sorted(candidates, key=lambda item: item.box[0]):
        text = normalize_resource_bar_text(candidate.text)
        matches = list(token_pattern.finditer(text))
        if not matches:
            continue

        if len(matches) == 1:
            expanded.append(
                ResourceBarCandidate(
                    box=candidate.box,
                    text=matches[0].group(0),
                )
            )
            continue

        x1, y1, x2, y2 = candidate.box
        width = max(1, x2 - x1)
        text_len = max(1, len(text))

        for matched in matches:
            start = x1 + int(width * matched.start() / text_len)
            end = x1 + int(width * matched.end() / text_len)
            if end <= start:
                end = start + 1
            expanded.append(
                ResourceBarCandidate(
                    box=(start, y1, end, y2),
                    text=matched.group(0),
                )
            )

    return expanded


def merge_resource_bar_candidates(
    candidates: list[ResourceBarCandidate],
    expected_count: int,
    max_gap: int = 24,
) -> list[ResourceBarCandidate] | None:
    merged = sorted(candidates, key=lambda item: item.box[0])
    if len(merged) < expected_count:
        return None

    while len(merged) > expected_count:
        best_index = -1
        best_gap = None
        for index in range(len(merged) - 1):
            left = merged[index]
            right = merged[index + 1]
            gap = right.box[0] - left.box[2]
            if best_gap is None or gap < best_gap:
                best_gap = gap
                best_index = index

        if best_gap is None or best_gap > max_gap:
            return None

        left = merged[best_index]
        right = merged[best_index + 1]
        merged_box = (
            min(left.box[0], right.box[0]),
            min(left.box[1], right.box[1]),
            max(left.box[2], right.box[2]),
            max(left.box[3], right.box[3]),
        )
        merged_text = f"{left.text}{right.text}"
        merged = (
            merged[:best_index]
            + [ResourceBarCandidate(box=merged_box, text=merged_text)]
            + merged[best_index + 2 :]
        )

    return merged


def maybe_correct_combat_stamina_candidate(
    candidate: ResourceBarCandidate,
    layout: tuple[str, ...],
    key: str,
    min_icon_overlap: int = 4,
) -> ResourceBarCandidate:
    if layout != RESOURCE_BAR_LAYOUT_COMBAT or key != "stamina":
        return candidate

    matched = re.fullmatch(r"(\d+)\s*/\s*(\d+)", candidate.text)
    if matched is None:
        return candidate

    current = matched.group(1)
    if len(current) < 2 or not current.startswith("5"):
        return candidate

    icon_x1, _, icon_x2, _ = STAMINA_ICON.area
    overlap = min(candidate.box[2], icon_x2) - max(candidate.box[0], icon_x1)
    if overlap < min_icon_overlap:
        return candidate

    corrected = f"{current[1:]}/{matched.group(2)}"
    logger.attr("CombatStaminaIconFix", f"{candidate.text} -> {corrected}")
    return ResourceBarCandidate(box=candidate.box, text=corrected)


def parse_resource_bar_candidates(
    candidates: list[ResourceBarCandidate],
    layout: tuple[str, ...],
) -> dict[str, ResourceBarValue] | None:
    expanded = expand_resource_bar_candidates(candidates)
    merged = merge_resource_bar_candidates(expanded, expected_count=len(layout))
    if merged is None:
        return None

    parsed: dict[str, ResourceBarValue] = {}
    for key, candidate in zip(layout, merged):
        candidate = maybe_correct_combat_stamina_candidate(candidate, layout=layout, key=key)
        value = parse_resource_bar_text(candidate.text, spec=RESOURCE_BAR_SPECS[key])
        if value is None:
            return None
        parsed[key] = value

    return parsed


def parse_resource_bar_text(
    text: str,
    spec: ResourceBarSpec,
) -> ResourceBarValue | None:
    if spec.kind == RESOURCE_KIND_COUNTER:
        text = normalize_resource_bar_counter_text(text, spec=spec)
    else:
        text = normalize_resource_bar_text(text)
    if spec.kind == RESOURCE_KIND_COUNTER:
        matched = re.search(r"(\d+)\s*/\s*(\d+)", text)
        if matched is None:
            return None
        value = int(matched.group(1))
        total = int(matched.group(2))
        if total <= 0:
            return None
        if value > total:
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


def split_resource_bar_text_for_specs(
    text: str,
    specs: list[ResourceBarSpec],
    boundary_hints: list[int] | None = None,
) -> list[ResourceBarValue] | None:
    text = normalize_resource_bar_text(text)
    if not specs:
        return [] if not text else None

    if len(specs) == 1:
        value = parse_resource_bar_text(text, spec=specs[0])
        return [value] if value is not None else None

    if boundary_hints:
        target = boundary_hints[0]
        split_indexes: list[int] = []
        seen: set[int] = set()
        for delta in range(0, len(text)):
            for index in (target - delta, target + delta):
                if 1 <= index < len(text) and index not in seen:
                    split_indexes.append(index)
                    seen.add(index)
        for index in range(1, len(text)):
            if index not in seen:
                split_indexes.append(index)
    else:
        split_indexes = list(range(1, len(text)))

    for split_index in split_indexes:
        left = parse_resource_bar_text(text[:split_index], spec=specs[0])
        if left is None:
            continue

        next_hints = None
        if boundary_hints:
            next_hints = [index - split_index for index in boundary_hints[1:]]
        right = split_resource_bar_text_for_specs(
            text[split_index:],
            specs=specs[1:],
            boundary_hints=next_hints,
        )
        if right is not None:
            return [left] + right

    return None


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
        x2 = OCR_RESOURCE_BAR.area[2]

    if x2 <= x1:
        return None
    return (
        x1,
        OCR_RESOURCE_BAR.area[1] + current_offset[1],
        x2,
        OCR_RESOURCE_BAR.area[3] + current_offset[1],
    )


def get_resource_bar_boundary_hints(
    candidate: ResourceBarCandidate,
    segment_areas: list[tuple[int, int, int, int]],
    text: str,
) -> list[int]:
    text = normalize_resource_bar_text(text)
    if len(segment_areas) <= 1 or not text:
        return []

    x1, _, x2, _ = candidate.box
    width = max(1, x2 - x1)
    hints: list[int] = []
    for index in range(len(segment_areas) - 1):
        boundary_x = int((segment_areas[index][2] + segment_areas[index + 1][0]) / 2)
        split_index = round(len(text) * (boundary_x - x1) / width)
        split_index = max(1, min(len(text) - 1, split_index))
        hints.append(split_index)
    return hints


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

    def _resource_bar_candidates(self) -> list[ResourceBarCandidate]:
        results = OcrResourceBar(
            OCR_RESOURCE_BAR,
            lang=self._ocr_lang(),
            name="ResourceBarOCR",
        ).detect_and_ocr(self.device.image)
        candidates = [
            ResourceBarCandidate(box=result.box, text=result.ocr_text)
            for result in sorted(results, key=lambda item: item.box[0])
        ]
        return candidates

    def _match_resource_bar_icons(
        self,
        layout: tuple[str, ...],
    ) -> tuple[dict[str, tuple[int, int]], list[str]]:
        icon_offsets: dict[str, tuple[int, int]] = {}
        matched_icons: list[str] = []

        for key in layout:
            icon = RESOURCE_BAR_ICONS[key]
            icon.load_search(OCR_RESOURCE_BAR.area)
            if not icon.match_template(self.device.image, similarity=0.75):
                matched_icons.append(f"{key}=miss")
                return icon_offsets, matched_icons

            offset = tuple(int(value) for value in icon.button_offset)
            icon_offsets[key] = offset
            matched_icons.append(f"{key}={offset}")

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

    def _resource_bar_from_candidates_by_icon(
        self,
        candidates: list[ResourceBarCandidate],
        layout: tuple[str, ...],
        layout_name: str,
        icon_offsets: dict[str, tuple[int, int]],
    ) -> dict[str, ResourceBarValue] | None:
        parsed: dict[str, ResourceBarValue] = {}
        split_logs: list[str] = []
        layout_index = 0

        segment_areas: list[tuple[int, int, int, int] | None] = [
            get_resource_bar_segment_area(layout, key, icon_offsets=icon_offsets) for key in layout
        ]
        if any(area is None for area in segment_areas):
            return None

        for candidate in sorted(candidates, key=lambda item: item.box[0]):
            if layout_index >= len(layout):
                break

            overlapped: list[int] = []
            for index in range(layout_index, len(layout)):
                area = segment_areas[index]
                overlap = min(candidate.box[2], area[2]) - max(candidate.box[0], area[0])
                if overlap > 0:
                    overlapped.append(index)

            if not overlapped:
                continue

            start = overlapped[0]
            end = overlapped[-1]
            if start != layout_index:
                return None

            specs = [RESOURCE_BAR_SPECS[layout[index]] for index in range(start, end + 1)]
            boundaries = get_resource_bar_boundary_hints(
                candidate,
                segment_areas=segment_areas[start : end + 1],
                text=candidate.text,
            )
            values = split_resource_bar_text_for_specs(
                candidate.text,
                specs=specs,
                boundary_hints=boundaries,
            )
            if values is None:
                logger.attr(
                    f"{layout_name}ResourceBarSplitFailed",
                    f"{candidate.text} -> {[spec.key for spec in specs]}",
                )
                return None

            for offset, value in enumerate(values):
                key = layout[start + offset]
                parsed[key] = value
                split_logs.append(f"{key}={value.text}")

            layout_index = end + 1

        if len(parsed) != len(layout):
            return None

        logger.attr(f"{layout_name}ResourceBarCandidateSplit", split_logs)
        return parsed

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

            icon_offsets, matched_icons = self._match_resource_bar_icons(layout)
            parsed = self._resource_bar_by_icon(
                layout=layout,
                layout_name=layout_name,
                icon_offsets=icon_offsets,
                matched_icons=matched_icons,
            )
            if parsed is None:
                candidates = self._resource_bar_candidates()
                logger.attr(
                    f"{layout_name}ResourceBarCandidates",
                    [candidate.text for candidate in candidates],
                )
                parsed = parse_resource_bar_candidates(candidates, layout)
                if parsed is None:
                    parsed = self._resource_bar_from_candidates_by_icon(
                        candidates=candidates,
                        layout=layout,
                        layout_name=layout_name,
                        icon_offsets=icon_offsets,
                    )
            if parsed is not None:
                for key in layout:
                    value = parsed[key]
                    if value.spec.kind == RESOURCE_KIND_COUNTER:
                        logger.attr(f"ResourceBar.{key}", f"{value.value}/{value.total}")
                    else:
                        logger.attr(f"ResourceBar.{key}", value.value)
                return parsed

            if timeout.reached():
                logger.warning(
                    f"Resource bar OCR timeout on {layout_name}: "
                    f"{[candidate.text for candidate in candidates] if 'candidates' in locals() else 'icon-primary-failed'}"
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
