"""
秘密商店刷书签模块

功能:
    - 自动刷新秘密商店
    - 识别圣约书签和神秘奖牌
    - 自动购买并处理确认弹窗
    - 滚动列表查看所有商品
    - 统计购买数量

设计:
    - 未滑动状态: 使用 *_TOP assets 扫描 1-4 行
    - 滑动后状态: 使用 *_BOTTOM assets 扫描 5-6 行
    - 稳定检测: BUY_TOP_STABLE (第4个) / BUY_BOTTOM_STABLE (第6个) 固定位置连续帧确认
    - 不存在重复匹配问题: TOP 和 BOTTOM 搜索区域完全分离

Pages:
    in: page_secret_shop
    out: page_secret_shop
"""
import datetime
import re
import statistics
from copy import copy

from module.base.button import ClickButton
from module.base.timer import Timer
from module.base.utils import color_similar, get_color
from module.exception import GameStuckError, RequestHumanTakeover
from module.logger import logger
from module.ocr.ocr import Duration, OcrWhiteLetterOnComplexBackground
from tasks.base.popup import PopupHandler
from tasks.base.page import page_secret_shop
from tasks.base.resource_bar import (
    RESOURCE_BAR_LAYOUT_SECRET_SHOP, RESOURCE_BAR_SPECS, ResourceBarMixin, ResourceBarValue,
    get_resource_bar_segment_area,
)
from tasks.base.assets.assets_base_popup import NETWORK_ERROR_DISCONNECT
from tasks.base.ui import UI
from tasks.base.assets.assets_base_resource_bar import OCR_RESOURCE_BAR, SKYSTONE_ICON
from tasks.dungeon.assets.assets_dungeon_repeat_common import REPEAT_COMBAT_CHECK
from tasks.secret_shop.payment import ITEM_GOLD_COST, ShopPayment
from tasks.secret_shop.recognition import ShopCurrencyOcr, ShopGoods, same_currency_image

from tasks.secret_shop.assets.assets_secret_shop import (
    BUY_TOP,
    BUY_TOP_STABLE,
    BUY_BOTTOM,
    BUY_BOTTOM_STABLE,
    BUY_CONFIRM,
    COVENANT_BOOKMARK_TOP,
    COVENANT_BOOKMARK_BOTTOM,
    MYSTIC_MEDAL_TOP,
    MYSTIC_MEDAL_BOTTOM,
    OCR_AUTO_REFRESH,
    REFRESH,
    REFRESH_CONFIRM,
    SECRET_SHOP_CHECK,
    SECRET_SHOP_GOLD_ICON,
)


class SecretShopRefreshDuration(OcrWhiteLetterOnComplexBackground, Duration):
    def after_process(self, result):
        result = Duration.after_process(self, result)
        result = result.replace('自动刷新', '')
        result = result.replace('后', '')
        result = result.replace(' ', '')
        result = result.replace('：', ':')
        result = re.sub(r'(\d{1,2})时(?![间辰])', r'\1小时', result)
        result = re.sub(r'(\d{1,2})分(?![钟贝])', r'\1分钟', result)
        result = re.sub(r'(\d{1,2})\s*hours?', r'\1h', result, flags=re.IGNORECASE)
        result = re.sub(r'(\d{1,2})\s*minutes?', r'\1m', result, flags=re.IGNORECASE)
        result = re.sub(r'(\d{1,2})\s*seconds?', r'\1s', result, flags=re.IGNORECASE)
        return result


