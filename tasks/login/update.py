"""
Epic Seven Google Play 更新模块

功能:
    - 检测游戏版本更新提示
    - 跳转 Google Play 商店
    - 等待更新完成
    - 重启游戏

流程:
    检测到 GAME_UPGRADE_AVAILABLE
        ↓
    点击 TOUCH_TO_CLOSE → 跳转 Google Play
        ↓
    等待 3 秒加载
        ↓
    轮询 GOOGLE_PLAY_UPDATE → 点击开始更新
        ↓
    轮询 GOOGLE_PLAY_PLAY（更新完成标志）
        ↓
    出现后 → 返回，由调用方执行 app_restart()
"""
import module.config.server as server_
from module.base.timer import Timer
from module.exception import GameNotRunningError
from module.logger import logger
from tasks.base.popup import PopupHandler
from tasks.base.assets.assets_base_popup import TOUCH_TO_CLOSE
from tasks.login.assets.assets_login import GAME_UPGRADE_AVAILABLE
from tasks.login.assets.assets_login_update import (
    GOOGLE_PLAY_UPDATE,
    GOOGLE_PLAY_PLAY,
)


class GameUpdateRequired(Exception):
    """游戏需要更新，更新完成后需要重启"""
    pass


class GameUpdateFailed(Exception):
    """游戏更新失败"""
    pass


class UpdateHandler(PopupHandler):
    """
    Google Play 更新处理

    使用 ALAS 标准状态循环模式
    """

    def handle_game_update(self) -> bool:
        """
        检测并处理游戏版本更新

        Returns:
            bool: 是否检测到更新并处理完成
                  True = 更新完成，需要重启游戏
                  False = 没有检测到更新

        Raises:
            GameUpdateFailed: 更新超时或失败
        """
        if not server_.is_oversea_server(self.config.Emulator_PackageName):
            return False

        if not self.appear(GAME_UPGRADE_AVAILABLE):
            return False

        logger.hr('Game update required')
        logger.info('Detected game version update, redirecting to Google Play')

        # 点击跳转 Google Play
        self.device.click(TOUCH_TO_CLOSE)

        # 执行更新流程
        self._handle_google_play_update()

        return True

    def _handle_google_play_update(self):
        """
        处理 Google Play 更新流程

        Raises:
            GameUpdateFailed: 更新超时或失败
        """
        logger.info('Waiting for Google Play to load...')

        # 等待 Google Play 加载
        self.device.sleep(3)

        # 超时设置：5 分钟
        timeout = Timer(300, count=300).start()
        update_clicked = False

        while 1:
            self.device.screenshot()

            # 超时检查
            if timeout.reached():
                logger.error('Google Play update timeout after 5 minutes')
                self.device.app_stop()
                raise GameUpdateFailed('Google Play update timeout')

            # 阶段1：等待并点击 Update 按钮
            if not update_clicked:
                if self.appear_then_click(GOOGLE_PLAY_UPDATE, interval=5):
                    logger.info('Clicked Update button, waiting for download...')
                    update_clicked = True
                    continue

            # 阶段2：等待更新完成（Play 按钮出现）
            if self.appear(GOOGLE_PLAY_PLAY):
                logger.info('Update completed, Play button appeared')
                # 不点击 Play，直接返回让调用方 app_restart
                return

        # 不应该到达这里
        raise GameUpdateFailed('Unexpected state in Google Play update')
