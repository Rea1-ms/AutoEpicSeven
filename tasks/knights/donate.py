"""Inventory-bounded donations, with observations independent of click intent."""

import re
import unicodedata
from dataclasses import dataclass

import numpy as np

from module.base.button import ClickButton
from module.base.timer import Timer
from module.logger import logger
from module.ocr.language import resolve_ocr_lang
from module.ocr.ocr import Ocr
from tasks.base.page import page_knights
from tasks.knights.assets.assets_knights_activity_support_active import (
    HIT_BOTTOM,
    SUPPORT_ACTION,
    SUPPORT_COMPLETE,
)
from tasks.knights.assets.assets_knights_activity_support_entry import SUPPORT_CHECK


def normalize_donation_text(text):
    return re.sub(r'\s', '', unicodedata.normalize('NFKC', text))


def parse_donation_stock(text):
    matched = re.fullmatch(r'持有[:：]?(\d+|\d{1,3}(?:,\d{3})+)', normalize_donation_text(text))
    return int(matched[1].replace(',', '')) if matched else None


def parse_donation_quantity(text):
    matched = re.fullmatch(r'支援([1-9]\d?)个', normalize_donation_text(text))
    return int(matched[1]) if matched else None


def donation_views_match(before, after):
    return before[0] == after[0] and np.mean(np.abs(before[1] - after[1])) < 2


@dataclass(frozen=True)
class DonationQuote:
    item: str
    button: ClickButton
    stock: int
    quantity: int
    identity: bytes


