"""Period targets for the guild member shop, with price-button-only clicks."""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from module.base.button import ButtonWrapper, ClickButton, match_template
from module.base.timer import Timer
from module.base.utils import color_similar, crop, get_color
from module.config import server
from module.config.utils import server_time_offset
from module.exception import RequestHumanTakeover
from module.logger import logger
from module.ocr.ocr import Ocr
from tasks.base.assets.assets_base_popup import NETWORK_ERROR_ABNORMAL, NETWORK_ERROR_DISCONNECT
from tasks.base.ui import UI
from tasks.knights.assets.assets_knights_shop import (
    ACCESSORY_RESET_STONE, ARMBAND_ICON, ARMBAND_BALANCE_ICON,
    BRAVE_CREST_ICON, BRAVE_CREST_BALANCE_ICON, CATALYST_CHEST,
    CONVERSION_GEM_CHEST, EPIC_CATALYST_CHEST, EPIC_SPIRIT_BLOOM,
    EQUIPMENT_RESET_STONE, FIRE_CONNECTION, GOLD_TRANSMIT_STONE, ICE_CONNECTION,
    LEVEL_85_MANASTONE_CHEST, LEVEL_88_MANASTONE_CHEST, MOLAGORA, MYSTIC_MEDALS,
    NATURE_CONNECTION, PROOF_OF_COURAGE_ICON, PROOF_OF_COURAGE_BALANCE_ICON,
    MANASTONE_LEVEL_85, MANASTONE_LEVEL_88,
    RARE_CATALYST_CHEST, REFORGE_MATERIAL_CHEST, REFORGING_STONE_CHEST,
    SHOP_CHECK, SHOP_PRICE_ACTIVE, SHOP_PURCHASE_CANCEL,
    SHOP_SELECTED,
    SHOP_PURCHASE_FINAL_CHECK, SHOP_PURCHASE_FINAL_CONFIRM, SHOP_PURCHASE_FINAL_CANCEL,
)
from tasks.store.assets.assets_store_actions import (
    BUY_CONFIRM_MULTI, BUY_CONFIRM_SINGLE, BUY_MAX, BUY_MIN,
    BUY_TIMES_MINUS, BUY_TIMES_PLUS, OCR_BUY_TIMES,
)
from tasks.store.purchase import (
    normalize_config_purchase_quantity, plan_purchase_selection,
    PurchaseCounterPreset, ocr_purchase_counter,
    resolve_ocr_lang, resolve_period_purchase_quantity,
)


@dataclass(frozen=True)
class GuildShopItem:
    option: str
    title: str
    asset: ButtonWrapper
    limit: int
    currency: str
    price: int


