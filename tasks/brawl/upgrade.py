"""Verify one requested level upgrade without repeating an unconfirmed payment."""

import re
from dataclasses import dataclass
from enum import Enum

import numpy as np

from module.base.base import ModuleBase
from module.base.timer import Timer
from module.logger import logger
from module.ocr.ocr import Ocr
from tasks.brawl.assets.assets_brawl_upgrade import (
    OCR_UPGRADE_COST,
    OCR_UPGRADE_CURRENT_CAPACITY,
    OCR_UPGRADE_CURRENT_LEVEL,
    OCR_UPGRADE_NEXT_CAPACITY,
    OCR_UPGRADE_NEXT_LEVEL,
    UPGRADE_CONFIRM,
    UPGRADE_ENTRY,
)
from tasks.brawl.rules import MAX_LEVEL, team_capacity, upgrade_cost
from tasks.brawl.vision import (
    BrawlMode,
    BrawlScreen,
    BrawlVision,
    ObservationError,
    PreparationPanel,
)


@dataclass(frozen=True)
class UpgradeRequest:
    panel: PreparationPanel
    game_mode: BrawlMode
    reserve_coins: int = 0


@dataclass(frozen=True)
class UpgradeDialog:
    header: PreparationPanel
    game_mode: BrawlMode
    next_level: int
    next_capacity: int
    cost: int
    ready: bool


class UpgradeOutcome(str, Enum):
    CONFIRMED = "confirmed"
    TIMEOUT = "timeout"
    SCENE_CHANGED = "scene_changed"
    CONTEXT_CHANGED = "context_changed"
    BALANCE_MISMATCH = "balance_mismatch"


def capture_upgrade(image, reserve_coins=0):
    """Capture one affordable upgrade; a caller chooses when to spend and reserve."""
    if type(reserve_coins) is not int or reserve_coins < 0:
        raise ValueError("The coin reserve must be a nonnegative integer")
    observed = BrawlVision(image).observe()
    if (observed.state != BrawlScreen.PREPARATION or observed.panel is None
            or observed.game_mode is None):
        raise ObservationError("A stable preparation panel is required for an upgrade")
    panel = observed.panel
    if panel.level >= MAX_LEVEL:
        raise ObservationError("The brawl level is already at its maximum")
    if panel.coins < upgrade_cost(panel.level) + reserve_coins:
        raise ObservationError("The upgrade would exceed the available coin budget")
    return UpgradeRequest(panel, observed.game_mode, reserve_coins)


def parse_upgrade_fields(values):
    """Require complete source/target levels, capacities and the displayed cost."""
    if len(values) != 5 or any(not np.isfinite(score) or score < .8 for _, score in values):
        raise ObservationError("Upgrade fields are incomplete or have low confidence")
    texts = [re.sub(r"\s+", "", text).upper() for text, _ in values]
    levels = [re.fullmatch(r"LV[.．]?(MAX|[1-9])", text) for text in texts[:2]]
    if any(match is None for match in levels) or any(
            re.fullmatch(r"[0-9]{1,3}", text) is None for text in texts[2:]):
        raise ObservationError("Unrecognized upgrade field format")
    current, target = (MAX_LEVEL if match[1] == "MAX" else int(match[1]) for match in levels)
    capacity, next_capacity, cost = map(int, texts[2:])
    if (current >= MAX_LEVEL or target != current + 1 or capacity != team_capacity(current)
            or next_capacity != team_capacity(target) or cost != upgrade_cost(current)):
        raise ObservationError("Upgrade levels, capacities and price disagree with the rules")
    return current, target, capacity, next_capacity, cost


def read_upgrade_dialog(image):
    """Read the current quote; neither an enabled button nor this quote proves payment."""
    vision = BrawlVision(image)
    observed = vision.observe()
    if observed.state != BrawlScreen.LEVEL_UP or observed.game_mode is None:
        raise ObservationError("No recognized brawl upgrade dialog")
    header = vision.preparation_panel()
    areas = (OCR_UPGRADE_CURRENT_LEVEL.area, OCR_UPGRADE_NEXT_LEVEL.area,
             OCR_UPGRADE_CURRENT_CAPACITY.area, OCR_UPGRADE_NEXT_CAPACITY.area, OCR_UPGRADE_COST.area)
    crops = [image[y1:y2, x1:x2] for x1, y1, x2, y2 in areas]
    values = Ocr(OCR_UPGRADE_COST, lang="en", name="BRAWL_UPGRADE_QUOTE").ocr_multi_lines(crops)
    current, target, capacity, next_capacity, cost = parse_upgrade_fields(values)
    if header.level != current or header.capacity != capacity:
        raise ObservationError("The upgrade quote does not match its preparation header")
    ready = vision.matches(UPGRADE_CONFIRM) and UPGRADE_CONFIRM.match_color(image, threshold=20)
    return UpgradeDialog(header, observed.game_mode, target, next_capacity, cost, ready)