class KnightsDonateMixin:
    SUPPORT_DONATE_TIMEOUT_SECONDS = 30
    SUPPORT_DONATE_ACTION_INTERVAL_SECONDS = 2
    SUPPORT_DONATE_RESULT_TIMEOUT_SECONDS = 10
    SUPPORT_DONATE_EMPTY_CONFIRM_SECONDS = 1
    SUPPORT_DONATE_COMPLETE_COUNT = 4

    def _donation_text(self, area, name):
        return Ocr(ClickButton(area, name=name), lang=resolve_ocr_lang(self.config)).ocr_single_line(
            self.device.image
        )

    def _donation_stock_at(self, center_y):
        area = (1030, center_y - 60, 1208, center_y - 30)
        # Check the field itself before cropping. A clipped icon can still have
        # a complete stock label, while a complete icon cannot rescue a label
        # hidden by the panel header. Zero is valid; unreadable is None.
        if area[1] < 133 or area[3] > 645:
            return None
        return parse_donation_stock(self._donation_text(area, 'OCR_DONATION_STOCK'))

    def _donation_quote(self, item, button):
        center_y = round(self._button_center_y(button))
        area = (1060, center_y - 16, 1190, center_y + 17)
        if not self._donation_fields_visible(button):
            return None
        stock = self._donation_stock_at(center_y)
        quantity = parse_donation_quantity(self._donation_text(area, 'OCR_DONATION_QUANTITY'))
        if stock is None or quantity is None:
            logger.warning(f'Knights donation: unreadable quote for {item} at {button.area}')
            return None
        return DonationQuote(item, button, stock, quantity, self._donation_identity(button))

    def _donation_fields_visible(self, button):
        center_y = round(self._button_center_y(button))
        return center_y - 60 >= 133 and center_y + 17 <= 645

    def _donation_identity(self, button):
        # The recipient, not the screen row, identifies a request. Exclude the
        # animated portrait border and keep only the inner avatar and name.
        # Keep these pixels in memory only; never log names or image bytes.
        center_y = round(self._button_center_y(button))
        image = self.device.image
        return (image[center_y - 30:center_y + 4, 792:825].tobytes()
                + image[center_y - 52:center_y - 25, 845:1018].tobytes())

    def _donation_inventory(self, plans, item):
        stocks = set()
        for plan in plans:
            if plan.name != item:
                continue
            for icon in plan.asset.match_multi_template(self.device.image, threshold=30):
                # Item centers are 14/15 pixels above the action label center.
                stock = self._donation_stock_at(round(self._button_center_y(icon)) + 15)
                if stock is not None:
                    stocks.add(stock)
        return stocks.pop() if len(stocks) == 1 else None

    def _donation_page_ready(self):
        return self.appear(SUPPORT_CHECK) and SUPPORT_CHECK.match_color(self.device.image, threshold=30)

    def _donation_list_entries(self):
        return (SUPPORT_ACTION.match_multi_template(self.device.image, threshold=40),
                SUPPORT_COMPLETE.match_multi_template(self.device.image, threshold=40))

    def _donation_view(self):
        # Exclude animated portraits and use the item, progress and action
        # columns to observe actual list movement after a swipe.
        image = self.device.image
        # Different recipients can request identical items and quantities.
        # Compare their white name glyphs separately: averaging them together
        # with the large unchanged panel could mistake moving rows for a
        # stable view. Thresholding excludes the dim animated backdrop.
        names = np.all(image[135:645, 845:1018] > 180, axis=2).tobytes()
        content = np.concatenate((image[135:645:4, 315:750:4].ravel(),
                                  image[135:645:4, 1030:1208:4].ravel())).astype(np.int16)
        return names, content

    def _run_support_donate(self, skip_first_screenshot=True) -> bool:
        """Donate affordable enabled items and leave the support list usable.

        Pages:
            in: page_knights_support, possibly covered by a reward.
            out: page_knights_support, with no pending donation.
        """
        plans = self._enabled_plans(self._build_donate_plans())
        if not plans:
            logger.info('Knights support donate disabled by config')
            return True

        logger.info('Knights support: donate available inventory')
        timeout = Timer(self.SUPPORT_DONATE_TIMEOUT_SECONDS, count=90).start()
        result_timeout = Timer(self.SUPPORT_DONATE_RESULT_TIMEOUT_SECONDS, count=30)
        scroll_timer = Timer(1, count=2)
        empty_list_confirm = Timer(self.SUPPORT_DONATE_EMPTY_CONFIRM_SECONDS, count=4)
        pending = None
        reward_seen = False
        stock_caps = {}
        completed_requests = set()
        unreadable = set()
        before_scroll = None
        previous_view = None
        stable_frames = 0
        hit_bottom_confirm = 0
        # Entering support already places the list at the top. Inspect that
        # first usable frame directly; OCR only gates affordability and payment
        # confirmation, never the initial position or the established end marks.
        # After an actual swipe, before_scroll still requires settled new frames.
        self.interval_clear(SUPPORT_ACTION, interval=self.SUPPORT_DONATE_ACTION_INTERVAL_SECONDS)
        self.device.click_record_clear()

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached() or (pending is not None and result_timeout.reached()):
                logger.warning('Knights donation: observation timeout, donation not confirmed')
                return False

            if self._donation_page_ready():
                view = self._donation_view()
                if previous_view is None:
                    stable_frames = 2
                else:
                    stable_frames = stable_frames + 1 if donation_views_match(view, previous_view) else 0
                previous_view = view
                if before_scroll is not None:
                    if not scroll_timer.reached() or stable_frames < 2:
                        continue
                    # A swipe may have no effect. Stable unchanged pixels only
                    # allow another observation/action, never prove the bottom;
                    # completion still uses the established check/empty states.
                    if not donation_views_match(view, before_scroll):
                        timeout.reset()
                    before_scroll = None

                action_buttons, complete_buttons = self._donation_list_entries()
                pairs = self._find_donate_action_pairs(plans, action_buttons=action_buttons)
                if pending is not None:
                    stock = self._donation_inventory(plans, pending.item)
                    expected = pending.stock - pending.quantity
                    quotes = [self._donation_quote(name, button) for name, button in pairs]
                    still_actionable = any(name == pending.item
                                           and self._donation_identity(button) == pending.identity
                                           for name, button in pairs)
                    same_request = next((quote for quote in quotes if quote is not None
                                         and quote.item == pending.item
                                         and quote.identity == pending.identity), None)
                    # Closing a popup is only a pending result signal. Require
                    # either the exact debit or a fresh usable list where this
                    # request is no longer actionable before the next donation.
                    # An unchanged old list must never release another click.
                    if stock == expected or (reward_seen and not still_actionable):
                        stock_caps[pending.item] = expected
                        completed_requests.add((pending.item, pending.identity))
                        logger.info(f'Knights donation confirmed: {pending.item}, remaining={expected}')
                        pending = None
                        reward_seen = False
                        timeout.reset()
                        continue
                    if (not reward_seen and stock == pending.stock and same_request is not None
                            and same_request.quantity == pending.quantity
                            and self.interval_is_reached(
                                SUPPORT_ACTION, interval=self.SUPPORT_DONATE_ACTION_INTERVAL_SECONDS)):
                        self.device.click(same_request.button)
                        self.interval_reset(SUPPORT_ACTION, interval=self.SUPPORT_DONATE_ACTION_INTERVAL_SECONDS)
                    continue

                if stable_frames >= 2:
                    candidate = None
                    for name, button in pairs:
                        # A row cut by the viewport is revisited after scrolling;
                        # it is not an OCR failure and must not poison the final
                        # outcome when the same request later becomes readable.
                        if not self._donation_fields_visible(button):
                            continue
                        key = (name, self._donation_identity(button))
                        if key in completed_requests:
                            continue
                        quote = self._donation_quote(name, button)
                        if quote is None:
                            unreadable.add(key)
                            continue
                        unreadable.discard(key)
                        stock = min(quote.stock, stock_caps.get(name, quote.stock))
                        if stock < quote.quantity:
                            logger.info(f'Knights donation: skip {name}, stock={stock}, required={quote.quantity}')
                            continue
                        candidate = DonationQuote(name, button, stock, quote.quantity, quote.identity)
                        break
                    if candidate is not None:
                        if self.interval_is_reached(SUPPORT_ACTION, interval=self.SUPPORT_DONATE_ACTION_INTERVAL_SECONDS):
                            self.device.click(candidate.button)
                            self.interval_reset(SUPPORT_ACTION, interval=self.SUPPORT_DONATE_ACTION_INTERVAL_SECONDS)
                            pending = candidate
                            reward_seen = False
                            result_timeout.reset()
                        continue

                if stable_frames >= 2:
                    # Preserve the original completion rules, after checking
                    # affordable requests in this same frame. A pending payment
                    # must be confirmed above before any marker can end scanning.
                    if len(complete_buttons) >= self.SUPPORT_DONATE_COMPLETE_COUNT:
                        logger.info('Knights donation: visible requests completed')
                        return not unreadable
                    if self.appear(HIT_BOTTOM):
                        hit_bottom_confirm += 1
                        if hit_bottom_confirm >= 2:
                            logger.info('Knights donation: reached white bottom check')
                            return not unreadable
                        continue
                    hit_bottom_confirm = 0

                    # The player's own request has neither a support action nor
                    # a completed check. Confirm the usable panel briefly and
                    # leave without swiping; do not wait for the global timeout.
                    # Count all actions, including disabled/unsupported items,
                    # so an unpaired request cannot masquerade as an empty list.
                    if action_buttons or complete_buttons:
                        empty_list_confirm.clear()
                    else:
                        if not empty_list_confirm.started():
                            empty_list_confirm.start()
                        elif empty_list_confirm.reached():
                            logger.info('Knights donation: no other requests')
                            return not unreadable
                        continue

                    self.device.swipe(self.SUPPORT_SCROLL_START, self.SUPPORT_SCROLL_END, duration=(0.2, 0.3))
                    before_scroll = view
                    stable_frames = 0
                    hit_bottom_confirm = 0
                    scroll_timer.reset()
                    continue

            elif self.appear(page_knights.check_button) or self.is_in_main(interval=0):
                logger.warning('Knights donation: unexpectedly left support')
                return False

            # Use the shared close control without OCR of popup sentences.
            # Keep this signal until the pending request is confirmed above;
            # closing alone does not prove payment or permit another donation.
            if self.handle_touch_to_close(interval=2):
                if pending is not None:
                    reward_seen = True
                stable_frames = 0
                previous_view = None
                empty_list_confirm.clear()
                hit_bottom_confirm = 0
                continue
            if self.handle_network_error():
                stable_frames = 0
                previous_view = None
                empty_list_confirm.clear()
                hit_bottom_confirm = 0
                continue
