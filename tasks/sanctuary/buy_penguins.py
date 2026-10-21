"""Buy full batches of penguins from the Forest of Elves growth altar."""
import re

from module.base.timer import Timer
from module.exception import GameStuckError, ScriptError
from module.logger import logger
from module.ocr.ocr import Ocr
from tasks.base.page import page_sanctuary_forest
from tasks.base.ui import UI
from tasks.sanctuary.assets.assets_sanctuary_forest_of_elves import ALTAR_OF_GROWTH
from tasks.sanctuary.assets.assets_sanctuary_buy_penguins import (
    OCR_PENGUIN_BALANCE,
    OCR_PENGUIN_QUANTITY,
    OCR_PENGUIN_TOTAL_PRICE,
    OCR_PENGUIN_UNIT_PRICE,
    PENGUIN_BUY,
    PENGUIN_CANCEL,
    PENGUIN_CONFIRM,
    PENGUIN_MAX,
    PENGUIN_PURCHASE_CHECK,
    PENGUIN_REWARD_CHECK,
    PENGUIN_REWARD_CLOSE,
    PENGUIN_SHOP_CHECK,
    PENGUIN_SHOP_CLOSE,
)


class BuyPenguins(UI):
    BATCH_SIZE = 50

    @staticmethod
    def parse_number(text):
        """Reject unreadable prices rather than silently treating them as zero."""
        text = str(text).strip().replace(' ', '')
        if not re.fullmatch(r'(?:\d+|\d{1,3}(?:,\d{3})+)', text):
            return None
        return int(text.replace(',', ''))

    @staticmethod
    def parse_quantity(text):
        match = re.fullmatch(r'\s*(\d{1,2})\s*/\s*(\d{1,2})\s*', str(text))
        if match:
            current, maximum = map(int, match.groups())
            if 1 <= current <= maximum <= 50:
                return current, maximum
        return None

    def _read_number(self, asset):
        # Keep raw model output in logs and validate the complete string. A
        # failed OCR result must never become a free price or empty balance.
        text = Ocr(asset, lang=self._ocr_lang()).ocr_single_line(self.device.image)
        logger.attr(asset.name, text)
        return self.parse_number(text)

    def _read_shop_values(self):
        balance = self._read_number(OCR_PENGUIN_BALANCE)
        unit = self._read_number(OCR_PENGUIN_UNIT_PRICE)
        if balance is not None and unit is not None and unit > 0:
            return balance, unit
        return None

    def _read_purchase_values(self):
        text = Ocr(OCR_PENGUIN_QUANTITY, lang=self._ocr_lang()).ocr_single_line(self.device.image)
        logger.attr('PenguinQuantity', text)
        return self.parse_quantity(text), self._read_number(OCR_PENGUIN_TOTAL_PRICE)

    def _ocr_lang(self):
        language = self.config.Emulator_GameLanguage
        return 'cn' if language in ('cn', 'auto', '', None) else language

    def _shop_is_ready(self):
        return (self.appear(PENGUIN_SHOP_CHECK)
                and PENGUIN_SHOP_CHECK.match_color(self.device.image))

    def _forest_is_ready(self):
        return (self.appear(ALTAR_OF_GROWTH)
                and ALTAR_OF_GROWTH.match_color(self.device.image))

    def buy_batches(self, batches, skip_first_screenshot=True):
        """Buy exactly 50 penguins per settled batch, then dismiss the altar.

        Args:
            batches (int): Positive number of full batches to buy.
            skip_first_screenshot (bool): Reuse the caller's prepared frame.

        Returns:
            int: Batches confirmed by an exact debit; may stop on low balance.

        Pages:
            in: page_sanctuary_forest, growth altar or penguin purchase overlay
            out: page_sanctuary_forest, without a purchase/reward overlay
        """
        if type(batches) is not int or batches < 1:
            raise ScriptError('Penguin batch count must be a positive integer')
        completed = 0
        stop_reason = None
        timeout = Timer(30, count=30).start()
        baseline = None
        submitted = False
        stable_values = None
        stable_count = 0
        debit_count = 0
        self.completed_batches = 0
        self.stop_reason = None
        self.interval_clear([PENGUIN_BUY, PENGUIN_MAX, PENGUIN_CONFIRM,
                             PENGUIN_CANCEL, PENGUIN_REWARD_CLOSE, PENGUIN_SHOP_CLOSE,
                             ALTAR_OF_GROWTH])
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # Only a usable forest frame proves every overlay was dismissed.
            if (completed >= batches or stop_reason) and self._forest_is_ready():
                self.stop_reason = stop_reason
                logger.info(f'Penguins: {completed}/{batches} batches, reason={stop_reason or "complete"}')
                return completed
            if timeout.reached():
                logger.error('Penguin purchase made no verified progress within 30 seconds')
                raise GameStuckError('Penguin purchase or settlement timed out')

            if self._shop_is_ready():
                values = self._read_shop_values()
                if baseline is not None and submitted:
                    # The original balance and unit price belong to exactly
                    # one payment. Retrying controls never replaces them or
                    # resets its deadline. A reward, click or unchanged shop
                    # frame cannot settle payment. Two consecutive exact debit
                    # readings allow counting even when the reward was missed.
                    expected = (baseline[0] - baseline[1] * self.BATCH_SIZE, baseline[1])
                    debit_count = debit_count + 1 if values == expected else 0
                    if debit_count >= 2:
                        completed += 1
                        self.completed_batches = completed
                        logger.info(f'Penguin batch settled: {completed}/{batches}')
                        baseline = None
                        submitted = False
                        debit_count = 0
                        stable_values = None
                        stable_count = 0
                        timeout.reset()
                        self.interval_clear([PENGUIN_BUY, PENGUIN_MAX, PENGUIN_CONFIRM])
                    else:
                        continue
                if completed >= batches or stop_reason:
                    self.appear_then_click(PENGUIN_SHOP_CLOSE, interval=2)
                    continue
                if baseline is None:
                    stable_count = stable_count + 1 if values is not None and values == stable_values else 1
                    stable_values = values
                    if values is None or stable_count < 2:
                        continue
                    if values[0] < values[1] * self.BATCH_SIZE:
                        stop_reason = 'insufficient_stigma'
                        continue
                    baseline = values
                    timeout.reset()
                if self.appear_then_click(PENGUIN_BUY, interval=2):
                    continue

            else:
                # Consecutive observations cannot span overlays, unreadable
                # frames or a network interruption. The payment baseline stays
                # intact, but evidence for stable balances must start again.
                stable_values = None
                stable_count = 0
                debit_count = 0

            if self.appear(PENGUIN_PURCHASE_CHECK):
                if baseline is None or stop_reason:
                    # A manually opened dialog has no known pre-payment
                    # balance. Cancel it before starting a measured batch.
                    self.appear_then_click(PENGUIN_CANCEL, interval=2)
                    continue
                quantity, total_price = self._read_purchase_values()
                if quantity is None or total_price is None:
                    continue
                if quantity[1] < self.BATCH_SIZE:
                    stop_reason = 'insufficient_stigma'
                    self.appear_then_click(PENGUIN_CANCEL, interval=2)
                    continue
                if quantity[0] < self.BATCH_SIZE:
                    if submitted:
                        raise GameStuckError('Penguin quantity changed after confirmation')
                    self.appear_then_click(PENGUIN_MAX, interval=2)
                    continue
                if total_price != baseline[1] * self.BATCH_SIZE:
                    # A changed price invalidates the quoted batch. Cancel and
                    # establish a fresh shop baseline before another attempt.
                    if submitted:
                        raise GameStuckError('Penguin price changed after confirmation')
                    if self.appear_then_click(PENGUIN_CANCEL, interval=2):
                        baseline = None
                    continue
                if self.appear_then_click(PENGUIN_CONFIRM, interval=2):
                    submitted = True
                continue

            if self.appear(PENGUIN_REWARD_CHECK):
                self.appear_then_click(PENGUIN_REWARD_CLOSE, interval=2)
                continue
            if self._forest_is_ready() and baseline is None:
                self.appear_then_click(ALTAR_OF_GROWTH, interval=2)
                continue
            if self.handle_network_error():
                continue

    def run(self):
        """Run one manually requested batch set using the normal page graph."""
        language = self.config.Emulator_GameLanguage
        if language not in ('cn', 'auto', '', None):
            logger.info('Penguin purchases currently support Chinese clients only')
            return 0
        batches = self.config.BuyPenguins_BatchCount
        if type(batches) is not int or batches < 1:
            raise ScriptError('Penguin batch count must be a positive integer')
        if not self.device.app_is_running():
            from tasks.login.login import Login
            Login(self.config, device=self.device).app_start()
        self.device.screenshot()
        if not (self._forest_is_ready() or self._shop_is_ready()
                or self.appear(PENGUIN_PURCHASE_CHECK) or self.appear(PENGUIN_REWARD_CHECK)):
            self.ui_goto(page_sanctuary_forest)
        logger.hr(f'Buy penguins: {batches} batches of {self.BATCH_SIZE}', level=1)
        return self.buy_batches(batches)
