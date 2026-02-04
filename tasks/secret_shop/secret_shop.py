"""
秘密商店刷书签模块

功能:
    - 自动刷新秘密商店
    - 识别圣约书签和神秘奖牌
    - 自动购买并处理确认弹窗
    - 支持金币/水晶不足检测
    - 统计购买数量
"""
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


class SecretShopRefresh(ModuleBase):
    """秘密商店刷书签"""

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

    def find_and_buy_bookmarks(self) -> int:
        """
        在当前页面查找并购买书签

        Returns:
            int: 本次购买的数量
        """
        bought = 0
        image = self.device.image

        # 查找目标物品
        items = []
        if self.buy_covenant:
            bookmarks = COVENANT_BOOKMARK.match_multi_template(image)
            for b in bookmarks:
                items.append(('covenant', b))
        if self.buy_mystic:
            medals = MYSTIC_MEDAL.match_multi_template(image)
            for m in medals:
                items.append(('mystic', m))

        if not items:
            logger.info('当前页面没有找到书签或奖牌')
            return 0

        # 查找购买按钮
        buy_buttons = BUY.match_multi_template(image)
        if not buy_buttons:
            logger.warning('没有找到购买按钮')
            return 0

        # 按 Y 坐标排序
        items.sort(key=lambda x: x[1].area[1])
        buy_buttons.sort(key=lambda x: x.area[1])

        # Y 坐标配对并购买
        for item_type, item in items:
            item_y_center = (item.area[1] + item.area[3]) / 2

            for buy_btn in buy_buttons:
                buy_y_center = (buy_btn.area[1] + buy_btn.area[3]) / 2

                # Y 中心点距离在 30 像素内认为是同一行
                if abs(item_y_center - buy_y_center) < 30:
                    logger.info(f'找到 {item_type}: Y={item_y_center:.0f}')

                    # 点击购买
                    self.device.click(buy_btn)
                    self.device.sleep(0.5)

                    # 处理购买确认
                    if self._handle_buy_confirm():
                        bought += 1
                        if item_type == 'covenant':
                            self.covenant_bought += 1
                            logger.info(f'购买圣约书签成功 (累计: {self.covenant_bought})')
                        else:
                            self.mystic_bought += 1
                            logger.info(f'购买神秘奖牌成功 (累计: {self.mystic_bought})')

                        # 购买后需要重新截图
                        self.device.screenshot()
                    break

        return bought

    def _handle_buy_confirm(self) -> bool:
        """
        处理购买确认弹窗

        Returns:
            bool: 是否购买成功
        """
        timeout = Timer(3).start()
        while not timeout.reached():
            self.device.screenshot()

            # 确认购买
            if self.appear_then_click(BUY_CONFIRM, interval=0.5):
                self.device.sleep(0.5)
                return True

            # TODO: 检测金币/水晶不足的弹窗
            # if self.appear(INSUFFICIENT_GOLD):
            #     logger.warning('金币不足')
            #     self.appear_then_click(POPUP_CANCEL)
            #     return False

        logger.warning('购买确认超时')
        return False

    def refresh_shop(self) -> bool:
        """
        刷新商店

        Returns:
            bool: 是否刷新成功
        """
        logger.info('刷新商店')

        # 点击刷新
        self.device.screenshot()
        if not self.appear_then_click(REFRESH, interval=0.5):
            logger.warning('没有找到刷新按钮')
            return False

        # 等待确认弹窗
        timeout = Timer(3).start()
        while not timeout.reached():
            self.device.screenshot()
            if self.appear_then_click(REFRESH_CONFIRM, interval=0.5):
                self.refresh_count += 1
                logger.info(f'刷新成功 (累计: {self.refresh_count})')
                self.device.sleep(0.8)
                return True

        logger.warning('刷新确认超时')
        return False

    def run(self):
        """
        主运行逻辑
        """
        logger.hr('秘密商店刷书签', level=1)
        logger.info(f'最大刷新次数: {self.max_refresh}')
        logger.info(f'购买圣约书签: {self.buy_covenant}')
        logger.info(f'购买神秘奖牌: {self.buy_mystic}')

        # TODO: 导航到秘密商店
        # self.goto_secret_shop()

        while self.refresh_count < self.max_refresh:
            # 截图
            self.device.screenshot()

            # 查找并购买书签
            self.find_and_buy_bookmarks()

            # 刷新商店
            if not self.refresh_shop():
                logger.error('刷新失败，停止运行')
                break

            # 防止过快
            self.device.sleep(0.3)

        # 输出统计
        logger.hr('刷书签完成', level=1)
        logger.info(f'刷新次数: {self.refresh_count}')
        logger.info(f'圣约书签: {self.covenant_bought}')
        logger.info(f'神秘奖牌: {self.mystic_bought}')

        # TODO: 设置下次运行时间
        # self.config.task_delay(minute=30)
