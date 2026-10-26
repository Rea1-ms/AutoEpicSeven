"""Preview named bench removals, then cancel or verify their exact proceeds."""

import hashlib
import re
from dataclasses import dataclass

import cv2
import numpy as np

from module.base.base import ModuleBase
from module.base.button import ClickButton
from module.base.timer import Timer
from module.logger import logger
from tasks.brawl.assets.assets_brawl import (
    HERO_RESULT_CHECK,
    TRANSMIT_CHECK,
    TRANSMIT_CONFIRM,
)
from tasks.brawl.assets.assets_brawl_transfer import OCR_TRANSFER_CARDS, TRANSMIT_ENTER
from tasks.brawl.heroes import HeroResult, read_hero_result
from tasks.brawl.name_cache import read_name_lines, remember_name
from tasks.brawl.observation_cache import invalidate_panel_observation_cache
from tasks.brawl.rules import (
    Imprint,
    Resources,
    RuleInputError,
    get_hero,
    validate_imprint,
)
from tasks.brawl.scanner import BenchScanResult
from tasks.brawl.summon import _result_balances
from tasks.brawl.transmit import SELECTION_AREA, read_transmit_panel
from tasks.brawl.vision import (
    BrawlMode,
    BrawlScreen,
    BrawlVision,
    ObservationError,
    PreparationPanel,
)


@dataclass(frozen=True)
class TransferPreview:
    header: PreparationPanel
    game_mode: BrawlMode
    names: tuple[str, ...]
    quote: Resources
    evidence: str


@dataclass(frozen=True)
class TransferReport:
    panel: PreparationPanel
    game_mode: BrawlMode
    names: tuple[str, ...]
    quote: Resources
    hero_results: tuple[HeroResult, ...]
    committed: bool


def read_transfer_cards(image):
    """Return complete, current names and their own clickable text rectangles."""
    if BrawlVision(image).observe().state != BrawlScreen.TRANSMIT_SELECTION:
        raise ObservationError("Transfer cards require the selection scene")
    entries = {}
    for line in read_name_lines(image, OCR_TRANSFER_CARDS, font="transfer", log_name="BRAWL_TRANSFER_NAMES"):
        x1, y1, x2, y2 = map(int, line.box)
        # The faded row at the upper edge is still a selectable card, but only
        # its complete name is useful. Clipped names, badges and footer text
        # cannot turn into targets, even if OCR happens to produce a hero name.
        if (not np.isfinite(line.score) or line.score < .8
                or not (995 <= x1 < x2 <= 1210 and 126 <= y1 < y2 <= 530 and 14 <= y2 - y1 <= 35)):
            continue
        try:
            name = get_hero(re.sub(r"\s+", "", line.ocr_text)).name
        except RuleInputError:
            continue
        if name in entries:
            raise ObservationError("Transfer card names are ambiguous")
        entries[name] = (x1, y1, x2, y2)
        remember_name(line, name, font="transfer")
    return entries


def read_transfer_icons(image):
    """Read the circular tray portraits, independently of the moving bench.

    The tray uses fixed-size circular portraits. Their centers are detected
    from the photographed ring, rather than assuming a fourth/fifth grid slot.
    Each inner portrait digest must survive the next selection unchanged. A
    missed ring prevents progress; it never authorizes a smaller selected set.
    """
    x1, y1, x2, y2 = SELECTION_AREA
    region = image[y1:y2, x1:x2]
    circles = cv2.HoughCircles(cv2.cvtColor(region, cv2.COLOR_RGB2GRAY), cv2.HOUGH_GRADIENT,
                               dp=1, minDist=65, param1=100, param2=30, minRadius=32, maxRadius=38)
    if circles is None:
        return ()
    icons = []
    for x, y, _ in circles[0]:
        cx, cy = round(float(x)) + x1, round(float(y)) + y1
        if not (x1 + 25 <= cx <= x2 - 25 and y1 + 25 <= cy <= y2 - 25):
            raise ObservationError("A transfer portrait is clipped")
        digest = hashlib.sha256(image[cy - 24:cy + 24, cx - 24:cx + 24].tobytes()).hexdigest()
        icons.append((cx, cy, digest))
    return tuple(sorted(icons))


