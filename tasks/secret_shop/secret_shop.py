"""
秘密商店刷书签模块

功能:
    - 自动刷新秘密商店
    - 识别圣约书签和神秘奖牌
    - 自动购买并处理确认弹窗
    - 滚动列表查看所有商品
    - 统计购买数量

Pages:
    in: page_secret_shop
    out: page_secret_shop
"""
from module.base.button import ClickButton
from module.base.base import ModuleBase
from module.base.timer import Timer
from module.logger import logger

from tasks.secret_shop.assets.assets_secret_shop import (
    BUY,
    BUY_CONFIRM,
    COVENANT_BOOKMARK,
    MYSTIC_MEDAL,
    REFRESH,
    REFRESH_CONFIRM,
)


class SecretShop(ModuleBase):
    """
    秘密商店刷书签

    """

    # 滚动参数
    SCROLL_AREA = (960, 550, 960, 360)

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
        self._pending_buy: list[ClickButton] = []
        self._scrolled = False
        self._current_round_bought = 0
        # 当前刷新周期内是否已购买（每次刷新最多各出现一个）
        self._covenant_purchased_this_round = False
        self._mystic_purchased_this_round = False

    def _find_target_buy_buttons(self) -> list[tuple[str, ClickButton]]:
        """
        查找当前页面中目标物品对应的购买按钮

        Returns:
            list[tuple[str, ClickButton]]: [(item_type, buy_button), ...]
        """
        image = self.device.image
        result = []

        # 获取所有购买按钮
        buy_buttons = BUY.match_multi_template(image)
        if not buy_buttons:
            return []

        # 查找目标物品（跳过本轮已购买的类型）
        targets = []
        if self.buy_covenant and not self._covenant_purchased_this_round:
            for item in COVENANT_BOOKMARK.match_multi_template(image):
                targets.append(('covenant', item))
        if self.buy_mystic and not self._mystic_purchased_this_round:
            for item in MYSTIC_MEDAL.match_multi_template(image):
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

    def _handle_buy_confirm(self, skip_first_screenshot=True) -> bool:
        """
        处理购买确认弹窗

        Args:
            skip_first_screenshot: 是否跳过第一次截图

        Returns:
            bool: 是否购买成功

        Pages:
            in: any
            out: page_secret_shop
        """
        timeout = Timer(3, count=6).start()
        confirm_timer = Timer(0.5, count=2)
        seen_confirm = False

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
            if self.appear_then_click(BUY_CONFIRM, interval=0.3):
                seen_confirm = True
                confirm_timer.reset()
                continue

            # 必须曾经看到过弹窗，且弹窗已消失
            if seen_confirm and not self.appear(BUY_CONFIRM):
                if confirm_timer.reached():
                    return True

        return False

    def _handle_refresh_confirm(self, skip_first_screenshot=True) -> bool:
        """
        处理刷新确认弹窗

        Args:
            skip_first_screenshot: 是否跳过第一次截图

        Returns:
            bool: 是否刷新成功

        Pages:
            in: any
            out: page_secret_shop
        """
        timeout = Timer(3, count=6).start()
        confirm_timer = Timer(0.5, count=2)

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 超时退出
            if timeout.reached():
                logger.warning('刷新确认超时')
                return False

            # 点击确认刷新
            if self.appear_then_click(REFRESH_CONFIRM, interval=0.3):
                confirm_timer.reset()
                continue

            # 确认弹窗消失 = 刷新完成
            if not self.appear(REFRESH_CONFIRM):
                if confirm_timer.reached():
                    return True

        return False

    def _scroll_down(self) -> bool:
        """
        向下滚动一次

        Returns:
            bool: 是否执行了滚动
        """
        if self._scrolled:
            return False

        logger.info('向下滚动')
        self.device.swipe(
            (self.SCROLL_AREA[0], self.SCROLL_AREA[1]),
            (self.SCROLL_AREA[2], self.SCROLL_AREA[3]),
            duration=(0.4, 0.6)
        )
        self._scrolled = True
        return True

    def run(self):
        """
        主运行逻辑

        Pages:
            in: page_secret_shop
            out: page_secret_shop
        """
        logger.hr('秘密商店刷书签', level=1)
        logger.info(f'最大刷新次数: {self.max_refresh}')
        logger.info(f'购买圣约书签: {self.buy_covenant}')
        logger.info(f'购买神秘奖牌: {self.buy_mystic}')

        # 稳定性计时器
        timeout = Timer(60, count=120).start()
        action_timer = Timer(0.3, count=1)
        scan_timer = Timer(0.8, count=2)
        # TODO: computer performance needed
        # scan_timer = Timer(0.5, count=2)

        while 1:
            self.device.screenshot()

            # 超时保护
            if timeout.reached():
                logger.warning('运行超时，停止')
                break

            # 达到最大刷新次数
            if self.refresh_count >= self.max_refresh:
                logger.info('达到最大刷新次数')
                break

            # 优先处理购买确认弹窗
            if self.appear_then_click(BUY_CONFIRM, interval=0.3):
                timeout.reset()
                continue

            # 处理刷新确认弹窗（不在这里计数，在点击 REFRESH 时计数）
            if self.appear_then_click(REFRESH_CONFIRM, interval=0.3):
                self._scrolled = False
                self._current_round_bought = 0
                # 重置购买标记，新一轮可以重新购买
                self._covenant_purchased_this_round = False
                self._mystic_purchased_this_round = False
                timeout.reset()
                scan_timer.reset()
                continue

            # 等待页面稳定后再扫描
            if not scan_timer.reached():
                continue

            # 查找并购买目标物品
            if action_timer.reached():
                targets = self._find_target_buy_buttons()

                if targets:
                    # 购买第一个找到的目标
                    item_type, buy_btn = targets[0]
                    logger.info(f'找到 {item_type}: area={buy_btn.area}, button={buy_btn.button}')
                    self.device.click(buy_btn)

                    # 等待确认弹窗
                    if self._handle_buy_confirm():
                        self._current_round_bought += 1
                        if item_type == 'covenant':
                            self.covenant_bought += 1
                            self._covenant_purchased_this_round = True
                            logger.info(f'购买圣约书签成功 (累计: {self.covenant_bought})')
                        else:
                            self.mystic_bought += 1
                            self._mystic_purchased_this_round = True
                            logger.info(f'购买神秘奖牌成功 (累计: {self.mystic_bought})')

                    timeout.reset()
                    action_timer.reset()
                    continue

                # 当前页面没有目标，尝试滚动
                if not self._scrolled:
                    self._scroll_down()
                    timeout.reset()
                    scan_timer.reset()
                    continue

                # 已滚动且没有目标，刷新商店
                if self.appear_then_click(REFRESH, interval=0.5):
                    self.refresh_count += 1
                    logger.info(f'刷新商店 (累计: {self.refresh_count})')
                    timeout.reset()
                    scan_timer.reset()  # 等待刷新弹窗出现，避免扫描旧画面
                    continue

        # 输出统计
        logger.hr('刷书签完成', level=1)
        logger.info(f'刷新次数: {self.refresh_count}')
        logger.info(f'圣约书签: {self.covenant_bought}')
        logger.info(f'神秘奖牌: {self.mystic_bought}')


# 保持向后兼容
SecretShopRefresh = SecretShop
