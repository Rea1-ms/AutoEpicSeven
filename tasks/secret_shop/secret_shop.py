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
from module.base.button import ClickButton
from module.base.timer import Timer
from module.logger import logger
from tasks.base.popup import PopupHandler

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
    REFRESH,
    REFRESH_CONFIRM,
)


class SecretShop(PopupHandler):
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

    def __init__(self, config, device):
        super().__init__(config, device)
        # 统计
        self.covenant_bought = 0
        self.mystic_bought = 0
        self.refresh_count = 0
        # 配置
        self.max_refresh = getattr(config, 'SecretShop_MaxRefresh', 100)
        self.buy_covenant = getattr(config, 'SecretShop_BuyCovenantBookmark', True)
        self.buy_mystic = getattr(config, 'SecretShop_BuyMysticMedal', True)
        # 状态
        self._scrolled = False
        self._stable_count = 0
        # 当前刷新周期内是否已购买（每次刷新最多各出现一个）
        self._covenant_purchased_this_round = False
        self._mystic_purchased_this_round = False

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
            self._stable_count += 1
            logger.info(f'[Stable] scrolled={self._scrolled}, count={self._stable_count}/{self.STABLE_THRESHOLD}')
            if self._stable_count >= self.STABLE_THRESHOLD:
                return True
        else:
            if self._stable_count > 0:
                logger.info(f'[Stable] 重置计数 (之前={self._stable_count})')
            self._stable_count = 0
        return False

    def _reset_stable(self):
        """重置稳定计数器，用于滚动/刷新/购买后"""
        self._stable_count = 0

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
        if self.buy_covenant and not self._covenant_purchased_this_round:
            covenant_matches = covenant_asset.match_multi_template(image)
            if covenant_matches:
                logger.info(f'[Scan] covenant_matches={len(covenant_matches)}')
                for i, m in enumerate(covenant_matches):
                    logger.info(f'[Scan]   covenant[{i}]: Y={int((m.area[1] + m.area[3]) / 2)}')
            for item in covenant_matches:
                targets.append(('covenant', item))
        if self.buy_mystic and not self._mystic_purchased_this_round:
            mystic_matches = mystic_asset.match_multi_template(image)
            if mystic_matches:
                logger.info(f'[Scan] mystic_matches={len(mystic_matches)}')
                for i, m in enumerate(mystic_matches):
                    logger.info(f'[Scan]   mystic[{i}]: Y={int((m.area[1] + m.area[3]) / 2)}')
            for item in mystic_matches:
                targets.append(('mystic', item))

        if not targets:
            return []

        # 按 Y 坐标匹配物品和购买按钮
        for item_type, item in targets:
            item_y = (item.area[1] + item.area[3]) / 2

            for buy_btn in buy_buttons:
                buy_y = (buy_btn.area[1] + buy_btn.area[3]) / 2

                # Y 中心点距离在 50 像素内认为是同一行
                if abs(item_y - buy_y) < 50:
                    result.append((item_type, buy_btn))
                    break

        return result

    def handle_buy_confirm(self, skip_first_screenshot=True) -> bool:
        """
        处理购买确认弹窗

        标准 handle 系方法：
        - 返回 True = 购买成功
        - 返回 False = 超时或失败

        Pages:
            in: BUY_CONFIRM popup
            out: page_secret_shop
        """
        timeout = Timer(3, count=6).start()
        clicked = False

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 超时退出
            if timeout.reached():
                logger.warning('购买确认超时')
                return False

            # 点击确认购买
            if self.appear_then_click(BUY_CONFIRM, interval=2):
                clicked = True
                continue

            # 正面条件退出：曾点击过确认，且确认按钮消失
            if clicked and not self.appear(BUY_CONFIRM):
                return True

        return False

    def run(self, skip_first_screenshot=False):
        """
        主运行逻辑

        使用 ALAS 标准状态循环模式：
        - 截图 -> 退出条件 -> 弹窗处理 -> 稳定检测 -> 扫描购买 -> 滚动 -> 刷新
        - 无 sleep 依赖
        - 分离 TOP/BOTTOM 区域，避免重复匹配
        - 使用 *_STABLE assets 固定位置检测稳定

        Pages:
            in: page_secret_shop
            out: page_secret_shop
        """
        logger.hr('秘密商店刷书签', level=1)
        logger.info(f'最大刷新次数: {self.max_refresh}')
        logger.info(f'购买圣约书签: {self.buy_covenant}')
        logger.info(f'购买神秘奖牌: {self.buy_mystic}')

        # 超时保护
        timeout = Timer(60, count=120).start()

        while 1:
            # 1. 截图
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 2. 退出条件（不带 interval，不带操作）
            if timeout.reached():
                logger.warning('运行超时，停止')
                break
            if self.refresh_count >= self.max_refresh:
                logger.info('达到最大刷新次数')
                break

            # 3. 优先处理弹窗
            if self.appear_then_click(BUY_CONFIRM, interval=2):
                timeout.reset()
                continue

            if self.appear(REFRESH_CONFIRM):
                if self.appear_then_click(REFRESH_CONFIRM, interval=2):
                    # 刷新完成后重置状态
                    self._scrolled = False
                    self._covenant_purchased_this_round = False
                    self._mystic_purchased_this_round = False
                    self._reset_stable()
                    timeout.reset()
                continue

            # 3.1 刷新完概率触发网络不稳定提示，处理后继续
            if self.handle_network_error():
                continue

            # 4. 稳定检测（必须通过才能继续扫描）
            if not self._is_shop_stable():
                continue

            # 5. 扫描并购买目标物品
            targets = self._find_target_buy_buttons()
            if targets:
                item_type, buy_btn = targets[0]
                logger.info(f'找到 {item_type}: area={buy_btn.area}')
                self.device.click(buy_btn)

                # 清除子状态机共用 assets 的 interval
                self.interval_clear(BUY_CONFIRM)

                # 进入子状态机处理购买确认
                if self.handle_buy_confirm():
                    if item_type == 'covenant':
                        self.covenant_bought += 1
                        self._covenant_purchased_this_round = True
                        logger.info(f'购买圣约书签成功 (累计: {self.covenant_bought})')
                    else:
                        self.mystic_bought += 1
                        self._mystic_purchased_this_round = True
                        logger.info(f'购买神秘奖牌成功 (累计: {self.mystic_bought})')

                # 购买完成后画面变化，重置稳定检测
                self._reset_stable()
                timeout.reset()
                continue

            # 6. 当前页面没有目标，尝试滚动
            if not self._scrolled:
                logger.info('向下滚动')
                self.device.swipe(
                    (self.SCROLL_AREA[0], self.SCROLL_AREA[1]),
                    (self.SCROLL_AREA[2], self.SCROLL_AREA[3]),
                    duration=(0.4, 0.6)
                )
                self._scrolled = True
                self._reset_stable()  # 滚动后重置稳定计数
                timeout.reset()
                continue

            # 7. 已滚动且没有目标，刷新商店
            if self.appear_then_click(REFRESH, interval=2):
                self.refresh_count += 1
                logger.info(f'刷新商店 (累计: {self.refresh_count})')
                self._reset_stable()  # 刷新后重置稳定计数
                timeout.reset()
                continue

        # 输出统计
        logger.hr('刷书签完成', level=1)
        logger.info(f'刷新次数: {self.refresh_count}')
        logger.info(f'圣约书签: {self.covenant_bought}')
        logger.info(f'神秘奖牌: {self.mystic_bought}')


# 保持向后兼容
SecretShopRefresh = SecretShop
