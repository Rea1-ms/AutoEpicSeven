"""Buy rising-price friendship gifts one at a time toward a weekly target."""

import re
from dataclasses import dataclass

from module.base.button import ClickButton
from module.base.timer import Timer
from module.base.utils import area_offset
from module.logger import logger
from module.ocr.ocr import Ocr
from tasks.base.assets.assets_base_popup import NETWORK_ERROR_ABNORMAL, NETWORK_ERROR_DISCONNECT
from tasks.base.resource_bar import (
    RESOURCE_BAR_ICONS, RESOURCE_BAR_SPECS, RESOURCE_KIND_INT, ResourceBarMixin, ResourceBarSpec,
)
from tasks.store.assets.assets_store_entries import FREE_STORE_CHECK
from tasks.store.assets.assets_store_friendship_gift import (
    FRIENDSHIP_GIFT_BALANCE_ICON,
    FRIENDSHIP_GIFT_CANCEL,
    FRIENDSHIP_GIFT_CARD_CURRENCY,
    FRIENDSHIP_GIFT_CONFIRM,
    FRIENDSHIP_GIFT_ITEM,
    FRIENDSHIP_GIFT_POPUP_CHECK,
    FRIENDSHIP_GIFT_POPUP_CURRENCY,
    FRIENDSHIP_GIFT_POPUP_ITEM,
    FRIENDSHIP_GIFT_STORE_SELECTED,
    OCR_FRIENDSHIP_GIFT_CARD_PRICE,
    OCR_FRIENDSHIP_GIFT_CARD_STOCK,
    OCR_FRIENDSHIP_GIFT_POPUP_PRICE,
    OCR_FRIENDSHIP_GIFT_POPUP_STOCK,
)
from tasks.store.purchase import PurchaseResult, resolve_ocr_lang, resolve_period_purchase_quantity


WEEKLY_LIMIT = 10
PRICES = (200, 400, 800, 1600, 3200, 6400, 6400, 6400, 6400, 6400)
FRIENDSHIP_STORE_LAYOUT = ('friendship', 'stamina')
FRIENDSHIP_STORE_ICONS = {'friendship': FRIENDSHIP_GIFT_BALANCE_ICON, **RESOURCE_BAR_ICONS}
FRIENDSHIP_STORE_SPECS = {
    'friendship': ResourceBarSpec('friendship', RESOURCE_KIND_INT), **RESOURCE_BAR_SPECS,
}


def parse_amount(text):
    text = re.sub(r"\s", "", text).replace("，", ",")
    if re.fullmatch(r"(?:\d+|\d{1,3}(?:,\d{3})+)", text):
        return int(text.replace(",", ""))
    return None


def price_for_remaining(remaining):
    return PRICES[WEEKLY_LIMIT - remaining] if 0 < remaining <= WEEKLY_LIMIT else None


@dataclass(frozen=True)
class GiftCard:
    remaining: int
    price: int | None
    balance: int


@dataclass(frozen=True)
class GiftPopup:
    remaining: int
    price: int
    balance: int


