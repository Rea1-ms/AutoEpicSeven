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
    识别并点击更新通知 → 跳转 Google Play
        ↓
    轮询 GOOGLE_PLAY_UPDATE → 点击开始更新
        ↓
    轮询 GOOGLE_PLAY_PLAY（更新完成标志）
        ↓
    出现后 → 返回，由调用方执行 app_restart()
"""
import module.config.server as server_
from module.base.timer import Timer
from module.logger import logger
from tasks.base.assets.assets_base_popup import TOUCH_TO_CLOSE
from tasks.base.popup import PopupHandler
from tasks.login.assets.assets_login import GAME_UPGRADE_AVAILABLE
from tasks.login.assets.assets_login_update import (
    GOOGLE_PLAY_PLAY,
    GOOGLE_PLAY_UPDATE,
)


class GameUpdateRequired(Exception):
    """游戏需要更新，更新完成后需要重启"""


class GameUpdateFailed(Exception):
    """游戏更新失败"""


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

        return self._handle_google_play_update()

    def _handle_google_play_update(self, skip_first_screenshot=True) -> bool:
        """
        处理 Google Play 更新流程

        Pages:
            in: 游戏更新通知或 Google Play 更新页面
            out: Google Play 显示可启动游戏

        Returns:
            bool: 是否执行了跳转或更新点击。

        Raises:
            GameUpdateFailed: 更新超时或失败
        """
        logger.info('Waiting for Google Play update...')
        timeout = Timer(300).start()
        clicked = False

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # Completion is current-frame evidence, independent of either
            # action's retry interval and of whether this invocation clicked.
            if self.appear(GOOGLE_PLAY_PLAY):
                logger.info('Update completed, Play button appeared')
                return clicked

            # 超时检查
            if timeout.reached():
                logger.error('Google Play update timeout after 5 minutes')
                self.device.app_stop()
                raise GameUpdateFailed('Google Play update timeout')

            # A sent click is not proof that downloading started. Retry only
            # while the current frame still positively offers Update. Pending,
            # cancelling, download, and unknown frames authorize no clicks.
            # Neither retry renews the original five-minute deadline.
            if self.appear_then_click(GOOGLE_PLAY_UPDATE, interval=5):
                logger.info('Clicked Update button, waiting for download...')
                clicked = True
                continue

            # The legacy notice text still matches the new dialog. Its matched
            # coordinates therefore do not identify the retry control. Match
            # the shared arrow on this frame to carry its vertical offset into
            # the click instead of using the old fixed footer coordinates.
            if self.appear(GAME_UPGRADE_AVAILABLE) and self.appear_then_click(TOUCH_TO_CLOSE, interval=3):
                logger.info('Clicked game update notice, waiting for Google Play...')
                clicked = True
                continue
