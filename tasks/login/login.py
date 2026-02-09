"""
Epic Seven 登录模块

功能:
    - 启动游戏并等待加载完成
    - 处理登录错误（重试一次）
    - 处理维护公告（OCR剩余时间，调整调度器）
    - 处理热更新（等待下载完成）
    - 处理版本更新（跳转 Google Play）
    - 点击进入游戏

流程:
    启动游戏 → 等待5秒 → 轮询检测:
      - LOGIN_ERROR → 重启一次，再出现则报错
      - UNDER_MAINTENANCE → 关闭广告 → OCR时间 → 调度 → 退出
      - GAME_UPGRADE_AVAILABLE → 跳转 Google Play → 更新 → 重启
      - PATCH_APPLY → 等待热更新完成
      - TOUCH_TO_CLOSE → 关闭各种公告弹窗
      - LOGIN_LOADING → 等待中
      - LOGIN_CONFIRM → 点击上方区域进入
"""
from module.base.timer import Timer
from module.exception import GameNotRunningError, GameServerUnderMaintenance
from module.logger import logger
from tasks.login.update import UpdateHandler
from tasks.base.assets.assets_base_popup import TOUCH_TO_CLOSE
from tasks.login.assets.assets_login import (
    GAME_UPGRADE_AVAILABLE,
    LOGIN_ERROR,
    LOGIN_LOADING,
    PATCH_APPLY,
    LOGIN_CONFIRM,
    UNDER_MAINTENANCE,
)
from tasks.login.assets.assets_login_maintenance import ADVERTISE_CLOSE


class Login(UpdateHandler):
    """
    Epic Seven 登录处理

    使用 ALAS 标准状态循环模式
    继承 UpdateHandler 以支持 Google Play 更新
    """

    def _handle_app_login(self):
        """
        处理游戏启动到进入主界面的过程

        Pages:
            in: 游戏启动中
            out: 游戏主界面

        Raises:
            GameNotRunningError: 游戏未运行或启动失败
            GameServerUnderMaintenance: 服务器维护中
        """
        logger.hr('App login')

        # 计时器
        orientation_timer = Timer(5)
        startup_timer = Timer(5).start()  # 启动后等待5秒再开始检测
        app_timer = Timer(5).start()  # 检查游戏是否存活
        timeout = Timer(120, count=120).start()  # 总超时

        # 状态
        start_success = False
        login_success = False
        error_retried = False  # 是否已重试过一次

        self.device.stuck_record_clear()

        while 1:
            # 检查游戏是否存活
            if app_timer.reached():
                if self.device.app_is_running():
                    start_success = True
                else:
                    if start_success:
                        logger.error('Game died during launch')
                        raise GameNotRunningError('Game not running')
                    else:
                        if timeout.reached():
                            logger.error('Game not started after timeout')
                            raise GameNotRunningError('Game not running')
                app_timer.reset()
            # Watch device rotation
            if not login_success and orientation_timer.reached():
                # Screen may rotate after starting an app
                self.device.get_orientation()
                orientation_timer.reset()

            self.device.screenshot()

            # 总超时检查
            if timeout.reached():
                logger.error('Login timeout')
                raise GameNotRunningError('Login timeout')

            # 等待5秒后再开始检测
            if not startup_timer.reached():
                continue

            # === 优先检测错误状态 ===

            # 登录错误
            if self.appear(LOGIN_ERROR, interval=5):
                if error_retried:
                    logger.error('Login error appeared twice, stopping')
                    self.device.app_stop()
                    raise GameNotRunningError('Login error')
                else:
                    logger.warning('Login error, restarting game')
                    error_retried = True
                    self.device.app_stop()
                    self.device.app_start()
                    startup_timer.reset()
                    timeout.reset()
                    self.device.stuck_record_clear()
                    continue

            # 维护中
            if self.appear(UNDER_MAINTENANCE, interval=5):
                logger.warning('Server under maintenance')
                self._handle_maintenance()
                # _handle_maintenance 会抛出异常，不会执行到这里

            # 版本更新（需要跳转 Google Play）
            if self.appear(GAME_UPGRADE_AVAILABLE, interval=5):
                logger.warning('Game update available')
                self.device.click(TOUCH_TO_CLOSE)  # 点击跳转 Google Play
                self._handle_google_play_update()
                # 更新完成后重启游戏
                self.app_restart()
                return True

            # === 更新和加载流程 ===

            # 热更新下载中
            if self.appear(PATCH_APPLY, interval=5):
                logger.info('Patch downloading...')
                self.device.stuck_record_clear()
                timeout.reset()  # 下载可能耗时，重置超时
                continue

            # 加载中
            if self.appear(LOGIN_LOADING, interval=5):
                logger.info('Game loading...')
                self.device.stuck_record_clear()
                continue

            # === 弹窗处理 ===

            # 各种公告弹窗（活动公告等，注意要在 GAME_UPGRADE_AVAILABLE 之后检测）
            if self.handle_touch_to_close():
                self.device.stuck_record_clear()
                continue

            # === 正常进入游戏 ===

            # 加载完成，可以进入游戏
            if self.appear_then_click(LOGIN_CONFIRM, interval=2):
                logger.info('Clicking to enter game')
                self.device.stuck_record_clear()
                login_success = True
                # 点击后等待进入主界面
                break

        logger.info('Login completed')
        return True

    def _handle_maintenance(self):
        """
        处理维护公告

        流程:
            1. 关闭广告弹窗
            2. OCR 剩余维护时间
            3. 调整调度器
            4. 退出游戏并抛出异常

        Raises:
            GameServerUnderMaintenance: 服务器维护中
        """
        logger.hr('Handle maintenance')

        # 等待广告弹窗出现并关闭
        close_timeout = Timer(10).start()
        while 1:
            self.device.screenshot()

            if close_timeout.reached():
                logger.warning('Advertise close timeout, proceeding')
                break

            if self.appear_then_click(ADVERTISE_CLOSE, interval=2):
                logger.info('Closed advertise popup')
                break

        # TODO: OCR 维护剩余时间
        # 目前先设置一个固定的等待时间
        maintenance_minutes = 60  # 默认等待60分钟

        # 调整调度器
        logger.info(f'Setting delay for {maintenance_minutes} minutes')
        # self.config.task_delay(minute=maintenance_minutes)

        # 退出游戏
        self.device.app_stop()

        raise GameServerUnderMaintenance(f'Server under maintenance, waiting {maintenance_minutes} minutes')

    def handle_app_login(self):
        """
        登录入口（带截图间隔调整）
        """
        logger.info('handle_app_login')
        self.device.screenshot_interval_set(0.5)
        self.device.stuck_timer = Timer(300, count=300).start()
        try:
            self._handle_app_login()
        finally:
            self.device.screenshot_interval_set()
            self.device.stuck_timer = Timer(60, count=60).start()

    def app_stop(self):
        """停止游戏"""
        logger.hr('App stop')
        self.device.app_stop()

    def app_start(self):
        """启动游戏"""
        logger.hr('App start')
        self.device.app_start()
        self.handle_app_login()

    def app_restart(self):
        """重启游戏"""
        logger.hr('App restart')
        self.device.app_stop()
        self.device.app_start()
        self.handle_app_login()