class FriendshipGiftMixin(ResourceBarMixin):
    TIMEOUT_SECONDS = 60
    SCROLL_LIMIT = 6

    def _ocr(self, asset, offset=(0, 0)):
        area = area_offset(asset.area, offset)
        if not (0 <= area[0] < area[2] <= self.device.image.shape[1]
                and 0 <= area[1] < area[3] <= self.device.image.shape[0]):
            return ""
        text = Ocr(ClickButton(area, name=asset.name), lang=resolve_ocr_lang(self.config)).ocr_single_line(
            self.device.image)
        logger.attr(asset.name, text)
        return text

    def _match_at(self, asset, offset=(0, 0)):
        buttons = tuple(asset.iter_buttons())
        searches = tuple(button.search for button in buttons)
        try:
            asset.load_search(area_offset(asset.search, offset))
            return self.match_template_color(asset)
        finally:
            for button, search in zip(buttons, searches):
                button.load_search(search)
                button.clear_offset()

    def _balance(self):
        inspected = self.inspect_resource_bar_status(
            FRIENDSHIP_STORE_LAYOUT, 'FriendshipStore',
            icons=FRIENDSHIP_STORE_ICONS, specs=FRIENDSHIP_STORE_SPECS,
        )
        return inspected.final['friendship'].value if inspected.final is not None else None

    def _shop_ready(self):
        # The reward toast changes this highlight by up to 17 color levels in
        # the supplied captures; modal shading changes it by over 200 levels.
        return self.appear(FREE_STORE_CHECK) and self.match_color(FRIENDSHIP_GIFT_STORE_SELECTED, threshold=20)

    def _popup_ready(self):
        return (self.match_template_color(FRIENDSHIP_GIFT_POPUP_CHECK)
                and self.match_template_color(FRIENDSHIP_GIFT_POPUP_ITEM))

    def _read_card(self):
        matches = FRIENDSHIP_GIFT_ITEM.match_multi_template(self.device.image, threshold=30)
        if len(matches) != 1:
            return None
        offset = (matches[0].area[0] - FRIENDSHIP_GIFT_ITEM.area[0],
                  matches[0].area[1] - FRIENDSHIP_GIFT_ITEM.area[1])
        stock = re.fullmatch(r"(\d{1,2})/10", re.sub(r"\s", "", self._ocr(
            OCR_FRIENDSHIP_GIFT_CARD_STOCK, offset)).replace("／", "/"))
        if stock is None or not 0 <= int(stock[1]) <= WEEKLY_LIMIT:
            return None
        remaining = int(stock[1])
        balance = self._balance()
        if balance is None:
            return None
        price = None
        if remaining:
            if not self._match_at(FRIENDSHIP_GIFT_CARD_CURRENCY, offset):
                return None
            price = parse_amount(self._ocr(OCR_FRIENDSHIP_GIFT_CARD_PRICE, offset))
            if price is None:
                return None
        return GiftCard(remaining, price, balance)

    def _read_popup(self):
        if not self._match_at(FRIENDSHIP_GIFT_POPUP_CURRENCY):
            return None
        stock = re.fullmatch(r"剩余可购买次数[:：](\d{1,2})次", re.sub(r"\s", "", self._ocr(
            OCR_FRIENDSHIP_GIFT_POPUP_STOCK)))
        price = parse_amount(self._ocr(OCR_FRIENDSHIP_GIFT_POPUP_PRICE))
        balance = self._balance()
        if stock is None or not 1 <= int(stock[1]) <= WEEKLY_LIMIT or price is None or balance is None:
            return None
        return GiftPopup(int(stock[1]), price, balance)

    def _purchase_friendship_gift(self, item, skip_first_screenshot=True):
        """Buy individual gifts until the weekly target or available funds end.

        Pages:
            in: selected general store, caller has prepared the first screenshot
            out: same store after completion/cancellation; current page on failure
        """
        timeout = Timer(self.TIMEOUT_SECONDS, count=120).start()
        selected = None
        pending = None
        stop_result = None
        bought = 0
        scrolls = 0

        # selected describes the card whose click actually opened our dialog.
        # pending is set only after sending its payment, and survives old frames,
        # resource update delays and network overlays. Never pay again while it
        # exists: an unchanged dialog cannot prove the server rejected payment.
        # Clear pending only when this item's stock and actual debit both agree.
        # Price alone cannot confirm a purchase at the 6400-point plateau.
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            blocked = self.appear(NETWORK_ERROR_DISCONNECT) or self.appear(NETWORK_ERROR_ABNORMAL)
            popup_ready = False
            shop_ready = False
            card = None
            if not blocked:
                # The selected tab remains recognizable behind the popup.
                # Its presence alone must never allow a second card/payment.
                popup_ready = self._popup_ready()
                if not popup_ready and self._shop_ready():
                    shop_ready = True
                    card = self._read_card()

            # End/progress: recognize the result before any click or handler.
            if shop_ready and stop_result is not None:
                return stop_result
            if card is not None:
                if pending is not None:
                    if card.remaining == pending.remaining - 1 and card.balance == pending.balance - pending.price:
                        bought += 1
                        logger.info(f'FriendshipGift: purchased={bought}, remaining={card.remaining}')
                        pending = selected = None
                        timeout.reset()
                    else:
                        card = None
                if card is not None:
                    if resolve_period_purchase_quantity(
                            item.desired_quantity, item.purchase_limit, card.remaining)[1] == 0:
                        return PurchaseResult(True, bought, quantity_source='remaining_counter')
                    if card.price != price_for_remaining(card.remaining):
                        logger.warning(f'FriendshipGift: unexpected price={card.price}, remaining={card.remaining}')
                        return PurchaseResult(False, bought, quantity_source='remaining_counter')
                    if card.balance < card.price:
                        logger.info('FriendshipGift: insufficient friendship points')
                        return PurchaseResult(True, bought, quantity_source='remaining_counter')

            if timeout.reached():
                logger.warning(f'FriendshipGift: purchase timeout, selected={selected}, pending={pending}')
                self._save_debug_image('friendship_gift_timeout')
                return PurchaseResult(False, bought, quantity_source='remaining_counter')

            # Actions: the game screens determine the order; only payment needs
            # remembered evidence because sending a click is not a result.
            if popup_ready:
                if pending is not None:
                    continue
                if selected is None or stop_result is not None:
                    self.appear_then_click(FRIENDSHIP_GIFT_CANCEL, interval=2)
                    continue
                popup = self._read_popup()
                if popup is None:
                    continue
                if (popup.remaining, popup.price) != (selected.remaining, selected.price):
                    logger.warning('FriendshipGift: popup stock/price differs from selected card')
                    stop_result = PurchaseResult(False, bought, quantity_source='remaining_counter')
                elif popup.balance < popup.price:
                    logger.info('FriendshipGift: insufficient friendship points in purchase dialog')
                    stop_result = PurchaseResult(True, bought, quantity_source='remaining_counter')
                elif self.appear_then_click(FRIENDSHIP_GIFT_CONFIRM, interval=2):
                    pending = popup
                    timeout.reset()
                continue

            if card is not None and self.interval_is_reached(FRIENDSHIP_GIFT_ITEM, interval=2):
                if self._click_purchase_target(item):
                    selected = card
                    self.interval_reset(FRIENDSHIP_GIFT_ITEM, interval=2)
                    continue

            # Search only when the item itself is absent. A visible but unreadable
            # card is not a reason to swipe, nor is a missing payment result.
            if (shop_ready and pending is None and stop_result is None
                    and not self._has_item(item.asset, self.device.image)
                    and scrolls < self.SCROLL_LIMIT
                    and self.interval_is_reached(FRIENDSHIP_GIFT_ITEM, interval=2)):
                self.device.swipe(self.INHERITANCE_SCROLL_START, self.INHERITANCE_SCROLL_END, duration=(0.3, 0.4))
                self.interval_reset(FRIENDSHIP_GIFT_ITEM, interval=2)
                scrolls += 1
                continue

            # Shared handlers come last and retain any unconfirmed payment.
            # A visible network error blocks clicks even when its handler is
            # on cooldown and returns False.
            if blocked:
                self.handle_network_error()
                continue
            if self.ui_additional():
                continue
