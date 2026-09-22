"""Buy only Mystic Medals from the timed Huche shop.

Reuse the regular store's quantity planner and action assets, and the shared
resource-bar reader. Unlike a refresh-farming shop, this task never refreshes
stock itself. Completion requires verified stock or a fully inspected rotating
batch ending at the regular-item boundary, never a missing item match alone.
"""

from dataclasses import dataclass
import re

import module.config.server as server
from module.base.button import ClickButton
from module.base.timer import Timer
from module.base.utils import area_offset
from module.game_info.catalog import aware_time, load_info
from module.logger import logger
from module.ocr.ocr import Ocr
from tasks.activity.assets.assets_activity_huche_shop_26_9_17 import (
    HUCHE_BUY_CANCEL,
    HUCHE_BUY_CURRENCY,
    HUCHE_BUY_MYSTIC_CHECK,
    HUCHE_BUY_POPUP_CHECK,
    HUCHE_ITEMS_AREA,
    HUCHE_ITEM_SOLD_OUT,
    HUCHE_MYSTIC_BUY,
    HUCHE_MYSTIC_ITEM,
    HUCHE_PRICE_CURRENCY,
    HUCHE_REFRESH_ITEM,
    HUCHE_REGULAR_ITEM_CHECK,
    HUCHE_SHOP_CHECK,
    OCR_HUCHE_BUY_AMOUNT,
    OCR_HUCHE_BUY_PRICE,
    OCR_HUCHE_PRICE,
    OCR_HUCHE_REFRESH_NAME,
    OCR_HUCHE_STOCK,
)
from tasks.activity.calendar import active_activities
from tasks.activity.scheduling import (
    is_activity_checked_in_window, is_activity_checked_since, mark_activity_checked,
)
from tasks.base.resource_bar import RESOURCE_BAR_LAYOUT_SECRET_SHOP, ResourceBarMixin
from tasks.base.ui import UI
from tasks.store.assets.assets_store_actions import (
    BUY_CONFIRM_MULTI, BUY_CONFIRM_SINGLE, BUY_MAX, BUY_MIN,
    BUY_TIMES_MINUS, BUY_TIMES_PLUS, OCR_BUY_TIMES,
)
from tasks.store.purchase import (
    PurchaseCounterPreset, ocr_purchase_counter, parse_purchase_counter_text,
    plan_purchase_selection, resolve_ocr_lang,
)


@dataclass(frozen=True)
class MysticOffer:
    row: tuple[int, int, int, int]
    price: int
    remaining: int
    limit: int
    sold_out: bool


@dataclass(frozen=True)
class DiscountItem:
    y: int
    name: str
    mystic: bool


@dataclass(frozen=True)
class DiscountView:
    items: tuple[DiscountItem, ...]
    boundary: tuple[int, int, int, int] | None


class DiscountBatchScan:
    """Track continuous coverage from the top to a positive regular label.

    The stable first-row position identifies the entry viewport at the top.
    Do not drag it down to probe the top: E7 overscrolls and bounces, making a
    correct entry look unstable. Moving toward later rows keeps an overlapping
    item; losing it means coverage is unknown, not that the batch has ended.
    Observations are invalidated after every drag and unreadable frame. A lost
    tail requests a bounded backward search; waiting on the same settled view
    cannot restore an item that inertia has already moved off screen.
    """

    def __init__(self):
        self.previous = None
        self.top = False
        self.tail = None
        self.scrolls = 0
        self.recoveries = 0

    def observe(self, view):
        stable = view is not None and view == self.previous
        self.previous = view
        if not stable:
            return "wait"
        if not self.top:
            # The first clock sits at the top row; a regular-only inventory
            # instead starts with its explicit label. Only a viewport that
            # does not show this entry alignment needs to return to the top.
            aligned = (bool(view.items) and 79 <= view.items[0].y <= 91
                       or not view.items and view.boundary is not None and 150 <= view.boundary[1] <= 195)
            if not aligned:
                return "up"
            self.top = True
        if self.tail is not None:
            overlaps = [item for item in view.items
                        if (item.name, item.mystic) == (self.tail.name, self.tail.mystic)]
            if not overlaps:
                return "recover"
            if min(item.y for item in overlaps) >= self.tail.y - 20:
                return "down"
            self.tail = None
            self.recoveries = 0
        if any(item.mystic for item in view.items):
            return "target"
        if view.boundary is not None:
            return "complete"
        return "down"

    def scrolled(self, view, direction):
        # Keep the last verified tail throughout recovery and failed forward
        # gestures. Replacing it with an unconnected viewport would silently
        # discard unseen rows and let a visible boundary finish an incomplete
        # scan. Reset the recovery budget only after overlap proves progress.
        if direction == "down" and self.tail is None:
            self.tail = view.items[-1]
        elif direction == "recover":
            self.recoveries += 1
        self.previous = None
        self.scrolls += 1


