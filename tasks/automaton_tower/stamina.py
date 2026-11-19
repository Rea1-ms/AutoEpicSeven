"""Read tower stamina and verify the leaf-only refill dialog."""

from __future__ import annotations

import re
from dataclasses import dataclass

import cv2
import numpy as np

from module.base.button import ClickButton
from module.ocr.ocr import Ocr

LEAF_STAMINA = 80
REFILL_SELECT_LEAF = ClickButton((412, 342, 455, 371), name="TOWER_REFILL_SELECT_LEAF")
REFILL_BUY = ClickButton((710, 535, 786, 565), name="TOWER_REFILL_BUY")
FLOOR_STAMINA = ClickButton((976, 645, 1069, 684), name="TowerFloorStamina")
LEAF_COST = ClickButton((422, 389, 442, 407), name="TowerRefillLeafCost")
REFILL_AMOUNT = ClickButton((545, 385, 580, 411), name="TowerRefillAmount")
LEAF_BALANCE = ClickButton((403, 411, 588, 434), name="TowerRefillLeafBalance")


@dataclass(frozen=True)
class FloorStamina:
    cost: int
    current: int

    @classmethod
    def from_text(cls, text: str) -> FloorStamina | None:
        match = re.fullmatch(r"([1-9][0-9]{0,2})\s*/\s*([0-9]{1,5})", text.strip())
        if match is None:
            return None
        return cls(int(match[1]), int(match[2]))


class TowerStaminaOcr(Ocr):
    def pre_process(self, image):
        if self.button is LEAF_BALANCE:
            visible = image[:, :, 1] >= 130
        elif self.button is FLOOR_STAMINA:
            red, green, blue = np.moveaxis(image.astype(np.int16), -1, 0)
            visible = (red >= 155) & (green >= 120) & (red - blue >= 60)
        else:
            visible = image.min(axis=2) >= 178
        mask = visible.astype(np.uint8) * 255
        enlarged = cv2.resize(mask, None, fx=3, fy=3, interpolation=cv2.INTER_NEAREST)
        return cv2.cvtColor(enlarged, cv2.COLOR_GRAY2RGB)


def _edge_glow(edge: np.ndarray) -> bool | None:
    red, green, blue = np.moveaxis(edge, -1, 0)
    gold = (red >= 180) & (green >= 170) & (blue >= 80) & (red > blue)
    if np.any(gold.mean(axis=0) >= 0.8):
        return True
    if red.max() < 100:
        return False
    return None


def refill_selection(image: np.ndarray) -> str | None:
    left = _edge_glow(image[328:418, 378:384])
    right = _edge_glow(image[328:418, 896:903])
    if left is True and right is False:
        return "leaf"
    if right is True and left is False:
        return "skystone"
    return None


def read_leaf_balance(image: np.ndarray, lang: str) -> int | None:
    text = TowerStaminaOcr(LEAF_BALANCE, lang=lang).ocr_single_line(image)
    match = re.fullmatch(r"持有\s*[:：]\s*([0-9]{1,6})", text.strip())
    return int(match[1]) if match is not None else None
