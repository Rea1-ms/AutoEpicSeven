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

国服功能（补充）:
    - 大版本更新按已采集画面尽力处理
    - 确认客户端下载并等待下载完成
    - 处理客户端安装提示和系统安装确认（可能竖屏）
    - 确认本游戏安装完成，再由调用方重启游戏

国服大版本更新流程:
    检测到 CLIENT_UPDATE_CONFIRM 或已在更新中的页面
        ↓
    点击 CLIENT_UPDATE_CONFIRM → 等待 CLIENT_DOWNLOADING
        ↓
    点击 CLIENT_INSTALL_CONFIRM → 跳转系统安装器
        ↓
    识别 PACKAGE_INSTALL_CONFIRM → 转换竖屏坐标并确认安装
        ↓
    轮询 PACKAGE_INSTALL_COMPLETE（安装完成标志）
        ↓
    出现后 → 返回，由调用方执行 app_restart()

补丁更新流程（国服与国际服，由登录循环处理）:
    PATCH_CHECKING（检查与扫描） → PATCH_APPLY（确认下载）
        ↓
    PATCH_DOWNLOADING（资源下载中） → 继续登录游戏
"""
import cv2

import module.config.server as server_
from module.base.button import ClickButton
from module.base.timer import Timer
from module.logger import logger
from tasks.base.assets.assets_base_popup import TOUCH_TO_CLOSE
from tasks.base.popup import PopupHandler
from tasks.login.assets.assets_login import GAME_UPGRADE_AVAILABLE
from tasks.login.assets.assets_login_update import (
    CLIENT_DOWNLOADING,
    CLIENT_INSTALL_CONFIRM,
    CLIENT_UPDATE_CONFIRM,
    GOOGLE_PLAY_PLAY,
    GOOGLE_PLAY_UPDATE,
    PACKAGE_INSTALL_COMPLETE,
    PACKAGE_INSTALL_CONFIRM,
)


class GameUpdateRequired(Exception):
    """游戏需要更新，更新完成后需要重启"""


class GameUpdateFailed(Exception):
    """游戏更新失败"""


class UpdateHandler(PopupHandler):
    """
    国服与 Google Play 客户端更新处理

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
        if server_.is_cn_server(self.config.Emulator_PackageName):
            if not UpdateHandler._is_cn_app_update(self):
                return False
            return UpdateHandler._handle_cn_app_update(self)

        if not server_.is_oversea_server(self.config.Emulator_PackageName):
            return False

        if not self.appear(GAME_UPGRADE_AVAILABLE):
            return False

        logger.hr('Game update required')
        logger.info('Detected game version update, redirecting to Google Play')

        return self._handle_google_play_update()

    def _cn_package_install_button(self):
        """Recognize the captured portrait installer and return native bounds."""
        image = self.device.image
        if image.shape[:2] != (1280, 720):
            return None

        # Assets remain 1280x720. Rotate only a recognition copy, leaving the
        # device screenshot in native portrait coordinates. The template includes
        # Epic Seven's icon/name and update question, not a generic UPDATE label.
        normalized = cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
        if not PACKAGE_INSTALL_CONFIRM.match_template(normalized):
            return None
        left, top, right, bottom = PACKAGE_INSTALL_CONFIRM.button
        return ClickButton(
            area=(top, 1280 - right, bottom, 1280 - left),
            name='PACKAGE_INSTALL_CONFIRM',
        )

    def _is_cn_app_update(self) -> bool:
        if not server_.is_cn_server(self.config.Emulator_PackageName):
            return False
        if UpdateHandler._cn_package_install_button(self):
            return True
        if self.device.image.shape[:2] != (720, 1280):
            return False
        return any(self.appear(button) for button in (
            CLIENT_UPDATE_CONFIRM, CLIENT_DOWNLOADING, CLIENT_INSTALL_CONFIRM, PACKAGE_INSTALL_COMPLETE,
        ))

    def _handle_cn_app_update(self, skip_first_screenshot=True) -> bool:
        """Best-effort update of the CN client, including its native installer.

        Pages:
            in: CN client update notice, download, or captured installer state.
            out: Native installer positively reports Epic Seven installed.

        The caller restarts the game only after this method returns normally.
        """
        logger.hr('CN client update required')
        timeout = Timer(300).start()
        clicked = False

        # The SDK can close the game and rotate Android during installation.
        # Keep application-liveness checks in the caller suspended until the
        # installer confirms completion; neither a click nor game exit proves
        # success. All retries share the original, non-renewable deadline.
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.get_orientation()
                self.device.screenshot()

            portrait = self.device.image.shape[:2] == (1280, 720)
            if not portrait and self.appear(PACKAGE_INSTALL_COMPLETE):
                logger.info('CN client installation completed')
                return clicked

            if timeout.reached():
                logger.error('CN client update timeout after 5 minutes')
                self.device.app_stop()
                raise GameUpdateFailed('CN client update timeout')

            if portrait:
                button = UpdateHandler._cn_package_install_button(self)
                if button and self.interval_is_reached(button, interval=3):
                    # Native portrait coordinates must bypass the configured
                    # landscape touch backend, which assumes 1280x720. Retain
                    # normal click protection and the per-action retry timer.
                    self.device.handle_control_check(button)
                    left, top, right, bottom = button.button
                    x, y = (left + right) // 2, (top + bottom) // 2
                    logger.info(f'Confirming CN package installation at ({x}, {y})')
                    self.device.click_adb(x, y)
                    self.interval_reset(button, interval=3)
                    clicked = True
                continue

            if self.appear_then_click(CLIENT_UPDATE_CONFIRM, interval=3):
                logger.info('Starting CN client download')
                clicked = True
                continue
            if self.appear_then_click(CLIENT_INSTALL_CONFIRM, interval=3):
                logger.info('Opening CN package installer')
                clicked = True
                continue
            if self.appear(CLIENT_DOWNLOADING):
                self.device.stuck_record_clear()
                continue

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
