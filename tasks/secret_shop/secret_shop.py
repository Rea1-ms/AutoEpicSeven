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
    BUY_FOR_STABLE,
    COVENANT_BOOKMARK,
    MYSTIC_MEDAL,
    REFRESH,
    REFRESH_CONFIRM,
)


class SecretShop(ModuleBase):
    """
    秘密商店刷书签

    使用 ALAS 标准状态循环模式。
    稳定检测：使用 BUY_FOR_STABLE 检测底部区域的购买按钮，连续帧确认画面稳定。
    """

    # 滚动参数
    SCROLL_AREA = (960, 550, 960, 360)
    # 稳定检测：连续 N 帧检测到底部 BUY 按钮才认为稳定
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

        使用 BUY_FOR_STABLE 检测底部区域的购买按钮。
        连续 STABLE_THRESHOLD 帧检测到才认为稳定，避免动画中误判。

        Returns:
            bool: 画面是否稳定
        """
        if self.appear(BUY_FOR_STABLE):
            self._stable_count += 1
            if self._stable_count >= self.STABLE_THRESHOLD:
                return True
        else:
            self._stable_count = 0
        return False

    def _reset_stable(self):
        """重置稳定计数器，用于滚动/刷新后"""
        self._stable_count = 0

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

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 超时退出
            if timeout.reached():
                logger.warning('购买确认超时')
                return False

            # 正面条件退出：看到 REFRESH 按钮 = 弹窗已关闭，回到商店
            if self.appear(REFRESH):
                return True

            # 点击确认购买
            if self.appear_then_click(BUY_CONFIRM, interval=2):
                continue

        return False

    def run(self, skip_first_screenshot=False):
        """
        主运行逻辑

        使用 ALAS 标准状态循环模式：
        - 截图 -> 退出条件 -> 弹窗处理 -> 稳定检测 -> 扫描购买 -> 滚动 -> 刷新
        - 无 sleep 依赖
        - 使用 BUY_FOR_STABLE 连续帧检测画面稳定

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