CURRENCIES = {
    'armband': ARMBAND_ICON,
    'crest': BRAVE_CREST_ICON,
    'proof': PROOF_OF_COURAGE_ICON,
}
BALANCE_ICONS = {
    'armband': ARMBAND_BALANCE_ICON,
    'crest': BRAVE_CREST_BALANCE_ICON,
    'proof': PROOF_OF_COURAGE_BALANCE_ICON,
}
ITEMS = (
    GuildShopItem('KnightsShopWeekly_MysticMedals', '50个神秘奖牌', MYSTIC_MEDALS, 1, 'crest', 200),
    GuildShopItem('KnightsShopWeekly_RareCatalystChest', '稀有催化剂选择箱', RARE_CATALYST_CHEST, 12, 'crest', 30),
    GuildShopItem('KnightsShopWeekly_EpicCatalystChest', '传说催化剂选择箱', EPIC_CATALYST_CHEST, 5, 'crest', 150),
    GuildShopItem('KnightsShopWeekly_CatalystChest', '催化剂箱', CATALYST_CHEST, 10, 'proof', 180),
    GuildShopItem('KnightsShopWeekly_ReforgingStoneChest', '装备炼制石选择箱', REFORGING_STONE_CHEST, 1, 'crest', 150),
    GuildShopItem('KnightsShopMonthly_EquipmentResetStone', '装备魔控催化剂', EQUIPMENT_RESET_STONE, 1, 'armband', 120),
    GuildShopItem('KnightsShopMonthly_AccessoryResetStone', '饰品魔控催化剂', ACCESSORY_RESET_STONE, 1, 'armband', 150),
    GuildShopItem('KnightsShopMonthly_Level85ManastoneChest', 'Lv.85魔石选择箱', LEVEL_85_MANASTONE_CHEST, 1, 'armband', 70),
    GuildShopItem('KnightsShopMonthly_Level88ManastoneChest', 'Lv.88魔石选择箱', LEVEL_88_MANASTONE_CHEST, 1, 'armband', 80),
    GuildShopItem('KnightsShopMonthly_ConversionGemChest', '传说转换石选择箱', CONVERSION_GEM_CHEST, 2, 'armband', 40),
    GuildShopItem('KnightsShopMonthly_GoldTransmitStone', '金光传承石', GOLD_TRANSMIT_STONE, 2, 'crest', 300),
    GuildShopItem('KnightsShopMonthly_EpicSpiritBloom', '最上级精灵之花', EPIC_SPIRIT_BLOOM, 10, 'proof', 600),
    GuildShopItem('KnightsShopMonthly_Molagora', '魔罗戈拉', MOLAGORA, 5, 'proof', 250),
    GuildShopItem('KnightsShopMonthly_FireConnection', '猛烈火焰之缘', FIRE_CONNECTION, 5, 'crest', 100),
    GuildShopItem('KnightsShopMonthly_NatureConnection', '清新自然之缘', NATURE_CONNECTION, 5, 'crest', 100),
    GuildShopItem('KnightsShopMonthly_IceConnection', '冰凉寒气之缘', ICE_CONNECTION, 5, 'crest', 100),
    GuildShopItem('KnightsShopMonthly_ReforgingStoneChest', '装备炼制石选择箱', REFORGING_STONE_CHEST, 5, 'proof', 350),
    GuildShopItem('KnightsShopMonthly_ReforgeMaterialChest', '重铸材料选择箱', REFORGE_MATERIAL_CHEST, 4, 'proof', 450),
)


def enabled_purchases(config):
    """Read only this task's bound options; old configurations default to zero."""
    return [(item, quantity) for item in ITEMS if (quantity := normalize_config_purchase_quantity(
        getattr(config, item.option, 0), maximum=item.limit
    )) > 0]


def shop_period(item, now, daily_trigger, offset):
    """Use the server's refresh day, including the hours before its reset."""
    hour, minute = map(int, daily_trigger.split(':'))
    effective = now - offset - timedelta(hours=hour, minutes=minute)
    if item.option.startswith('KnightsShopWeekly_'):
        monday = effective.date() - timedelta(days=effective.weekday())
        return f'week:{monday.isoformat()}'
    return f'month:{effective:%Y-%m}'


def parse_stock(text):
    matched = re.fullmatch(r'(\d{1,2})/(\d{1,2})', re.sub(r'\s', '', text).replace('／', '/'))
    if matched:
        remaining, limit = map(int, matched.groups())
        if 0 <= remaining <= limit <= 12 and limit > 0:
            return remaining, limit
    return None


def parse_amount(text):
    text = re.sub(r'\s', '', text).replace('，', ',')
    if re.fullmatch(r'(?:\d+|\d{1,3}(?:,\d{3})+)', text):
        return int(text.replace(',', ''))
    return None


@dataclass(frozen=True)
class ShopCard:
    button: ClickButton
    remaining: int
    active: bool
    price: int | None


