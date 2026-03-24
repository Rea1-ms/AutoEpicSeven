import re
from dataclasses import dataclass
from typing import Literal

from module.base.button import ButtonWrapper, ClickButton
from module.ocr.ocr import Digit, DigitCounter

PurchaseQuantityStrategy = Literal["once", "target", "max"]
PurchaseAdjustAction = Literal["none", "min", "max", "plus", "minus"]


@dataclass(frozen=True)
class PurchaseCounterPreset:
    name: str
    area: tuple[int, int, int, int]

    def to_button(self) -> ClickButton:
        return ClickButton(self.area, name=self.name)


@dataclass(frozen=True)
class ItemPurchasePlan:
    name: str
    asset: ButtonWrapper
    desired_quantity: int = 1
    quantity_strategy: PurchaseQuantityStrategy = "once"
    direct_click: bool = False
    scroll_before: bool = False
    counter_preset: PurchaseCounterPreset | None = None
    purchase_limit: int = 1
    remaining_counter_preset: PurchaseCounterPreset | None = None


@dataclass(frozen=True)
class PurchaseResult:
    success: bool
    quantity: int = 0
    counter: tuple[int, int, int] = (0, 0, 0)
    quantity_source: str = "default"


@dataclass(frozen=True)
class PurchaseSelection:
    quantity: int = 1
    action: PurchaseAdjustAction = "none"
    counter: tuple[int, int, int] = (0, 0, 0)
    source: str = "default"


class StorePurchaseCounterOcr(DigitCounter):
    def after_process(self, result):
        return normalize_purchase_counter_text(result)


class StoreRemainingPurchaseTimesOcr(Digit):
    def after_process(self, result):
        return normalize_purchase_counter_text(result)


def normalize_purchase_counter_text(result: str) -> str:
    result = result.strip()
    result = result.replace(" ", "")
    result = result.replace(",", "").replace("，", "")
    result = result.replace("。", "").replace("：", ":")
    result = result.replace("／", "/")
    result = result.replace("I", "1").replace("l", "1").replace("|", "1")
    result = result.replace("O", "0").replace("o", "0")
    result = result.replace("S", "5").replace("s", "5")
    return result


def parse_purchase_counter_text(result: str) -> tuple[int, int, int]:
    result = normalize_purchase_counter_text(result)
    matched = re.search(r"(\d+)\s*/\s*(\d+)", result)
    if matched is None:
        return 0, 0, 0

    current = int(matched.group(1))
    total = int(matched.group(2))
    if total <= 0 or current < 0 or current > total:
        return 0, 0, 0
    return current, total - current, total


def parse_remaining_buy_times_text(result: str) -> int:
    result = normalize_purchase_counter_text(result)
    matched = re.search(r"(\d+)", result)
    if matched is None:
        return 0
    return int(matched.group(1))


def counter_to_text(counter: tuple[int, int, int]) -> str:
    current, _, total = counter
    return f"{current}/{total}"


def is_valid_purchase_counter(counter: tuple[int, int, int]) -> bool:
    _, _, total = counter
    return total > 0


def normalize_config_purchase_quantity(value, maximum: int = 1) -> int:
    """
    Normalize limited-purchase config value into a bounded quantity.

    Quantity fields are expected to come from select/input values only.
    Stale bool values are treated as invalid so old configs cannot silently buy
    1 item after schema changes.
    """
    if isinstance(value, bool):
        return 0
    if value in (None, ""):
        return 0
    try:
        value = int(value)
    except (TypeError, ValueError):
        return 0
    value = max(value, 0)
    maximum = max(int(maximum), 0)
    if maximum > 0:
        value = min(value, maximum)
    return value


def resolve_ocr_lang(config) -> str:
    lang = getattr(config, "Emulator_GameLanguage", "cn")
    if lang in ("auto", "", None, "cn", "global_cn", "zh", "zh_cn"):
        return "cn"
    if lang in ("en", "global_en", "en_us"):
        return "en"
    if lang in ("jp", "ja", "ja_jp"):
        return "jp"
    if lang in ("tw", "zh_tw"):
        return "tw"
    return "cn"


def ocr_purchase_counter(image, config, preset: PurchaseCounterPreset) -> tuple[int, int, int]:
    return StorePurchaseCounterOcr(
        preset.to_button(),
        lang=resolve_ocr_lang(config),
        name=preset.name,
    ).ocr_single_line(image)


def ocr_remaining_buy_times(image, config, preset: PurchaseCounterPreset) -> int:
    return StoreRemainingPurchaseTimesOcr(
        preset.to_button(),
        lang=resolve_ocr_lang(config),
        name=preset.name,
    ).ocr_single_line(image)


def resolve_period_purchase_quantity(
    desired_total: int,
    purchase_limit: int,
    remaining_times: int,
) -> tuple[int, int]:
    purchase_limit = max(int(purchase_limit), 0)
    if purchase_limit <= 0:
        return 0, 0

    desired_total = normalize_config_purchase_quantity(
        desired_total,
        maximum=purchase_limit,
    )
    remaining_times = max(int(remaining_times), 0)
    remaining_times = min(remaining_times, purchase_limit)

    already_bought = max(purchase_limit - remaining_times, 0)
    need_to_buy = max(desired_total - already_bought, 0)
    need_to_buy = min(need_to_buy, remaining_times)
    return already_bought, need_to_buy


def resolve_purchase_quantity(
    strategy: PurchaseQuantityStrategy,
    counter: tuple[int, int, int],
    desired_quantity: int = 1,
    fallback: int = 1,
) -> tuple[int, str]:
    fallback = max(fallback, 1)
    desired_quantity = max(desired_quantity, 1)
    if strategy == "max":
        if is_valid_purchase_counter(counter):
            _, _, total = counter
            return total, "counter"
        return fallback, "fallback"
    if strategy == "target":
        if is_valid_purchase_counter(counter):
            _, _, total = counter
            return min(desired_quantity, total), "counter"
        return min(desired_quantity, fallback), "fallback"
    return fallback, "default"


def plan_purchase_selection(
    strategy: PurchaseQuantityStrategy,
    counter: tuple[int, int, int],
    desired_quantity: int = 1,
    fallback: int = 1,
) -> PurchaseSelection:
    quantity, source = resolve_purchase_quantity(
        strategy=strategy,
        counter=counter,
        desired_quantity=desired_quantity,
        fallback=fallback,
    )
    if strategy == "once" or not is_valid_purchase_counter(counter):
        return PurchaseSelection(quantity=quantity, counter=counter, source=source)

    current, _, total = counter
    if current == quantity:
        return PurchaseSelection(quantity=quantity, counter=counter, source=source)
    if quantity <= 1:
        action: PurchaseAdjustAction = "min"
    elif quantity >= total:
        action = "max"
    elif current < quantity:
        action = "plus"
    else:
        action = "minus"
    return PurchaseSelection(
        quantity=quantity,
        action=action,
        counter=counter,
        source=source,
    )