class HucheShop(ResourceBarMixin, UI):
    PURCHASE_TIMEOUT_SECONDS = 120
    SCROLL_UP_LIMIT = 3
    SCROLL_DOWN_LIMIT = 10
    BATCH_NAME_MIN_SCORE = 0.70
    BATCH_UNVERIFIED_LIMIT = 6
    BATCH_RECOVERY_LIMIT = 3
    BATCH_DRAG_STEP = 145

    def __init__(self, config, device=None, task=None, activity_id="huche_shop_2026_09_17"):
        super().__init__(config=config, device=device, task=task)
        self.activity_id = activity_id

    def _ocr(self, asset, offset=(0, 0)) -> str:
        button = ClickButton(area_offset(asset.area, offset), name=asset.name)
        return Ocr(button, lang=resolve_ocr_lang(self.config)).ocr_single_line(self.device.image)

    def _number(self, asset, offset=(0, 0)) -> int | None:
        text = self._ocr(asset, offset).strip().replace(",", "").replace("，", "")
        return int(text) if re.fullmatch(r"[0-9]+", text) else None

    def _match_at(self, asset, offset=(0, 0), color=True) -> bool:
        # Assets are shared globals. Limit each match to this row, then restore
        # the search/offset even on failure; another row must not inherit it.
        buttons = tuple(asset.iter_buttons())
        searches = tuple(button.search for button in buttons)
        try:
            asset.load_search(area_offset(asset.search, offset))
            if color:
                return self.match_template_color(asset)
            return self.match_template_luma(asset)
        finally:
            for button, search in zip(buttons, searches):
                button.load_search(search)
                button.clear_offset()

    def _locate(self, asset, area):
        buttons = tuple(asset.iter_buttons())
        searches = tuple(button.search for button in buttons)
        try:
            asset.load_search(area)
            if self.match_template_color(asset):
                return tuple(asset.button_offset)
            return None
        finally:
            for button, search in zip(buttons, searches):
                button.load_search(search)
                button.clear_offset()

    def handle_purchase_cancel(self) -> bool:
        offset = self._locate(HUCHE_BUY_CANCEL, (350, 425, 580, 635))
        if offset is not None and self.interval_is_reached(HUCHE_BUY_CANCEL, interval=2):
            self.device.click(ClickButton(area_offset(HUCHE_BUY_CANCEL.area, offset), name="HucheBuyCancel"))
            self.interval_reset(HUCHE_BUY_CANCEL, interval=2)
            return True
        return False

    def find_offers(self) -> list[MysticOffer]:
        buttons = tuple(HUCHE_MYSTIC_ITEM.iter_buttons())
        searches = tuple(button.search for button in buttons)
        try:
            HUCHE_MYSTIC_ITEM.load_search(HUCHE_ITEMS_AREA.area)
            rows = HUCHE_MYSTIC_ITEM.match_multi_template(self.device.image)
        finally:
            for button, search in zip(buttons, searches):
                button.load_search(search)
                button.clear_offset()
        offers = []
        for row in sorted(rows, key=lambda item: item.area[1]):
            offset = (0, row.area[1] - HUCHE_MYSTIC_ITEM.area[1])
            # Prices and buttons belong to the same visual row. This also
            # distinguishes the 160/2 and 200/4 offers when both are sold out
            # at the bottom; a different item's 0/x can never finish this one.
            if abs(row.area[0] - HUCHE_MYSTIC_ITEM.area[0]) > 5:
                continue
            bounds = area_offset(OCR_HUCHE_STOCK.area, offset)
            if bounds[1] < HUCHE_ITEMS_AREA.area[1] or bounds[3] > HUCHE_ITEMS_AREA.area[3]:
                continue
            price = self._number(OCR_HUCHE_PRICE, offset)
            remaining, _, limit = parse_purchase_counter_text(self._ocr(OCR_HUCHE_STOCK, offset))
            if price not in self.price_limits or limit != self.price_limits[price]:
                continue
            if not self._match_at(HUCHE_PRICE_CURRENCY, offset, color=False):
                continue
            sold_out = remaining == 0 and self._match_at(HUCHE_ITEM_SOLD_OUT, offset, color=False)
            offers.append(MysticOffer(tuple(row.area), price, remaining, limit, sold_out))
        return offers

    def read_balance(self) -> int | None:
        result = self.inspect_resource_bar_status(RESOURCE_BAR_LAYOUT_SECRET_SHOP, "HucheShop")
        return result.final["skystone"].value if result.final is not None else None

    def read_discount_view(self) -> DiscountView | None:
        """Read clock-marked rows before the first explicit regular label.

        Some rotating products open a catalogue instead of a purchase popup,
        so BUY buttons cannot enumerate this batch. The clock anchors every
        rotating row, including those catalogue entries. Low-confidence text
        or a gap between rows invalidates the view instead of skipping stock.
        """
        matches = []
        for asset, area in ((HUCHE_REFRESH_ITEM, (545, 65, 590, 720)),
                            (HUCHE_REGULAR_ITEM_CHECK, (625, 80, 760, 720))):
            buttons = tuple(asset.iter_buttons())
            searches = tuple(button.search for button in buttons)
            try:
                asset.load_search(area)
                matches.append(sorted(asset.match_multi_template(self.device.image), key=lambda row: row.area[1]))
            finally:
                for button, search in zip(buttons, searches):
                    button.load_search(search)
                    button.clear_offset()
        clocks, regular = matches
        boundary = tuple(regular[0].area) if regular else None
        clocks = [row for row in clocks if boundary is None or row.area[1] < boundary[1] - 100]
        rows = []
        for clock in clocks:
            offset = (0, clock.area[1] - HUCHE_REFRESH_ITEM.area[1])
            area = area_offset(OCR_HUCHE_REFRESH_NAME.area, offset)
            if area[3] > HUCHE_ITEMS_AREA.area[3]:
                continue
            rows.append((clock, offset, area))
        if not rows:
            if boundary is not None:
                return DiscountView((), boundary)
            logger.attr("HucheBatchUnreadable", "no complete clock rows or regular-item boundary")
            return None
        if any(not 140 <= right[0].area[1] - left[0].area[1] <= 151
               for left, right in zip(rows, rows[1:])):
            logger.attr("HucheBatchUnreadable", f"clock row gap: {[row[0].area[1] for row in rows]}")
            return None
        if boundary is not None and not 215 <= boundary[1] - rows[-1][0].area[1] <= 240:
            logger.attr("HucheBatchUnreadable", "last clock row does not adjoin the regular-item boundary")
            return None
        items = []
        for clock, offset, area in rows:
            # Names may wrap onto two lines (summon selection boxes). Run
            # text detection inside the row instead of squeezing both lines
            # through single-line OCR, which can hide a Mystic Medal name.
            results = Ocr(ClickButton(area, name="HucheRefreshName"),
                          lang=resolve_ocr_lang(self.config)).detect_and_ocr(self.device.image)
            name = "".join(result.ocr_text for result in results)
            mystic = ("神秘" in name or "奖牌" in name
                      or self._match_at(HUCHE_MYSTIC_ITEM, offset, color=False))
            # Names establish navigation/overlap, never permission to buy.
            # The reported Leif screenshot reads the correct name at 0.785;
            # imposing purchase-level confidence here freezes every scan on
            # that harmless row. Keep a lower floor for navigation, but always
            # retain a possible Mystic match regardless of its OCR score. The
            # separate offer and popup checks still authorize every purchase.
            scores = [(result.ocr_text, round(float(result.score), 3)) for result in results]
            if not results or any(result.score < 0.9 for result in results):
                logger.attr("HucheRefreshNameScores", f"y={clock.area[1]}: {scores}")
            if not mystic and (len(name.strip()) < 2
                               or any(result.score < self.BATCH_NAME_MIN_SCORE for result in results)):
                logger.attr("HucheBatchUnreadable", f"name not verified at y={clock.area[1]}")
                return None
            items.append(DiscountItem(clock.area[1], name, mystic))
        return DiscountView(tuple(items), boundary)

    def popup_selection(self, offer, balance):
        """Verify the selected item, amount, currency, quantity and total cost."""
        offset = self._locate(HUCHE_BUY_MYSTIC_CHECK, (500, 180, 950, 425))
        if offset is None:
            return None
        if self._number(OCR_HUCHE_BUY_AMOUNT, offset) != self.medals_per_item:
            return None
        # Reuse the store's two positive popup layouts. A missing/invalid
        # counter is never interpreted as one item; only the known single
        # confirm layout and an observed remaining stock of one allow that.
        if self.match_template_color(BUY_CONFIRM_MULTI):
            confirm = BUY_CONFIRM_MULTI
            counter = ocr_purchase_counter(self.device.image, self.config,
                                           PurchaseCounterPreset("HucheBuyTimes", OCR_BUY_TIMES.area))
        elif offer.remaining == 1 and self.match_template_color(BUY_CONFIRM_SINGLE):
            confirm = BUY_CONFIRM_SINGLE
            counter = (1, 0, 1)
        else:
            return None
        price_offset = (confirm.area[0] - BUY_CONFIRM_MULTI.area[0],
                        confirm.area[1] - BUY_CONFIRM_MULTI.area[1])
        if not self._match_at(HUCHE_BUY_CURRENCY, price_offset):
            return None
        selected, _, total = counter
        if not 1 <= selected <= total <= offer.remaining:
            return None
        target = min(offer.remaining, balance // offer.price, total)
        if target <= 0:
            return None
        selection = plan_purchase_selection("target", counter, desired_quantity=target)
        price = self._number(OCR_HUCHE_BUY_PRICE, price_offset)
        if price != selected * offer.price or price > balance:
            return None
        return selection, confirm

    def run_purchase(self, window, skip_first_screenshot=True) -> bool:
        """Inspect both offers, purchase available medals, then verify stock.

        Pages:
            in: page_huche_shop
            out: page_huche_shop on success; current page on timeout
        """
        timeout = Timer(self.PURCHASE_TIMEOUT_SECONDS, count=180).start()
        refresh_at = window.refresh_start(aware_time())
        regular_id = f"{self.activity_id}_regular_mystic"
        # The normal-price offer has one fixed quota for the whole campaign.
        # Only a verified sold-out row persists this record. Insufficient funds
        # skip the current check but must leave the fixed quota retryable later.
        checked_prices = ({self.regular_price} if is_activity_checked_since(
            self.config, regular_id, window.start, now=aware_time()
        ) else set())
        previous = None
        scrolls = 0
        pending = None
        balance = None
        confirmed = False
        cancel_requested = False
        invalid_popup_frames = 0
        batch_scan = DiscountBatchScan()
        batch_unverified_frames = 0

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            shop_ready = self.match_template_color(HUCHE_SHOP_CHECK)
            popup = self._locate(HUCHE_BUY_POPUP_CHECK, (560, 95, 750, 245)) is not None
            now = aware_time()
            same_period = window.contains(now) and window.refresh_start(now) == refresh_at
            # Do not carry stock observations across a refresh or expiry. In
            # particular, an old 0/2 must never mark the next cycle complete.
            if shop_ready and (cancel_requested or not same_period):
                return False
            offers = self.find_offers() if shop_ready and not popup else []
            signature = tuple((item.row, item.price, item.remaining, item.sold_out) for item in offers)
            stable = bool(signature) and signature == previous
            previous = signature
            if stable:
                for offer in offers:
                    if offer.sold_out:
                        if offer.price == self.regular_price and offer.price not in checked_prices:
                            mark_activity_checked(self.config, regular_id)
                        checked_prices.add(offer.price)
                if pending is not None and confirmed:
                    # A purchase click is not success. Wait for the relevant
                    # stock to decrease before allowing another list click.
                    if any(item.price == pending.price and item.remaining < pending.remaining for item in offers):
                        pending = None
                        confirmed = False
                if checked_prices == set(self.price_limits):
                    mark_activity_checked(self.config, self.activity_id)
                    logger.info("HucheShop: both Mystic Medal offers checked for this refresh")
                    return True
            if timeout.reached():
                logger.warning("HucheShop: purchase/stock verification timed out")
                return False

            if popup:
                if not same_period or pending is None or cancel_requested:
                    if self.handle_purchase_cancel():
                        cancel_requested = True
                    continue
                selection = self.popup_selection(pending, balance)
                if selection is not None:
                    invalid_popup_frames = 0
                    selection, confirm = selection
                    actions = {"max": BUY_MAX, "min": BUY_MIN,
                               "plus": BUY_TIMES_PLUS, "minus": BUY_TIMES_MINUS}
                    if selection.action != "none":
                        if self.appear_then_click(actions[selection.action], interval=2):
                            previous = None
                        continue
                    if self.appear_then_click(confirm, interval=2):
                        confirmed = True
                        previous = None
                    continue
                invalid_popup_frames += 1
                if invalid_popup_frames >= 6:
                    logger.warning("HucheShop: item/quantity/price not verified, cancel purchase")
                    if self.handle_purchase_cancel():
                        cancel_requested = True

            elif shop_ready:
                invalid_popup_frames = 0
                if offers and not stable:
                    continue
                if confirmed and pending is not None and any(item.price == pending.price for item in offers):
                    continue
                if (self.regular_price in checked_prices and pending is None and not confirmed
                        and not any(item.price != self.regular_price for item in offers)):
                    # Rotating stock is optional: a new batch may contain no
                    # medals. Once the fixed quota is resolved, inspect only
                    # the leading batch and stop at its explicit boundary.
                    # A missing Mystic template by itself never completes it.
                    view = self.read_discount_view()
                    action = batch_scan.observe(view)
                    # A broken row must not spend the full purchase timeout
                    # rereading identical text. Retry a bounded number of new
                    # screenshots, then leave this refresh unchecked. Valid
                    # progress resets the budget; ordinary interval waits do
                    # not consume it. An unverified Mystic candidate is never
                    # reclassified as an empty batch just to make progress.
                    if view is None or action == "target":
                        batch_unverified_frames += 1
                        if batch_unverified_frames >= self.BATCH_UNVERIFIED_LIMIT:
                            reason = "unverified Mystic offer" if action == "target" else "unreadable batch rows"
                            logger.warning(f"HucheShop: {reason} after {batch_unverified_frames} frames, retry task later")
                            return False
                    else:
                        batch_unverified_frames = 0
                    if action == "complete":
                        # OCR can cross the refresh boundary after the loop's
                        # initial clock check. Never stamp the new batch with
                        # the previous batch's absence observation.
                        finished_at = aware_time()
                        if not window.contains(finished_at) or window.refresh_start(finished_at) != refresh_at:
                            return False
                        mark_activity_checked(self.config, self.activity_id)
                        logger.info("HucheShop: rotating batch inspected, no Mystic Medals this refresh")
                        return True
                    if action == "recover" and batch_scan.recoveries >= self.BATCH_RECOVERY_LIMIT:
                        logger.warning(f"HucheShop: overlap {batch_scan.tail.name!r} not recovered after "
                                       f"{batch_scan.recoveries} backward drags, retry task later")
                        return False
                    if action in ("up", "down", "recover") and self.interval_is_reached(HUCHE_ITEMS_AREA, interval=2):
                        if batch_scan.scrolls >= self.SCROLL_UP_LIMIT + self.SCROLL_DOWN_LIMIT:
                            logger.warning("HucheShop: rotating batch coverage not verified")
                            return False
                        left, top, right, bottom = HUCHE_ITEMS_AREA.area
                        x = (left + right) // 2
                        upper, lower = (x, top + 100), (x, bottom - 100)
                        # Touch backends release a swipe immediately and may
                        # ignore its duration argument. E7 then flings several
                        # rows past the intended endpoint. Drag holds at the
                        # endpoint before release; one-row steps leave enough
                        # overlap to verify coverage on two settled frames.
                        # Recovery reverses only one row and retains the old
                        # tail, even when the regular boundary is already seen.
                        if action == "up":
                            start, end = upper, lower
                            logger.info("HucheShop: return to list top")
                        elif action == "recover":
                            start, end = upper, (x, upper[1] + self.BATCH_DRAG_STEP)
                            logger.info(f"HucheShop: recover overlap {batch_scan.tail.name!r}, "
                                        f"attempt {batch_scan.recoveries + 1}/{self.BATCH_RECOVERY_LIMIT}, "
                                        f"visible={[item.name for item in view.items]}")
                        else:
                            start, end = lower, (x, lower[1] - self.BATCH_DRAG_STEP)
                            logger.info("HucheShop: scan next rotating rows")
                        self.device.drag(start, end)
                        self.interval_reset(HUCHE_ITEMS_AREA, interval=2)
                        batch_scan.scrolled(view, action)
                        previous = None
                    continue
                if stable and not confirmed:
                    candidates = [item for item in offers if item.remaining > 0 and item.price not in checked_prices]
                    if candidates:
                        balance = self.read_balance()
                        if balance is not None:
                            for offer in candidates:
                                if balance < offer.price:
                                    logger.info(f"HucheShop: insufficient skystones for {offer.price} offer, skip this refresh")
                                    checked_prices.add(offer.price)
                                    continue
                                offset = (0, offer.row[1] - HUCHE_MYSTIC_ITEM.area[1])
                                if self._match_at(HUCHE_MYSTIC_BUY, offset) and self.interval_is_reached(HUCHE_MYSTIC_BUY, interval=2):
                                    self.device.click(ClickButton(area_offset(HUCHE_MYSTIC_BUY.area, offset), name="HucheMysticBuy"))
                                    self.interval_reset(HUCHE_MYSTIC_BUY, interval=2)
                                    pending = offer
                                    previous = None
                                    break
                            else:
                                continue
                            continue
                # Both top and bottom matter: purchased items move to the end
                # on the next entry. Never use a fixed second-row click.
                if self.interval_is_reached(HUCHE_ITEMS_AREA, interval=2):
                    if scrolls >= self.SCROLL_UP_LIMIT + self.SCROLL_DOWN_LIMIT:
                        logger.warning("HucheShop: expected Mystic Medal stock not fully verified")
                        return False
                    left, top, right, bottom = HUCHE_ITEMS_AREA.area
                    x = (left + right) // 2
                    upper, lower = (x, top + 100), (x, bottom - 100)
                    start, end = (upper, lower) if scrolls < self.SCROLL_UP_LIMIT else (lower, upper)
                    self.device.swipe(start, end, duration=(0.3, 0.4))
                    self.interval_reset(HUCHE_ITEMS_AREA, interval=2)
                    scrolls += 1
                    previous = None
                    continue

            if confirmed and self.handle_touch_to_close(interval=2):
                previous = None
                continue
            if self.handle_network_error():
                previous = None
                continue

    def run(self) -> bool:
        """Buy the active overseas event's medals and leave navigation to entry.

        Pages:
            in: page_main, any
            out: page_huche_shop when inspected; current page when skipped
        """
        if not server.is_oversea_server(self.config.Emulator_PackageName) or server.lang != "global_cn":
            self.config.task_delay(server_update=True)
            return True
        if not self.config.SpecialActivity_BuyHucheMysticMedals:
            self.config.task_delay(server_update=True)
            return True
        window = next((event for event in active_activities(self.config) if event.event_id == self.activity_id), None)
        if window is None or is_activity_checked_in_window(self.config, window):
            self.config.task_delay(server_update=True)
            return True
        values = load_info().values("huche_shop", "OVERSEA")
        self.price_limits = {values["mystic_price"]: values["mystic_stock"],
                             values["mystic_regular_price"]: values["mystic_regular_stock"]}
        self.regular_price = values["mystic_regular_price"]
        self.medals_per_item = values["mystic_quantity"]
        if (window.refresh_hours <= 0 or len(self.price_limits) != 2
                or type(self.medals_per_item) is not int or self.medals_per_item <= 0
                or any(type(value) is not int or value <= 0 for pair in self.price_limits.items() for value in pair)):
            raise ValueError("HucheShop requires a refresh cycle, two distinct prices and positive integer quantities")
        if not self.device.app_is_running():
            from tasks.login.login import Login

            Login(self.config, device=self.device).app_start()
        # Import the conditional page only after the server/language guard.
        from tasks.base.page import page_huche_shop

        self.ui_goto(page_huche_shop)
        success = self.run_purchase(window)
        if not success:
            self.config.task_delay(success=False)
        return success
