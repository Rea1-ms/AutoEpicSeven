import re
from dataclasses import dataclass

from module.base.timer import Timer
from module.logger import logger
from module.ocr.ocr import OcrWhiteLetterOnComplexBackground
from tasks.base.assets.assets_base_resource_bar import OCR_RESOURCE_BAR, STAMINA_ICON


RESOURCE_KIND_INT = "int"
RESOURCE_KIND_COUNTER = "counter"

RESOURCE_BAR_LAYOUT_MAIN = ("arena_flag", "stamina", "skystone")
RESOURCE_BAR_LAYOUT_SECRET_SHOP = ("gold", "skystone")
RESOURCE_BAR_LAYOUT_COMBAT = ("stamina", "gold", "skystone")
RESOURCE_BAR_LAYOUT_ARENA_BATTLE_PASS = ("arena_flag", "conquest_point", "gold", "skystone")


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
        stored_attr="E7Gold",
    ),
    "skystone": ResourceBarSpec(
        key="skystone",
        kind=RESOURCE_KIND_INT,
        stored_attr="E7Skystone",
    ),
    "stamina": ResourceBarSpec(
        key="stamina",
        kind=RESOURCE_KIND_COUNTER,
        stored_attr="E7Stamina",
    ),
    "arena_flag": ResourceBarSpec(
        key="arena_flag",
        kind=RESOURCE_KIND_COUNTER,
        stored_attr="E7ArenaFlag",
        fixed_total=5,
    ),
    "conquest_point": ResourceBarSpec(
        key="conquest_point",
        kind=RESOURCE_KIND_INT,
        stored_attr="E7ConquestPoint",
    ),
}


def normalize_resource_bar_text(text: str) -> str:
    text = text.strip()
    text = text.replace(" ", "")
    text = text.replace(",", "")
    text = text.replace("，", "")
    text = text.replace("。", "")
    text = text.replace("：", ":")
    text = text.replace("／", "/")
    text = text.replace("I", "1").replace("l", "1").replace("|", "1")
    text = text.replace("O", "0").replace("o", "0")
    text = text.replace("S", "5").replace("s", "5")
    return text


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
        spec = RESOURCE_BAR_SPECS[key]
        if spec.kind == RESOURCE_KIND_COUNTER:
            matched = re.search(r"(\d+)\s*/\s*(\d+)", candidate.text)
            if matched is None:
                return None
            value = int(matched.group(1))
            total = int(matched.group(2))
            if total <= 0:
                return None
            if spec.fixed_total and total != spec.fixed_total:
                return None
            parsed[key] = ResourceBarValue(
                spec=spec,
                text=candidate.text,
                value=value,
                total=total,
            )
            continue

        matched = re.search(r"(\d+)", candidate.text)
        if matched is None:
            return None
        parsed[key] = ResourceBarValue(
            spec=spec,
            text=candidate.text,
            value=int(matched.group(1)),
        )

    return parsed


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

    def ocr_resource_bar_status(
        self,
        layout: tuple[str, ...],
        layout_name: str,
        skip_first_screenshot=True,
    ) -> dict[str, ResourceBarValue] | None:
        timeout = Timer(self.RESOURCE_BAR_TIMEOUT_SECONDS, count=self.RESOURCE_BAR_TIMEOUT_COUNT).start()

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            candidates = self._resource_bar_candidates()
            logger.attr(
                f"{layout_name}ResourceBarCandidates",
                [candidate.text for candidate in candidates],
            )
            parsed = parse_resource_bar_candidates(candidates, layout)
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
                    f"{[candidate.text for candidate in candidates]}"
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