@dataclass
class PurchaseAttempt:
    item: GuildShopItem
    before: int
    quantity: int
    confirmed: bool = False
    evidence: tuple | None = None
    # The selection dialog can lead to a distinct final payment dialog. Keep
    # each click latched independently, including across network recovery;
    # neither dialog disappearing nor its repeated frames prove payment.
    final_confirmed: bool = False
    # Final-dialog observations must be consecutive and must not borrow the
    # selection/stock evidence, whose tuple has a different meaning. Reset
    # on any intervening page, overlay, or unrecognized final-dialog frame.
    final_evidence: bool | None = None


class KnightsShopMixin:
    """Search both rows independently of the order of active/sold-out cards."""

    SCROLL_START = (1060, 392)
    SCROLL_END = (788, 392)
    SCROLL_LIMIT = 32
    QUANTITY_BATCH_CLICK_LIMIT = 3
    QUANTITY_BATCH_CLICK_INTERVAL = (0.2, 0.3)
    QUANTITY_CLICK_INTERVAL_SECONDS = 0.8
    QUANTITY_POST_CLICK_SETTLE_SECONDS = 0.6

    def _shop_record_context(self):
        package = getattr(self.config, 'Emulator_PackageName', '')
        trigger = getattr(self.config, 'Scheduler_ServerUpdate', server.get_server_update(package))
        trigger = trigger.split(',')[0].strip()
        return f'{server.normalize_server(package)}|{trigger}', trigger, server_time_offset()

    def _pending_shop_purchases(self, now=None):
        purchases = enabled_purchases(self.config)
        records = getattr(self.config, 'KnightsShopRuntime_Purchases', {})
        if not purchases or not isinstance(records, dict) or not records:
            return purchases
        now = now or datetime.now()
        scope, trigger, offset = self._shop_record_context()
        pending = []
        for item, target in purchases:
            record = records.get(item.option)
            if isinstance(record, dict):
                try:
                    checked_at = datetime.fromisoformat(record.get('checked_at', ''))
                    purchased = record.get('purchased')
                    valid = (
                        checked_at <= now
                        and type(purchased) is int and 0 <= purchased <= item.limit
                        and record.get('scope') == scope
                        and record.get('period') == shop_period(item, checked_at, trigger, offset)
                        and record['period'] == shop_period(item, now, trigger, offset)
                    )
                except (TypeError, ValueError):
                    valid = False
                if valid and purchased >= target:
                    logger.info(f'Knights shop: {item.option} recorded period target reached; skip')
                    continue
            pending.append((item, target))
        return pending

    def _record_shop_purchase(self, item, purchased, checked_at):
        # Replace the bound dictionary to trigger normal config persistence;
        # mutating it in place would only update this process's memory. Save
        # each item's observed total, including manual purchases, so a higher
        # target in the same period can still request the missing quantity.
        records = getattr(self.config, 'KnightsShopRuntime_Purchases', {})
        records = dict(records) if isinstance(records, dict) else {}
        scope, trigger, offset = self._shop_record_context()
        records[item.option] = dict(
            scope=scope, period=shop_period(item, checked_at, trigger, offset),
            purchased=purchased, checked_at=checked_at.isoformat(),
        )
        self.config.KnightsShopRuntime_Purchases = records

    def _text(self, area, name):
        return Ocr(ClickButton(area, name=name), lang=resolve_ocr_lang(self.config)).ocr_single_line(self.device.image)

    def _on_shop(self):
        return self.appear(SHOP_CHECK) and SHOP_SELECTED.match_color(self.device.image, threshold=25)

    def _shop_content_ready(self):
        """Require an actual product image, beyond the early tab highlight."""
        if not self._on_shop():
            return False
        seen = set()
        for item in ITEMS:
            if item.asset in seen:
                continue
            seen.add(item.asset)
            for match in item.asset.match_multi_template(self.device.image, similarity=0.88):
                x, y, _, _ = match.area
                if x - 18 >= 289 and x + 180 <= 1225 and min(abs(y - 153), abs(y - 410)) <= 3:
                    return True
        return False

    def _cards(self, item):
        if not self._on_shop():
            return []
        cards = []
        for match in item.asset.match_multi_template(self.device.image, similarity=0.88):
            x, y, _, _ = match.area
            # A title can match while its card is clipped by the scroll viewport.
            # Stock and price must belong to the same fully visible card; in
            # particular the two reforging boxes share a title, but not a limit
            # or currency. Never choose the first title match unconditionally.
            if x - 18 < 289 or x + 180 > 1225 or min(abs(y - 153), abs(y - 410)) > 3:
                continue
            if item.asset in (LEVEL_85_MANASTONE_CHEST, LEVEL_88_MANASTONE_CHEST):
                # The two labels differ by one digit and both cost armbands.
                # A broad title template can match both, including when sold
                # out and the price has vanished. Match only the distinguishing
                # level digits at this title's offset, without Chinese OCR.
                label, other = ((MANASTONE_LEVEL_85, MANASTONE_LEVEL_88)
                                if item.asset == LEVEL_85_MANASTONE_CHEST
                                else (MANASTONE_LEVEL_88, MANASTONE_LEVEL_85))
                level_image = crop(self.device.image, (x + 42, y, x + 67, y + 23))
                if (not match_template(level_image, label.matched_button.image, similarity=0.95)
                        or match_template(level_image, other.matched_button.image, similarity=0.95)):
                    continue
            # Keep the complete counter width, including 12/12, but exclude
            # the card background above/below its white pill. The wider old
            # vertical crop reproducibly reads the captured 5/10 as G/10.
            # Fix the pixels rather than guessing G means 5: an unreadable
            # result must still block both new payment and its completion.
            stock = parse_stock(self._text((x + 112, y + 29, x + 175, y + 54), 'GuildShopStock'))
            if stock is None:
                return None
            if stock[1] != item.limit:
                continue
            price_area = match.button
            patch = (price_area[0] + 7, price_area[1] + 6, price_area[0] + 31, price_area[1] + 25)
            active = color_similar(get_color(self.device.image, patch), SHOP_PRICE_ACTIVE.color, threshold=30)
            price = None
            if active and stock[0] > 0:
                icon = CURRENCIES[item.currency]
                # These shop-owned currency assets are used only with a fresh
                # per-card search. Shared store assets are never mutated here.
                icon.load_search(price_area)
                hits = icon.match_multi_template(self.device.image, similarity=0.8)
                if len(hits) == 1:
                    area = hits[0].area
                    price = parse_amount(self._text((area[2] + 1, price_area[1], price_area[2] - 10, price_area[3]), 'GuildShopPrice'))
            cards.append(ShopCard(match, stock[0], active, price))
        return cards

    def _balance(self, item):
        # This header is right-aligned as a group. Adding a digit to any
        # balance shifts its own icon and all preceding currencies, so fixed
        # OCR boxes can silently turn 1,010 into 010 or 275 into 75. Bound
        # each number by its own matched icon and the next currency's icon.
        positions = []
        for currency, icon in BALANCE_ICONS.items():
            hits = icon.match_multi_template(self.device.image, similarity=0.88)
            if len(hits) != 1:
                return None
            positions.append((currency, hits[0].area))
        if not all(left[1][2] < right[1][0] for left, right in zip(positions, positions[1:])):
            return None
        for index, (currency, area) in enumerate(positions):
            if currency == item.currency:
                right = positions[index + 1][1][0] - 2 if index + 1 < len(positions) else 1210
                return parse_amount(self._text((area[2] + 2, 80, right, 107), 'GuildShopBalance'))
        return None

    def _popup(self):
        # Like the existing store, identify each layout by its buttons. The
        # caller's attempt records the card matched and clicked in this run;
        # a dialog without that context is canceled, never silently inherited.
        if self.appear(BUY_CONFIRM_MULTI):
            return BUY_CONFIRM_MULTI, True
        if self.appear(BUY_CONFIRM_SINGLE):
            return BUY_CONFIRM_SINGLE, False
        return None

    def _popup_evidence(self, attempt, confirm, multi):
        if not self.appear(confirm):
            return None
        if multi:
            # The stock ledger is already read from the matched card. Only
            # the numeric selection counter needs OCR here, using the same
            # DigitCounter path as StoreCurrent. Its bounded ratio parser
            # handles the logged "¥1/5" and "1/5双精灵" artifacts without
            # trying to recognize item names or the surrounding Chinese UI.
            current, _, total = ocr_purchase_counter(
                self.device.image, self.config,
                PurchaseCounterPreset('GuildShopQuantity', OCR_BUY_TIMES.area),
            )
        else:
            current, total = 1, 1
        if not 1 <= current <= total or total != attempt.before:
            return None
        return current, total

    def _final_popup_evidence(self, attempt):
        """Recognize the final stage of this run's validated selection."""
        # No Chinese text or duplicated numeric OCR gates this transition.
        # Item identity comes from the matched card and quantity from the
        # stable selection counter before its first confirmation was sent.
        # A final-looking dialog alone cannot authorize a new purchase.
        if not attempt.confirmed or not 1 <= attempt.quantity <= attempt.before:
            return None
        if self.appear(SHOP_PURCHASE_FINAL_CHECK) and self.appear(SHOP_PURCHASE_FINAL_CONFIRM):
            return True
        return None

    def _network_visible(self):
        return self.appear(NETWORK_ERROR_DISCONNECT) or self.appear(NETWORK_ERROR_ABNORMAL)

    def _recover_shop(self):
        """Return after the shared network handler has completed login/retry.

        Pages:
            in: a network retry or login result handled by the shared handler.
            out: page_knights_shop; the pending payment still needs stock proof.
        """
        from tasks.base.page import page_knights_shop

        self.ui_goto(page_knights_shop, skip_first_screenshot=False)

    def _title_strip(self):
        image = self.device.image
        return np.concatenate((crop(image, (289, 148, 1224, 178)), crop(image, (289, 405, 1224, 435))), axis=0)

    @staticmethod
    def _same_view(before, after):
        return before is not None and np.mean(np.abs(before.astype(float) - after.astype(float))) < 1.5

    def _swipe(self, backwards=False):
        # Use a 20% shorter overlapping step than the inheritance store. A long
        # fling can skip a whole view containing mystic medals and molagora.
        start, end = self.SCROLL_START, self.SCROLL_END
        self.device.swipe(*((end, start) if backwards else (start, end)), duration=(0.3, 0.35))

    def _fail(self, message):
        logger.critical(f'Knights shop: {message}')
        # Raise instead of returning normal completion. The task framework
        # preserves its error screenshot, and must not advance the daily run.
        raise RequestHumanTakeover(message)

    def _execute_shop(self, purchases, skip_first_screenshot=True):
        """Buy the outstanding period targets, stopping on ambiguous payment.

        Pages:
            in: page_knights_shop (a stale purchase popup is canceled).
            out: page_knights_shop, with every enabled item checked.
        """
        pending = dict((item.option, (item, target)) for item, target in purchases)
        # Observations spanning a refresh stay in the entry period. Tagging
        # them with the later completion time could skip a newly reset quota.
        checked_at = datetime.now()
        attempt = None
        previous = None
        scroll_origin = None
        # A fresh tab entry already starts at the list head. Drag left to
        # reveal later cards; probing right first was redundant and reversed
        # the expected first action in the user's real entry log.
        backwards = False
        may_have_reordered = False
        stationary_probes = 0
        swipes = 0
        deadline = Timer(30, count=100).start()
        action = Timer(2, count=0)
        # Navigation recognizes the header before the product body settles.
        # The normal action timer allows a fast first click; borrowing that
        # for search caused a swipe 0.36s after tab arrival and a game fling
        # to the tail. Entry/re-entry never issues a reset gesture. Start the
        # search interval explicitly, require real card images and stationary
        # fresh frames, then only search when pending targets are off screen.
        scroll_interval = Timer(2, count=0).start()
        quantity_interval = Timer(self.QUANTITY_CLICK_INTERVAL_SECONDS, count=0)
        quantity_settle = Timer(self.QUANTITY_POST_CLICK_SETTLE_SECONDS, count=2).clear()

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            network = self._network_visible()
            on_shop = self._on_shop() and not network
            if on_shop and not pending and attempt is None:
                return True
            if deadline.reached():
                self._fail('state did not settle; purchase result is unverified')

            final_popup = self.appear(SHOP_PURCHASE_FINAL_CHECK) if not network else False
            if attempt is not None and not final_popup:
                attempt.final_evidence = None
            popup = self._popup() if not network and not final_popup else None
            if final_popup:
                previous = None
                if attempt is not None:
                    attempt.evidence = None
                if attempt is None:
                    # Never inherit a manually opened or stale final dialog.
                    if self.appear_then_click(SHOP_PURCHASE_FINAL_CANCEL, interval=2):
                        continue
                elif attempt.confirmed and not attempt.final_confirmed:
                    # Only the trusted first confirmation authorizes this
                    # second stage. Recognize its own dialog and buy button
                    # on two fresh frames; shared confirm handlers must not
                    # bypass these checks. Latch the final click even if the
                    # dialog persists, and require stock proof before clearing
                    # the attempt or allowing another purchase.
                    evidence = self._final_popup_evidence(attempt)
                    if evidence is not None and self.appear(SHOP_PURCHASE_FINAL_CONFIRM):
                        if attempt.final_evidence == evidence and action.reached():
                            self.device.click(SHOP_PURCHASE_FINAL_CONFIRM)
                            attempt.final_confirmed = True
                            attempt.final_evidence = None
                            logger.info(f'Knights shop: {attempt.item.option} final confirmation, quantity={attempt.quantity}')
                            action.reset()
                            deadline.reset()
                            continue
                        attempt.final_evidence = evidence
                    else:
                        attempt.final_evidence = None
            elif popup:
                previous = None
                if attempt is None:
                    # A dialog present at startup has no trusted item context.
                    # Cancel it; never inherit another task/manual selection.
                    if self.appear_then_click(SHOP_PURCHASE_CANCEL, interval=2):
                        continue
                    from tasks.base.assets.assets_base_popup import POPUP_CANCEL
                    if self.appear_then_click(POPUP_CANCEL, interval=2):
                        continue
                elif not attempt.confirmed:
                    # Quantity controls stay on this dialog and can accept a
                    # short batch, like combat preparation. After any batch,
                    # keep taking screenshots for overlays/network recovery
                    # but do not OCR its intermediate counter animation. A
                    # new stable reading determines the remaining difference;
                    # the sent click count never proves the selected quantity.
                    if quantity_settle.started() and not quantity_settle.reached():
                        attempt.evidence = None
                        continue
                    if not quantity_interval.reached():
                        attempt.evidence = None
                        continue
                    confirm, multi = popup
                    evidence = self._popup_evidence(attempt, confirm, multi)
                    if evidence is not None:
                        current, total = evidence
                        selection = plan_purchase_selection('target', (current, total - current, total), attempt.quantity)
                        if selection.action != 'none':
                            controls = {'min': BUY_MIN, 'max': BUY_MAX, 'plus': BUY_TIMES_PLUS, 'minus': BUY_TIMES_MINUS}
                            button = controls[selection.action]
                            if attempt.evidence == evidence and self.appear(button):
                                if selection.action in ('plus', 'minus'):
                                    clicks = min(abs(selection.quantity - current), self.QUANTITY_BATCH_CLICK_LIMIT)
                                    logger.info(f'Knights shop: adjust quantity {current}->{selection.quantity}, clicks={clicks}')
                                    self.device.multi_click(button, n=clicks, interval=self.QUANTITY_BATCH_CLICK_INTERVAL)
                                else:
                                    self.device.click(button)
                                # Preserve the overall deadline: repeating an
                                # ineffective batch is not game-state progress.
                                attempt.evidence = None
                                quantity_interval.reset()
                                quantity_settle.reset()
                                action.reset()
                                continue
                            attempt.evidence = evidence
                        elif attempt.evidence == evidence and action.reached():
                            # Send this confirmation once. A multi-selection
                            # may open the separately validated final dialog;
                            # old selection frames must never send it again.
                            # Neither confirmation alone proves payment. Only
                            # stock reduction can complete the attempt.
                            self.device.click(confirm)
                            attempt.confirmed = True
                            # This only authorizes a later reverse search,
                            # never purchase completion. A successful purchase
                            # may shift unvisited cards behind this viewport.
                            may_have_reordered = True
                            attempt.evidence = None
                            action.reset()
                            deadline.reset()
                            continue
                        else:
                            attempt.evidence = evidence
                    else:
                        attempt.evidence = None
            elif on_shop:
                if not self._shop_content_ready():
                    previous = None
                    scroll_interval.reset()
                    if attempt is not None:
                        attempt.evidence = None
                    continue
                view = self._title_strip()
                stable = self._same_view(previous, view)
                if previous is not None and not stable:
                    # A delayed scroll can start between two probes. Its
                    # motion invalidates the earlier stationary observation
                    # even after the previous probe has already settled.
                    if stationary_probes:
                        deadline.reset()
                    stationary_probes = 0
                if not stable and attempt is not None:
                    attempt.evidence = None
                previous = view
                if scroll_origin is not None:
                    if not self._same_view(scroll_origin, view):
                        scroll_origin = None
                        stationary_probes = 0
                        deadline.reset()
                    elif stable and action.reached():
                        # No product is a fixed edge marker: manual purchases
                        # and this run's purchases can move any sold-out card
                        # to the tail. Two separately issued swipes, each
                        # followed by settled fresh frames, delimit the scan.
                        # A failed swipe cannot silently complete the task:
                        # every enabled target still needs its own stock proof.
                        stationary_probes += 1
                        scroll_origin = None
                        if stationary_probes >= 2:
                            if not backwards and may_have_reordered:
                                # Only our own purchase can invalidate the
                                # fresh-entry scan order. Search earlier cards
                                # after reaching the tail, with the completed
                                # targets and payment latches still retained.
                                backwards = True
                                may_have_reordered = False
                                stationary_probes = 0
                                swipes = 0
                                logger.info('Knights shop: tail reached after purchase; recheck earlier cards')
                            elif attempt is not None:
                                self._fail(f'pending purchase stock not verified after full scan: {attempt.item.option}')
                            else:
                                self._fail(f'enabled items missing after full scan: {list(pending)}')
                    continue
                if stable:
                    need_scroll = False
                    if attempt is not None:
                        cards = self._cards(attempt.item)
                        if cards == []:
                            # A sold-out card may have moved to either row at
                            # the tail, or login may have reset the viewport.
                            # Search again while retaining the original stock
                            # and both payment latches; never buy it again just
                            # because its previous position is now occupied.
                            attempt.evidence = None
                            need_scroll = True
                        elif cards is not None and len(cards) == 1:
                            card = cards[0]
                            if attempt.confirmed:
                                expected = attempt.before - attempt.quantity
                                if card.remaining == expected:
                                    evidence = (card.remaining,)
                                    if attempt.evidence == evidence:
                                        logger.info(f'Knights shop: {attempt.item.option} purchased {attempt.quantity}, remaining={expected}')
                                        self._record_shop_purchase(attempt.item, attempt.item.limit - expected, checked_at)
                                        pending.pop(attempt.item.option)
                                        attempt = None
                                        previous = None
                                        # Continue towards the tail first. If
                                        # pending cards were shifted earlier,
                                        # allow one reverse pass only after the
                                        # forward pass reaches its boundary.
                                        backwards = False
                                        may_have_reordered = True
                                        stationary_probes = 0
                                        swipes = 0
                                        deadline.reset()
                                    else:
                                        attempt.evidence = evidence
                                else:
                                    attempt.evidence = None
                            elif card.remaining == attempt.before and card.active and card.price == attempt.item.price and action.reached():
                                self.device.click(card.button)
                                action.reset()
                                previous = None
                        else:
                            attempt.evidence = None
                    else:
                        unresolved = False
                        for key, (item, target) in list(pending.items()):
                            cards = self._cards(item)
                            if cards is None:
                                # A visible title with unreadable stock is not
                                # absence or completion. Do not scroll it away.
                                unresolved = True
                                continue
                            if not cards:
                                continue
                            if len(cards) != 1:
                                self._fail(f'ambiguous card: {key}')
                            card = cards[0]
                            _, quantity = resolve_period_purchase_quantity(target, item.limit, card.remaining)
                            if quantity == 0:
                                self._record_shop_purchase(item, item.limit - card.remaining, checked_at)
                                pending.pop(key)
                                logger.info(f'Knights shop: {key} period target already reached')
                                deadline.reset()
                                continue
                            if not card.active or card.price != item.price:
                                unresolved = True
                                continue
                            balance = self._balance(item)
                            if balance is None:
                                unresolved = True
                                continue
                            quantity = min(quantity, balance // item.price)
                            if quantity == 0:
                                logger.info(f'Knights shop: {key} insufficient currency; check next daily run')
                                pending.pop(key)
                                deadline.reset()
                                continue
                            if action.reached():
                                attempt = PurchaseAttempt(item, card.remaining, quantity)
                                quantity_interval.clear()
                                quantity_settle.clear()
                                self.device.click(card.button)
                                action.reset()
                                deadline.reset()
                                previous = None
                            break
                        else:
                            if pending and not unresolved:
                                need_scroll = True
                    if need_scroll and action.reached() and scroll_interval.reached():
                        if swipes >= self.SCROLL_LIMIT:
                            self._fail('horizontal scan exceeded its bounded swipe budget')
                        self._swipe(backwards=backwards)
                        scroll_origin = view.copy()
                        swipes += 1
                        action.reset()
                        scroll_interval.reset()
                        previous = None
                        continue
            else:
                previous = None
                if attempt is not None:
                    attempt.evidence = None

            # Generic handlers must never consume a purchase confirmation.
            if network:
                previous = None
                if attempt is not None:
                    attempt.evidence = None
                if self.handle_network_error():
                    self._recover_shop()
                    quantity_interval.clear()
                    quantity_settle.clear()
                    # Login/navigation re-enters at the head like a normal
                    # entry. Keep the payment latches, but start forwards.
                    backwards = False
                    may_have_reordered = False
                    stationary_probes = 0
                    swipes = 0
                    scroll_origin = None
                    scroll_interval.reset()
                    deadline.reset()
                continue
            if not on_shop and not popup and not final_popup and self.handle_touch_to_close(interval=2):
                previous = None
                continue

    def run_shop(self, skip_first_screenshot=True):
        """Check weekly/monthly game stock during the existing daily guild run.

        Pages:
            in: a page registered in the shared navigation graph.
            out: page_knights_shop; disabled/completed/unsupported options keep the input page.
        """
        purchases = self._pending_shop_purchases()
        if not purchases:
            return True
        if not server.is_oversea_server(self.config.Emulator_PackageName) or server.lang != 'global_cn':
            logger.info('Knights shop: supported captures are overseas Chinese only')
            return True
        from tasks.base.page import page_knights_shop

        logger.hr('Knights shop', level=2)
        self.ui_goto(page_knights_shop, skip_first_screenshot=skip_first_screenshot)
        self._execute_shop(purchases)
        return True


class KnightsShop(KnightsShopMixin, UI):
    """Standalone compatibility entry; normal scheduling uses Knights.run_shop."""

    def run(self):
        return self.run_shop()