class BrawlAutoTransfer(ModuleBase):
    ACTION_TIMER = "BrawlBenchScan"
    ACTION_INTERVAL = 3
    STEP_TIMEOUT = 18
    MAX_SWIPES = 10

    def _ready(self):
        return self.interval_is_reached(self.ACTION_TIMER, interval=self.ACTION_INTERVAL)

    def _click(self, button):
        self.device.click(button)
        self.interval_reset(self.ACTION_TIMER, interval=self.ACTION_INTERVAL)

    def preview(self, names, scan, skip_first_screenshot=True):
        """Select only named candidates and return the game's actual quote.

        Pages:
            in: preparation matching a complete bench scan
            out: verified selection tray for exactly the requested names

        Selection immediately removes cards from the right bench. Therefore
        this loop never uses the preparation scanner or its cached coordinates.
        Each click is latched until a new portrait appears, all previous tray
        portraits remain identical, and the clicked name leaves the bench.
        """
        if (not isinstance(scan, BenchScanResult) or not scan.coverage_complete
                or scan.panel is None or scan.game_mode is None or not isinstance(names, tuple)
                or not 1 <= len(names) <= 5 or len(set(names)) != len(names)):
            raise ObservationError("Transfer needs one to five names and a complete bench scan")
        known = {hero.name: hero for hero in scan.heroes}
        if len(known) != len(scan.heroes) or any(name not in known for name in names):
            raise ObservationError("Transfer candidates are not unique members of the scanned bench")
        try:
            for hero in scan.heroes:
                if get_hero(hero.name).name != hero.name or hero.imprint is None:
                    raise ObservationError("Transfer bench identity is incomplete")
                validate_imprint(hero.name, hero.imprint)
        except RuleInputError as exc:
            raise ObservationError("Transfer bench identity is invalid") from exc
        self._pending_preview = None
        timeout = Timer(self.STEP_TIMEOUT).start()
        entered = False
        selected = []
        icons = ()
        pending = None
        previous_cards = None
        swipes = 0
        quote = Resources(0, 0)
        while True:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()
            vision = BrawlVision(self.device.image)
            observation = vision.observe()
            if observation.game_mode is not None and observation.game_mode != scan.game_mode:
                raise ObservationError("Transfer mode changed")
            if observation.state == BrawlScreen.PREPARATION:
                if observation.panel is not None and observation.panel != scan.panel:
                    raise ObservationError("Transfer preparation changed after its scan")
                if (not entered and observation.panel == scan.panel and observation.game_mode == scan.game_mode
                        and vision.matches(TRANSMIT_ENTER) and self._ready()):
                    self._click(TRANSMIT_ENTER)
                    entered = True
                    timeout.reset()
                    continue
            elif observation.state == BrawlScreen.TRANSMIT_SELECTION:
                if not entered:
                    raise ObservationError("An existing tray is not owned by this transfer preview")
                panel = read_transmit_panel(self.device.image)
                if panel.header != scan.panel or panel.game_mode != scan.game_mode:
                    raise ObservationError("Transfer selection context changed")
                current_icons = read_transfer_icons(self.device.image)
                cards = read_transfer_cards(self.device.image)
                if any(name not in known for name in cards):
                    raise ObservationError("Transfer bench contains a hero absent from the complete scan")
                if any(name in cards for name in selected):
                    raise ObservationError("A selected transfer hero reappeared on the bench")
                if pending is not None:
                    if (len(current_icons) == len(icons) + 1 and set(icons).issubset(current_icons)
                            and pending not in cards and panel.selected
                            and panel.quote.coins >= quote.coins and panel.quote.tokens >= quote.tokens
                            and panel.quote != quote):
                        selected.append(pending)
                        pending = None
                        icons = current_icons
                        quote = panel.quote
                        previous_cards = None
                        timeout.reset()
                    elif current_icons != icons or panel.quote != quote:
                        raise ObservationError("Transfer click did not produce exactly one verified removal")
                elif current_icons != icons or panel.quote != quote or panel.selected != bool(selected):
                    raise ObservationError("Transfer tray changed without a verified selection")
                if pending is None and tuple(selected) == names:
                    result = TransferPreview(panel.header, panel.game_mode, names, panel.quote,
                                             panel.selection_fingerprint)
                    self._pending_preview = result
                    logger.info(f"Brawl transfer quote: heroes={names}, coins={panel.quote.coins}, "
                                f"tokens={panel.quote.tokens}")
                    return result
                if pending is None and self._ready():
                    target = names[len(selected)]
                    area = cards.get(target)
                    # Two independent frames must agree before a moving card
                    # becomes clickable. After every swipe or selection the
                    # stability evidence is reset, never inherited by a new row.
                    if area is not None and previous_cards == cards:
                        self._click(ClickButton(area, name="BRAWL_TRANSFER_SELECT"))
                        pending = target
                        previous_cards = None
                        timeout.reset()
                        continue
                    if area is None and previous_cards == cards:
                        if swipes >= self.MAX_SWIPES:
                            raise ObservationError("Transfer candidate was not found within the bounded search")
                        start, end = ((1150, 205), (1150, 505)) if swipes < 3 else ((1150, 505), (1150, 205))
                        self.device.swipe(start, end, name="BRAWL_TRANSFER_SCROLL")
                        self.interval_reset(self.ACTION_TIMER, interval=self.ACTION_INTERVAL)
                        swipes += 1
                        previous_cards = None
                        timeout.reset()
                        continue
                previous_cards = cards
            elif observation.state != BrawlScreen.UNKNOWN:
                raise ObservationError("Transfer preview was interrupted")
            if timeout.reached():
                raise ObservationError("Transfer preview did not reach a verified selection")

    def finish(self, preview, *, commit=False, skip_first_screenshot=True):
        """Cancel the quote or confirm it once and collect attributable gifts.

        Pages:
            in: unchanged selection returned by this instance's preview
            out: preparation with unchanged balances or exact quoted proceeds

        Cancellation still requires a fresh complete bench scan by the parent.
        A gift is attributable only after this call's confirm and its exact
        proceeds. Repeat frames never produce repeated sales or duplicate gifts.
        """
        if not isinstance(preview, TransferPreview) or getattr(self, "_pending_preview", None) is not preview:
            raise ObservationError("Transfer finish requires this instance's current preview")
        if type(commit) is not bool:
            raise ValueError("Transfer commit must be a boolean")
        # Consume the authorization before any action, including an eventual
        # timeout. Retrying this object must not send a second irreversible
        # confirmation while the first request may still be pending in game.
        self._pending_preview = None
        before = preview.header
        expected = (before.coins + (preview.quote.coins if commit else 0),
                    before.synergy_tokens + (preview.quote.tokens if commit else 0))
        timeout = Timer(self.STEP_TIMEOUT).start()
        clicked = returned = result_seen = pending_closed = False
        gifts = []
        pending = None
        while True:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()
            vision = BrawlVision(self.device.image)
            current = vision.observe()
            if current.game_mode is not None and current.game_mode != preview.game_mode:
                raise ObservationError("Transfer finish mode changed")
            if current.state == BrawlScreen.PREPARATION:
                if not clicked:
                    raise ObservationError("Transfer tray disappeared before its requested action")
                returned = True
                if result_seen and not pending_closed:
                    raise ObservationError("A transfer gift disappeared before its verified close")
                panel = current.panel
                if panel is not None:
                    if (panel.round_number, panel.level, panel.deployed, panel.capacity) != (
                            before.round_number, before.level, before.deployed, before.capacity):
                        raise ObservationError("Transfer return context changed")
                    balances = (panel.coins, panel.synergy_tokens)
                    if balances == expected and current.game_mode == preview.game_mode:
                        self._pending_preview = None
                        logger.info(f"Brawl transfer {'confirmed' if commit else 'cancelled'}: "
                                    f"heroes={preview.names}, balances={balances}, gifts={len(gifts)}")
                        return TransferReport(panel, preview.game_mode, preview.names, preview.quote,
                                              tuple(gifts), commit)
                    if not (before.coins <= panel.coins <= expected[0]
                            and before.synergy_tokens <= panel.synergy_tokens <= expected[1]):
                        raise ObservationError("Transfer proceeds differ from the displayed quote")
            elif current.state == BrawlScreen.TRANSMIT_SELECTION:
                if returned:
                    raise ObservationError("Transfer tray reopened after returning to preparation")
                panel = read_transmit_panel(self.device.image)
                if (panel.header != before or panel.game_mode != preview.game_mode
                        or panel.quote != preview.quote or panel.selection_fingerprint != preview.evidence):
                    raise ObservationError("Transfer selection changed after its preview")
                if not clicked and self._ready():
                    self._click(TRANSMIT_CONFIRM if commit else TRANSMIT_CHECK)
                    if commit:
                        invalidate_panel_observation_cache()
                    clicked = True
                    timeout.reset()
                    continue
            elif current.state == BrawlScreen.HERO_RESULT:
                if not clicked or not commit or returned:
                    raise ObservationError("Hero result is not attributable to this transfer")
                result_seen = True
                hero = read_hero_result(self.device.image)
                if hero is not None:
                    if hero.imprint == hero.previous_imprint == Imprint.SSS:
                        raise ObservationError("Transfer gift overflow requires separate proceeds evidence")
                    if hero != pending:
                        if hero in gifts:
                            raise ObservationError("A repeated transfer gift cannot be counted twice")
                        if pending is not None and not pending_closed:
                            raise ObservationError("A transfer gift changed before its verified close")
                        previous = next((item for item in reversed(gifts) if item.name == hero.name), None)
                        if previous is not None and hero.previous_imprint != previous.imprint:
                            raise ObservationError("Transfer gifts do not form a consistent imprint chain")
                        pending, pending_closed = hero, False
                        timeout.reset()
                    balances = _result_balances(vision)
                    if balances != expected:
                        raise ObservationError("Transfer gift balances do not match the quoted proceeds")
                    if not pending_closed and vision.matches(HERO_RESULT_CHECK) and self._ready():
                        self._click(HERO_RESULT_CHECK)
                        gifts.append(hero)
                        pending_closed = True
                        timeout.reset()
                        continue
            elif current.state != BrawlScreen.UNKNOWN:
                raise ObservationError("Transfer completion was interrupted")
            if timeout.reached():
                raise ObservationError("Transfer completion was not verified")