class BrawlUpgrade(ModuleBase):
    ACTION_INTERVAL = 3
    STEP_TIMEOUT = 12
    ACTION_TIMER = "BrawlUpgrade"

    def upgrade_once(self, request, skip_first_screenshot=True):
        """Open and confirm one captured upgrade, then verify level and exact debit.

        Pages:
            in: stable preparation matching the caller's current UpgradeRequest
            out: verified preparation after one upgrade, an interruption, or the
                final frame of the bounded, unconfirmed interaction

        The opening icon can be retried. The payment button is clicked once:
        a delayed result must never authorize another purchase. Unconfirmed
        returns require the parent to inspect the final frame and recapture its
        intent; this method neither advances a battle nor chains upgrades.
        """
        if (not isinstance(request, UpgradeRequest) or not isinstance(request.panel, PreparationPanel)
                or not isinstance(request.game_mode, BrawlMode)
                or type(request.reserve_coins) is not int or request.reserve_coins < 0
                or not 1 <= request.panel.level < MAX_LEVEL
                or request.panel.capacity != team_capacity(request.panel.level)
                or not 0 <= request.panel.deployed <= request.panel.capacity
                or request.panel.coins < upgrade_cost(request.panel.level) + request.reserve_coins):
            raise ValueError("A captured, affordable upgrade request is required")
        before = request.panel
        cost = upgrade_cost(before.level)
        expected_coins = before.coins - cost
        timeout = Timer(self.STEP_TIMEOUT).start()
        opened = dialog_seen = paid = returned = False
        while True:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()
            vision = BrawlVision(self.device.image)
            observed = vision.observe()
            if observed.game_mode is not None and observed.game_mode != request.game_mode:
                return UpgradeOutcome.CONTEXT_CHANGED
            if observed.state not in (BrawlScreen.PREPARATION, BrawlScreen.LEVEL_UP, BrawlScreen.UNKNOWN):
                return UpgradeOutcome.SCENE_CHANGED

            # This boundary is latched before reading animated resource digits.
            # Once preparation reappears, any later dialog belongs to another
            # action. Never reopen or confirm it to chase a delayed balance.
            if observed.state == BrawlScreen.PREPARATION:
                if paid:
                    returned = True
                elif dialog_seen:
                    return UpgradeOutcome.SCENE_CHANGED
                panel = observed.panel
                if panel is not None:
                    if paid:
                        if ((panel.round_number, panel.deployed, panel.synergy_tokens)
                                != (before.round_number, before.deployed, before.synergy_tokens)
                                or panel.level not in (before.level, before.level + 1)):
                            return UpgradeOutcome.CONTEXT_CHANGED
                        if not expected_coins <= panel.coins <= before.coins:
                            return UpgradeOutcome.BALANCE_MISMATCH
                        if (observed.game_mode == request.game_mode and panel.level == before.level + 1
                                and panel.capacity == team_capacity(before.level + 1)
                                and panel.coins == expected_coins):
                            logger.info(f"Brawl upgrade verified: level {panel.level}, spent {cost} coins")
                            return UpgradeOutcome.CONFIRMED
                    elif panel != before:
                        return UpgradeOutcome.CONTEXT_CHANGED
            elif observed.state == BrawlScreen.LEVEL_UP:
                if not opened or returned:
                    return UpgradeOutcome.SCENE_CHANGED
                if not dialog_seen:
                    if timeout.reached():
                        return UpgradeOutcome.TIMEOUT
                    dialog_seen = True
                    timeout.reset()
                try:
                    dialog = read_upgrade_dialog(self.device.image)
                except ObservationError:
                    dialog = None
                if dialog is not None and (dialog.header != before or dialog.game_mode != request.game_mode):
                    return UpgradeOutcome.CONTEXT_CHANGED
            else:
                dialog = None

            # Positive success above has priority even on a slow final frame.
            # A slow unread frame must not authorize a new action past deadline.
            if timeout.reached():
                logger.warning("Brawl upgrade remains unconfirmed after local timeout")
                return UpgradeOutcome.TIMEOUT
            if paid or observed.game_mode != request.game_mode:
                continue
            if not self.interval_is_reached(self.ACTION_TIMER, interval=self.ACTION_INTERVAL):
                continue
            if observed.state == BrawlScreen.PREPARATION and observed.panel == before:
                if vision.matches(UPGRADE_ENTRY):
                    self.device.click(UPGRADE_ENTRY)
                    self.interval_reset(self.ACTION_TIMER, interval=self.ACTION_INTERVAL)
                    opened = True
            elif observed.state == BrawlScreen.LEVEL_UP and dialog is not None and dialog.ready:
                self.device.click(UPGRADE_CONFIRM)
                self.interval_reset(self.ACTION_TIMER, interval=self.ACTION_INTERVAL)
                # Do not clear this latch on duplicate dialogs, unknown frames,
                # stale balances or level animations. Those observations cannot
                # establish that the previous payment failed; retrying can buy
                # another level after a missed close/reopen transition.
                paid = True
                timeout.reset()