class SecretShop(ResourceBarMixin, PopupHandler):
    """
    秘密商店刷书签

    使用 ALAS 标准状态循环模式。
    分离 TOP/BOTTOM 区域，避免重复匹配和动画干扰。
    使用 *_STABLE assets 固定位置检测画面稳定。
    """

    # 滚动参数
    SCROLL_AREA = (960, 550, 960, 300)
    # 稳定检测：连续 N 帧检测到固定位置 BUY 按钮才认为稳定
    STABLE_THRESHOLD = 2
    AUTO_REFRESH_SAMPLE_COUNT = 3
    AUTO_REFRESH_MIN_VALID_SAMPLES = 2
    AUTO_REFRESH_SAMPLE_SPREAD_SECONDS = 90
    AUTO_REFRESH_MAX_SECONDS = 3660
    AUTO_REFRESH_BUFFER_SECONDS = 65
    AUTO_REFRESH_SHORT_BUFFER_SECONDS = 10
    AUTO_REFRESH_FALLBACK_MINUTES = 10
    ITEM_TEMPLATE_SIMILARITY = 0.85
    ITEM_COLOR_THRESHOLD = 30

    def __init__(self, config, device):
        super().__init__(config, device)
        # 统计
        self.covenant_bought = 0
        self.mystic_bought = 0
        self.refresh_count = 0
        # 配置
        self.only_free = getattr(config, 'SecretShop_OnlyFree', True)
        self.max_refresh = getattr(config, 'SecretShop_MaxRefresh', 10)
        self.buy_covenant = getattr(config, 'SecretShop_BuyCovenantBookmark', True)
        self.buy_mystic = getattr(config, 'SecretShop_BuyMysticMedal', True)
        # Free-only scans the current assortment according to the buy toggles.
        # Its hidden goal remains saved for later paid runs, but must not make
        # a zero target silently disable buying or resume a paid goal session.
        self.completion_mode = getattr(config, 'SecretShop_CompletionMode', 'RefreshCount')
        if self.only_free:
            self.completion_mode = 'RefreshCount'
        self.target_covenant = max(0, int(getattr(config, 'SecretShop_TargetCovenant', 10)))
        self.target_mystic = max(0, int(getattr(config, 'SecretShop_TargetMystic', 10)))
        # 状态
        self._scrolled = False
        self._stable_count = 0
        self._payment: ShopPayment | None = None
        self._payment_timer = Timer(45, count=30)
        self._currency_candidate = None
        self._currency_observed = None
        self._currency_value = None
        self._goods_before_refresh = None
        self._goods_candidate = None
        self._refresh_sky_image = None
        self._refresh_fallback = Timer(3)
        self._known_gold = None
        # 当前刷新周期内是否已购买（每次刷新最多各出现一个）
        self._covenant_purchased_this_round = False
        self._mystic_purchased_this_round = False

    def _refresh_ocr_lang(self) -> str:
        lang = getattr(self.config, 'Emulator_GameLanguage', 'cn')
        if lang in ('auto', '', None, 'cn', 'global_cn', 'zh', 'zh_cn'):
            return 'cn'
        if lang in ('en', 'global_en', 'en_us'):
            return 'en'
        return 'cn'

    @staticmethod
    def _is_valid_auto_refresh_duration(remain: datetime.timedelta) -> bool:
        seconds = int(remain.total_seconds())
        return 0 < seconds <= SecretShop.AUTO_REFRESH_MAX_SECONDS

    @staticmethod
    def _is_second_precision_duration(text: str) -> bool:
        lower = text.lower()
        return '秒' in text or ':' in text or re.search(r'\d\s*s\b', lower) is not None

    def _ocr_auto_refresh_remaining_once(self) -> tuple[str, datetime.timedelta]:
        lang = self._refresh_ocr_lang()
        text = OcrWhiteLetterOnComplexBackground(
            OCR_AUTO_REFRESH, lang=lang, name='SecretShopAutoRefreshText'
        ).ocr_single_line(self.device.image)
        parser = SecretShopRefreshDuration(
            OCR_AUTO_REFRESH, lang=lang, name='SecretShopAutoRefreshDuration'
        )
        normalized = parser.after_process(text)
        remain = parser.format_result(normalized)
        logger.info(f'Secret shop auto refresh OCR: {text} -> {normalized} -> {remain}')
        return normalized, remain

    def _ocr_auto_refresh_remaining(self, skip_first_screenshot=True) -> tuple[datetime.timedelta | None, bool]:
        samples: list[tuple[int, str]] = []

        for index in range(self.AUTO_REFRESH_SAMPLE_COUNT):
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            text, remain = self._ocr_auto_refresh_remaining_once()
            if not self._is_valid_auto_refresh_duration(remain):
                logger.warning(f'Secret shop auto refresh OCR invalid: {text} -> {remain}')
                continue

            samples.append((int(remain.total_seconds()), text))

        if len(samples) < self.AUTO_REFRESH_MIN_VALID_SAMPLES:
            logger.warning(f'Secret shop auto refresh OCR has insufficient samples: {samples}')
            return None, False

        median_seconds = int(statistics.median(seconds for seconds, _ in samples))
        cluster = [
            (seconds, text)
            for seconds, text in samples
            if abs(seconds - median_seconds) <= self.AUTO_REFRESH_SAMPLE_SPREAD_SECONDS
        ]
        if len(cluster) < self.AUTO_REFRESH_MIN_VALID_SAMPLES:
            logger.warning(f'Secret shop auto refresh OCR samples diverged: {samples}')
            return None, False

        remain_seconds = int(statistics.median(seconds for seconds, _ in cluster))
        second_precision = any(self._is_second_precision_duration(text) for _, text in cluster)
        remain = datetime.timedelta(seconds=remain_seconds)
        logger.info(
            f'Secret shop next auto refresh confirmed: {remain} '
            f'(samples={samples}, cluster={cluster}, second_precision={second_precision})'
        )
        return remain, second_precision

    def _delay_to_auto_refresh(self):
        remain, second_precision = self._ocr_auto_refresh_remaining(skip_first_screenshot=True)
        if remain is None:
            logger.warning(
                f'Secret shop auto refresh OCR failed, fallback to {self.AUTO_REFRESH_FALLBACK_MINUTES} minutes'
            )
            self.config.task_delay(minute=self.AUTO_REFRESH_FALLBACK_MINUTES)
            return

        buffer_seconds = (
            self.AUTO_REFRESH_SHORT_BUFFER_SECONDS
            if second_precision
            else self.AUTO_REFRESH_BUFFER_SECONDS
        )
        target = datetime.datetime.now() + remain + datetime.timedelta(seconds=buffer_seconds)
        logger.info(
            f'Secret shop delay to auto refresh: remain={remain}, '
            f'buffer={buffer_seconds}s, target={target.replace(microsecond=0)}'
        )
        self.config.task_delay(target=target)

    def _is_shop_stable(self) -> bool:
        """
        检测商店画面是否稳定

        使用固定位置的 *_STABLE assets:
        - 未滑动: BUY_TOP_STABLE (第4个位置，search 范围小)
        - 已滑动: BUY_BOTTOM_STABLE (第6个位置，search 范围小)

        连续 STABLE_THRESHOLD 帧检测到才认为稳定，避免动画中误判。

        Returns:
            bool: 画面是否稳定
        """
        stable_asset = BUY_BOTTOM_STABLE if self._scrolled else BUY_TOP_STABLE

        if self.appear(stable_asset):
            if self._stable_count < self.STABLE_THRESHOLD:
                self._stable_count += 1
                logger.info(f'[Stable] scrolled={self._scrolled}, count={self._stable_count}/{self.STABLE_THRESHOLD}')
            if self._stable_count >= self.STABLE_THRESHOLD:
                return True
        else:
            if self._stable_count > 0:
                logger.info(f'[Stable] Reset count (previous={self._stable_count})')
            self._stable_count = 0
        return False

    def _reset_stable(self):
        """重置稳定计数器，用于滚动/刷新/购买后"""
        self._stable_count = 0

    def _match_target_item(self, asset, item_type: str, image) -> ClickButton | None:
        """
        Secret shop同一种目标货同屏只会出现一份。
        这里用 match_template_color() 先定位，再用颜色过滤灰色已点击状态。
        """
        if not asset.match_template_color(
            image,
            similarity=self.ITEM_TEMPLATE_SIMILARITY,
            threshold=self.ITEM_COLOR_THRESHOLD,
        ):
            return None

        return ClickButton(
            area=asset.button,
            button=asset.button,
            name=f'{item_type}_item',
        )

    def _find_target_buy_buttons(self) -> list[tuple[str, ClickButton]]:
        """
        查找当前页面中目标物品对应的购买按钮

        根据 _scrolled 状态使用不同的 assets:
        - 未滑动: *_TOP (1-4 行)
        - 已滑动: *_BOTTOM (5-6 行)

        Returns:
            list[tuple[str, ClickButton]]: [(item_type, buy_button), ...]
        """
        image = self.device.image
        result = []

        # 根据滑动状态选择 assets
        if self._scrolled:
            buy_asset = BUY_BOTTOM
            covenant_asset = COVENANT_BOOKMARK_BOTTOM
            mystic_asset = MYSTIC_MEDAL_BOTTOM
        else:
            buy_asset = BUY_TOP
            covenant_asset = COVENANT_BOOKMARK_TOP
            mystic_asset = MYSTIC_MEDAL_TOP

        # 获取当前区域的所有购买按钮
        buy_buttons = buy_asset.match_multi_template(image)

        # Debug 日志
        logger.info(f'[Scan] scrolled={self._scrolled}, buy_buttons={len(buy_buttons) if buy_buttons else 0}')
        if buy_buttons:
            for i, btn in enumerate(buy_buttons):
                logger.info(f'[Scan]   buy[{i}]: Y={int((btn.area[1] + btn.area[3]) / 2)}')

        if not buy_buttons:
            return []

        # 查找目标物品（跳过本轮已购买的类型）
        targets = []
        if self._needs_item('covenant') and not self._covenant_purchased_this_round:
            covenant_item = self._match_target_item(covenant_asset, 'covenant', image)
            if covenant_item:
                logger.info(
                    f'[Scan] covenant_match: area={covenant_item.area}, '
                    f'button={covenant_item.button}'
                )
                targets.append(('covenant', covenant_item))
        if self._needs_item('mystic') and not self._mystic_purchased_this_round:
            mystic_item = self._match_target_item(mystic_asset, 'mystic', image)
            if mystic_item:
                logger.info(
                    f'[Scan] mystic_match: area={mystic_item.area}, '
                    f'button={mystic_item.button}'
                )
                targets.append(('mystic', mystic_item))

        if not targets:
            return []

        # 按 Y 坐标匹配物品和购买按钮
        for item_type, item in targets:
            item_y = (item.area[1] + item.area[3]) / 2

            for buy_btn in buy_buttons:
                buy_y = (buy_btn.area[1] + buy_btn.area[3]) / 2

                # Y 中心点距离在 50 像素内认为是同一行
                if abs(item_y - buy_y) < 50 and color_similar(
                    get_color(image, buy_btn.area), buy_asset.color, threshold=30,
                ):
                    result.append((item_type, buy_btn))
                    break

        return result

    def handle_buy_confirm(self, skip_first_screenshot=True) -> bool:
        """Retry the visible confirmation; a click is not proof of settlement."""
        if not skip_first_screenshot:
            self.device.screenshot()
        payment = self._payment
        if payment is None or payment.kind == 'refresh':
            return False
        if self.appear(BUY_CONFIRM, interval=2):
            # Only entering this phase starts a new deadline. Repeated clicks
            # must not postpone stuck recovery indefinitely.
            if not payment.submitted:
                self._payment_timer.reset()
            payment.submitted = True
            self._reset_currency_observation()
            self.device.click(BUY_CONFIRM)
            return True
        return False

    def handle_refresh_confirm(self) -> bool:
        """Retry while the current refresh confirmation remains visible."""
        payment = self._payment
        if payment is None or payment.kind != 'refresh':
            return False
        if self.appear(REFRESH_CONFIRM, interval=2):
            if not payment.submitted:
                self._payment_timer.reset()
                self._refresh_fallback.reset()
            payment.submitted = True
            # A refresh returns the list to its top. This is a recognition
            # expectation only; bought flags/counts remain unchanged until
            # changed goods or the exact debit confirms this refresh.
            self._scrolled = False
            self._reset_stable()
            self.device.click(REFRESH_CONFIRM)
            return True
        return False

    def _shop_is_ready(self) -> bool:
        # The identity marker is covered by purchase/refresh dialogs. Do not
        # require a green refresh button: the final debit may exhaust stones.
        return self.match_template_color(SECRET_SHOP_CHECK)

    def _read_shop_balance(self) -> tuple[int, int] | None:
        values = tuple(self._read_shop_currency(key) for key in RESOURCE_BAR_LAYOUT_SECRET_SHOP)
        return values if all(value is not None for value in values) else None

    def _currency_image(self, key):
        icons = {'gold': SECRET_SHOP_GOLD_ICON, 'skystone': SKYSTONE_ICON}
        offsets, _ = self._match_resource_bar_icons(RESOURCE_BAR_LAYOUT_SECRET_SHOP, icons=icons)
        if len(offsets) != 2:
            return None
        area = get_resource_bar_segment_area(
            RESOURCE_BAR_LAYOUT_SECRET_SHOP, key, offsets, icons, {'gold': -3},
        )
        # Copy the existing marker detector: broadening a global asset's search
        # would leak shop-specific geometry into the combat navigator. Its glow
        # extends left of the tiny central template, so crop before that glow,
        # rather than stripping an OCR suffix that might itself resemble digits.
        repeat_marker = copy(REPEAT_COMBAT_CHECK.matched_button)
        repeat_marker.load_search(OCR_RESOURCE_BAR.area)
        if key == 'skystone' and repeat_marker.match_template_luma(self.device.image):
            area = (area[0], area[1], min(area[2], repeat_marker.button[0] - 36), area[3])
        if area[2] <= area[0]:
            return None
        return self.image_crop(area).copy()

    def _read_shop_currency(self, key, image=None):
        if image is None:
            image = self._currency_image(key)
        if image is None:
            return None
        ocr = ShopCurrencyOcr(OCR_RESOURCE_BAR, lang=self._ocr_lang(), name=f'SecretShop.{key}')
        text = ocr.ocr_single_line(image, direct_ocr=True)
        value = ocr.parse_amount(text)
        if value is None:
            logger.warning(f'Secret shop rejects malformed {key} amount: {text!r}')
        return value

    def _reset_currency_observation(self):
        self._currency_candidate = None
        self._currency_observed = None
        self._currency_value = None
        self.interval_clear('secret_shop_currency', interval=1)

    def _poll_currency(self, key):
        image = self._currency_image(key)
        stable = same_currency_image(self._currency_candidate, image)
        self._currency_candidate = image
        if not stable:
            return None
        if self._currency_value is not None and same_currency_image(self._currency_observed, image):
            return self._currency_value
        if not self.interval_is_reached('secret_shop_currency', interval=1):
            return None
        self.interval_reset('secret_shop_currency', interval=1)
        self._currency_observed = image
        self._currency_value = self._read_shop_currency(key, image=image)
        return self._currency_value

    def _capture_goods(self):
        return ShopGoods.capture(self.device.image)

    def _needs_item(self, kind):
        # Buying is controlled only by the toggles. A zero or already reached
        # target excludes no purchases while another positive goal is pending;
        # the all-goals exit below is checked only after the current assortment
        # has been scanned through its bottom, before opening another refresh.
        return self.buy_covenant if kind == 'covenant' else self.buy_mystic

    def _purchase_goals(self):
        return [(bought, target) for enabled, bought, target in (
            (self.buy_covenant, self.covenant_bought, self.target_covenant),
            (self.buy_mystic, self.mystic_bought, self.target_mystic),
        ) if enabled and target > 0]

    def _goal_complete(self):
        if self.completion_mode == 'PurchaseCount':
            goals = self._purchase_goals()
            # Zero means "not an exit goal", not "do not buy". An empty goal
            # set is invalid, rather than vacuously complete; run() rejects it
            # before navigation so this mode can never refresh without a goal.
            return bool(goals) and all(bought >= target for bought, target in goals)
        return not any(self._needs_item(kind) for kind in ITEM_GOLD_COST)

    def _goal_signature(self):
        return [self.completion_mode, self.target_covenant, self.target_mystic,
                self.buy_covenant, self.buy_mystic, self.only_free]

    def _save_progress(self):
        if self.completion_mode == 'PurchaseCount':
            self.config.SecretShopRuntime_Session = {
                'goal': self._goal_signature(), 'covenant': self.covenant_bought,
                'mystic': self.mystic_bought, 'refresh': self.refresh_count,
            }

    def handle_network_error(self, interval=5):
        if self.appear(NETWORK_ERROR_DISCONNECT):
            raise GameStuckError('Secret shop disconnected; restart before continuing')
        return super().handle_network_error(interval=interval)

    def _store_shop_balance(self, balance):
        self.write_resource_bar_status({
            key: ResourceBarValue(RESOURCE_BAR_SPECS[key], str(value), value)
            for key, value in zip(RESOURCE_BAR_LAYOUT_SECRET_SHOP, balance)
            if value is not None
        })

    def _reset_balance_evidence(self):
        self._currency_candidate = None

    def _begin_payment(self, kind, balance, button):
        # Capture the baseline BEFORE opening a dialog. Never replace it with
        # a later OCR value while the server may be applying this payment.
        self._payment = ShopPayment(kind, balance)
        self._entry_currency_image = self._currency_image('skystone' if kind == 'refresh' else 'gold')
        self._payment_timer.reset()
        self._reset_currency_observation()
        self._goods_candidate = None
        self._reset_balance_evidence()
        self.interval_clear('secret_shop_refresh_baseline', interval=1)
        self.interval_clear(BUY_CONFIRM)
        self.interval_clear(REFRESH_CONFIRM)
        self.interval_reset('secret_shop_payment_entry', interval=2)
        self.device.click(button)

    def _handle_payment_entry(self, balance=None):
        """Retry an opening click only on the unchanged, usable shop list."""
        payment = self._payment
        if payment.submitted:
            return False
        key = 'skystone' if payment.kind == 'refresh' else 'gold'
        if not same_currency_image(self._entry_currency_image, self._currency_image(key)):
            return False
        if not self.interval_is_reached('secret_shop_payment_entry', interval=2):
            return False
        if payment.kind == 'refresh':
            if not self.appear(REFRESH):
                return False
            button = REFRESH
        else:
            button = next((button for kind, button in self._find_target_buy_buttons()
                           if kind == payment.kind), None)
            if button is None:
                return False
        self.interval_reset('secret_shop_payment_entry', interval=2)
        self.device.click(button)
        return True

    def _finish_payment(self):
        payment = self._payment
        self._store_shop_balance(payment.expected)
        if payment.kind == 'refresh':
            self.refresh_count += 1
            self._scrolled = False
            self._covenant_purchased_this_round = False
            self._mystic_purchased_this_round = False
        elif payment.kind == 'covenant':
            self.covenant_bought += 1
            self._covenant_purchased_this_round = True
        else:
            self.mystic_bought += 1
            self._mystic_purchased_this_round = True
        logger.info(f'Secret shop {payment.kind} completed: {payment.before} -> {payment.expected}')
        if payment.kind != 'refresh':
            self._known_gold = payment.expected[0]
        self._save_progress()
        self._payment = None
        self._reset_balance_evidence()
        self._reset_stable()
        self._reset_currency_observation()

    def _refresh_complete(self):
        payment = self._payment
        if not payment.submitted:
            return False
        current = self._capture_goods()
        stable = self._goods_candidate is not None and self._goods_candidate.matches(current)
        self._goods_candidate = current
        if not stable:
            return False
        if self._goods_before_refresh is not None and not self._goods_before_refresh.matches(current):
            logger.info('Secret shop refresh confirmed by changed item icons')
            return True
        if not self._refresh_fallback.reached():
            return False
        if payment.before[1] is None:
            if self._refresh_sky_image is None:
                return False
            # The saved pre-refresh crop may be unreadable. Throttle failed
            # baseline reads just like the live currency observation; neither
            # failure nor retries may reset the transaction's total deadline.
            if not self.interval_is_reached('secret_shop_refresh_baseline', interval=1):
                return False
            self.interval_reset('secret_shop_refresh_baseline', interval=1)
            sky = self._read_shop_currency('skystone', image=self._refresh_sky_image)
            if sky is None:
                return False
            payment.before = (None, sky)
        sky = self._poll_currency('skystone')
        return payment.observe((None, sky)) if sky is not None else False

    def _stop_uncertain_payment(self, reason):
        logger.critical(f'Secret shop payment unresolved: {reason}; payment={self._payment}')
        # Let the scheduler save its error log and queue the existing Restart
        # task. Recovery must not depend on a manual confirmation in the UI.
        try:
            self.device.save_screenshot(genre='secret_shop_payment', interval=0)
        finally:
            raise GameStuckError(f'Secret shop payment unresolved: {reason}')

    def _run_transactions(self, skip_first_screenshot):
        timeout = Timer(60, count=120).start()
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # Settlement is checked before actions. Confirmation disappearance,
            # a stable old list, or a timeout cannot release a pending payment.
            if self._payment is not None:
                ready = self._shop_is_ready() and self._is_shop_stable()
                if ready and self._payment.submitted:
                    if self._payment.kind == 'refresh':
                        complete = self._refresh_complete()
                    else:
                        gold = self._poll_currency('gold')
                        complete = gold is not None and self._payment.observe((gold, None))
                    if complete:
                        self._finish_payment()
                        timeout.reset()
                        continue
                if not ready:
                    self._currency_candidate = None
                    self._goods_candidate = None
                if self._payment_timer.reached():
                    self._stop_uncertain_payment('confirmation or debit timed out')
                if self.handle_buy_confirm() or self.handle_refresh_confirm():
                    continue
                if self.handle_network_error():
                    continue
                if ready and self._handle_payment_entry():
                    continue
                continue

            if timeout.reached():
                logger.warning('Secret shop observation timeout; defer without spending')
                self.config.task_delay(minute=10)
                return False
            if self.completion_mode != 'PurchaseCount' and self._goal_complete():
                return True
            if self.appear(BUY_CONFIRM) or self.appear(REFRESH_CONFIRM):
                self._stop_uncertain_payment('dialog without a known pre-payment balance')
            if not self._shop_is_ready() or not self._is_shop_stable():
                self._reset_balance_evidence()
                if self.handle_network_error():
                    continue
                continue

            enabled_costs = [cost for kind, cost in ITEM_GOLD_COST.items() if self._needs_item(kind)]
            if self._known_gold is not None and self._known_gold < min(enabled_costs):
                logger.info('Secret shop: insufficient gold for any enabled item')
                return True
            # Scanning an ordinary list does not require OCR. Read gold only
            # after locating an eligible item, then use that exact baseline
            # until its debit is confirmed. Stones are irrelevant to a buy.
            targets = [(kind, button) for kind, button in self._find_target_buy_buttons()
                       if self._needs_item(kind)]
            if targets:
                if not self.interval_is_reached('secret_shop_buy_balance', interval=1):
                    continue
                self.interval_reset('secret_shop_buy_balance', interval=1)
                gold = self._read_shop_currency('gold')
                if gold is None:
                    continue
                self._known_gold = gold
                self._store_shop_balance((gold, None))
                targets = [(kind, button) for kind, button in targets if gold >= ITEM_GOLD_COST[kind]]
                if not targets:
                    if gold < min(enabled_costs):
                        return True
                    # A costly enabled item does not prevent finding a cheaper
                    # target in the other rows; keep scanning this assortment.
                else:
                    kind, button = targets[0]
                    self._begin_payment(kind, (gold, None), button)
                    continue

            if not self._scrolled:
                self._goods_before_refresh = self._capture_goods()
                self.device.swipe(
                    (self.SCROLL_AREA[0], self.SCROLL_AREA[1]),
                    (self.SCROLL_AREA[2], self.SCROLL_AREA[3]),
                    duration=(0.4, 0.6)
                )
                self._scrolled = True
                self._reset_stable()
                self._reset_balance_evidence()
                timeout.reset()
                continue

            # Reaching a purchase goal must not skip eligible top rows or items
            # hidden below the fold. Exit only after a stable bottom scan finds
            # nothing else to buy and every pending debit has been confirmed.
            # This placement also prevents spending stones on a new assortment.
            if self._goal_complete():
                logger.info('Secret shop: purchase goals reached and current assortment fully scanned')
                return True
            if self.only_free or (self.completion_mode == 'RefreshCount' and self.refresh_count >= self.max_refresh):
                return True
            if not self.match_template_color(REFRESH):
                logger.info('Secret shop: refresh is unavailable')
                return True
            # Keep the stone crop, not its OCR result. Most refreshes can be
            # proved by the new assortment without reading either currency.
            self._refresh_sky_image = self._currency_image('skystone')
            self._begin_payment('refresh', (None, None), REFRESH)

    def run(self, skip_first_screenshot=False):
        """Buy and refresh using one screenshot-first transaction loop.

        Pages:
            in: page_secret_shop (navigation prepares it when necessary)
            out: page_secret_shop; stalled payment uses scheduler restart recovery
        """
        if self.completion_mode == 'PurchaseCount' and not self._purchase_goals():
            message = 'Secret shop: select at least one item with a positive purchase target'
            logger.critical(message)
            raise RequestHumanTakeover(message)
        if not self.device.app_is_running():
            from tasks.login.login import Login
            Login(self.config, device=self.device).app_start()
        UI(self.config, device=self.device).ui_goto(page_secret_shop)
        if self.completion_mode == 'PurchaseCount':
            session = getattr(self.config, 'SecretShopRuntime_Session', {})
            if session.get('goal') == self._goal_signature():
                self.covenant_bought = session.get('covenant', 0)
                self.mystic_bought = session.get('mystic', 0)
                self.refresh_count = session.get('refresh', 0)
            self._save_progress()
        logger.hr('Secret Shop Bookmark Farming', level=1)
        logger.info(f'Secret shop goal: mode={self.completion_mode}, refresh={self.max_refresh}, '
                    f'covenant={self.target_covenant}, mystic={self.target_mystic}; free only: {self.only_free}')
        result = self._run_transactions(skip_first_screenshot)
        logger.info(f'Secret shop finished: refresh={self.refresh_count}, '
                    f'covenant={self.covenant_bought}, mystic={self.mystic_bought}')
        if result:
            if self.completion_mode == 'PurchaseCount':
                self.config.SecretShopRuntime_Session = {}
            self._delay_to_auto_refresh()
        return result


# 保持向后兼容
SecretShopRefresh = SecretShop
