"""
Epic Seven 登录模块

功能:
    - 启动游戏并等待加载完成
    - 处理登录错误（重试一次）
    - 处理维护公告（OCR剩余时间，调整调度器）
    - 处理热更新（等待下载完成）
    - 处理版本更新（跳转 Google Play）
    - 点击进入游戏
    - 处理登录后弹窗直到到达主界面

流程:
    启动游戏 → 等待5秒 → 单循环轮询:
      终止条件: is_in_main() = True（主界面 + 无遮罩）
      - LOGIN_ERROR → 重启一次，再出现则报错
      - 维护公告(ANNOUNCEMENT_CLOSE) → UNDER_MAINTENANCE → 调度 → 退出
      - GAME_UPGRADE_AVAILABLE → 跳转 Google Play → 更新 → 重启
      - PATCH_APPLY → 等待热更新完成
      - LOGIN_LOADING → 等待加载中
      - LOGIN_CONFIRM → 点击进入
      - ui_additional() → 签到/新角色/buff/礼包等弹窗
      - handle_network_error() → 网络错误弹窗
"""
from datetime import datetime, timedelta

from module.base.timer import Timer
from module.config.deep import deep_get
from module.config.config import TaskEnd
import module.config.server as server_
from module.exception import (
    GameNotRunningError,
    GameServerUnderMaintenance,
)
from module.logger import logger
from module.ocr.ocr import Duration, OcrWhiteLetterOnComplexBackground
from tasks.base.ui import UI
from tasks.base.assets.assets_base_popup import TOUCH_TO_CLOSE
from tasks.login.assets.assets_login import (
    GAME_UPGRADE_AVAILABLE,
    LOGIN_ANNOUNCEMENT_CLOSE,
    LOGIN_ERROR,
    LOGIN_LOADING,
    PATCH_APPLY,
    PATCH_PERCENT_SIGN,
    LOGIN_CONFIRM,
    UNDER_MAINTENANCE,
    VERIFYING
)
from tasks.login.assets.assets_login_maintenance import (
    ANNOUNCEMENT_CLOSE,
    OCR_MAINTENANCE_TIME,
)
from tasks.login.update import UpdateHandler


class Login(UI):
    """
    Epic Seven 登录处理

    继承 UI 以使用 is_in_main() 和 ui_additional()
    单循环模式：从游戏启动到进入主界面一气呵成
    """
    _maintenance_backup = None

    def _handle_login_domain_popup(self, interval=2) -> bool:
        """
        Handle popups that only belong to the login domain.

        This keeps login-specific startup/re-login popups out of global
        `ui_additional()` handlers.
        """
        if self.appear_then_click(ANNOUNCEMENT_CLOSE, interval=interval):
            logger.info('Closed announce popup')
            return True

        if server_.is_cn_server(self.config.Emulator_PackageName) and self.appear_then_click(
            LOGIN_ANNOUNCEMENT_CLOSE, interval=interval
        ):
            logger.info('Closed CN startup announcement')
            return True

        return False

    def _handle_app_login(self):
        """
        处理游戏启动到进入主界面的完整过程

        单循环设计：
        1. 等待加载，处理启动阶段的错误/维护/更新
        2. 点击进入游戏
        3. 处理登录后弹窗（签到、新角色、buff、礼包等）
        4. 检测到主界面且无遮罩后退出

        Pages:
            in: 游戏启动中
            out: 游戏主界面（无弹窗）

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
        main_confirm = Timer(1.5, count=4).start()  # 主界面稳定确认，防止突然闪现的正常一帧
        network_error_window = Timer(120, count=0).start()
        network_error_count = 0

        # 状态
        start_success = False
        login_success = False
        error_retried = False

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

            # ==========================================
            # 终止条件：到达主界面且无弹窗遮罩
            # ==========================================
            if self.is_in_main():
                if main_confirm.reached():
                    logger.info('Login to main confirm')
                    break
            else:
                main_confirm.reset()

            # ==========================================
            # 错误状态（优先级最高）
            # ==========================================

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
                    main_confirm.reset()
                    self.device.stuck_record_clear()
                    continue

            # Login-only popup layer, including CN re-login announcement close.
            if self._handle_login_domain_popup(interval=2):
                network_error_count = 0
                timeout.reset()
                main_confirm.reset()
                continue

            # 维护中
            if self.appear(UNDER_MAINTENANCE, interval=5):
                logger.warning('Server under maintenance')
                self._handle_maintenance()
                # _handle_maintenance 会抛出异常，不会执行到这里

            # 版本更新（跳转 Google Play）
            if server_.is_oversea_server(self.config.Emulator_PackageName) and self.appear(
                GAME_UPGRADE_AVAILABLE, interval=5
            ):
                logger.warning('Game update available')
                self.device.click(TOUCH_TO_CLOSE)
                UpdateHandler._handle_google_play_update(self)
                self.app_restart()
                return True

            # ==========================================
            # 加载状态
            # ==========================================

            # 热更新下载中
            # 开始下载
            if self.appear_then_click(PATCH_APPLY, interval=3):
                logger.info('Patch start...')
                network_error_count = 0
                timeout.reset()
                continue

            # 下载中
            if self.appear(PATCH_PERCENT_SIGN, interval=3):
                logger.info('Patch downloading...')
                network_error_count = 0
                timeout.reset()
                continue

            # 加载中
            if self.appear(LOGIN_LOADING, interval=5)\
                    or self.appear(VERIFYING, interval=5):
                logger.info('Game loading...')
                network_error_count = 0
                self.device.stuck_record_clear()
                continue

            # ==========================================
            # 进入游戏
            # ==========================================

            if self.appear_then_click(LOGIN_CONFIRM, interval=2):
                logger.info('Clicking to enter game')
                login_success = True
                network_error_count = 0
                self.device.stuck_record_clear()
                timeout.reset()
                main_confirm.reset()
                continue

            # ==========================================
            # 登录后弹窗处理
            # ui_additional() 包含：签到/新角色/buff/礼包/通用弹窗
            # ==========================================

            if self.ui_additional():
                network_error_count = 0
                timeout.reset()
                main_confirm.reset()
                continue

            # 网络错误弹窗
            if self.handle_network_error():
                if network_error_window.reached():
                    network_error_window.reset()
                    network_error_count = 0
                network_error_count += 1
                logger.warning(f'Network popup count in current window: {network_error_count}')

                if network_error_count >= 12:
                    logger.warning('Too many network popups during login, fallback to maintenance delay')
                    maintenance_minutes = self._ocr_maintenance_minutes()
                    maintenance_minutes = max(maintenance_minutes, 30)
                    self._delay_tasks_during_maintenance(maintenance_minutes)
                    self.device.app_stop()
                    raise TaskEnd(
                        f'Too many network popups, waiting {maintenance_minutes} minutes'
                    )

                timeout.reset()
                main_confirm.reset()
                continue

        logger.info('Login completed')
        if self._maintenance_backup is not None:
            self._maintenance_backup.recover()
            self._maintenance_backup = None
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
            TaskEnd: 维护中，结束当前任务
        """
        # 尝试关闭公告弹窗（不知道会不会出现1个以上弹窗的情况）
        if self.appear_then_click(ANNOUNCEMENT_CLOSE, interval=2):
            logger.info('Closed advertise popup')

        logger.hr('Handle maintenance')

        maintenance_minutes = self._ocr_maintenance_minutes()

        logger.info(f'Setting delay for {maintenance_minutes} minutes')
        self._delay_tasks_during_maintenance(maintenance_minutes)

        self.device.app_stop()

        raise TaskEnd(f'Server under maintenance, waiting {maintenance_minutes} minutes')

    def _ocr_maintenance_minutes(self) -> int:
        """
        OCR 维护剩余时间，失败则回退到 60 分钟
        """
        lang = self.config.Emulator_GameLanguage
        if lang == 'auto' or not lang:
            lang = 'cn'

        ocr = OcrWhiteLetterOnComplexBackground(
            OCR_MAINTENANCE_TIME, lang=lang, name='MaintenanceOCR'
        )
        duration = Duration(OCR_MAINTENANCE_TIME, lang=lang, name='MaintenanceDuration')
        timeout = Timer(8, count=16).start()

        while 1:
            self.device.screenshot()

            # 维护页有时先出现公告弹窗，先尝试关闭
            if self.appear_then_click(ANNOUNCEMENT_CLOSE, interval=2):
                continue

            text = ocr.ocr_single_line(self.device.image)
            td = duration.format_result(text)
            if td.total_seconds() > 0:
                minutes = max(1, int(td.total_seconds() // 60) + 1)
                logger.info(f'Maintenance OCR: {text} -> {minutes} minutes')
                return minutes

            if timeout.reached():
                break

        logger.warning('Maintenance OCR failed, fallback 60 minutes')
        return 60

    def _delay_tasks_during_maintenance(self, minutes: int):
        """
        Delay all enabled tasks whose next_run is within maintenance window.
        Re-schedule them sequentially starting from maintenance end.
        """
        now = datetime.now().replace(microsecond=0)
        delta = timedelta(minutes=minutes)
        limit = now + delta

        tasks = []
        for task_name, task_data in self.config.data.items():
            enable = deep_get(task_data, keys='Scheduler.Enable', default=False)
            next_run = deep_get(task_data, keys='Scheduler.NextRun', default=None)
            if not enable or not isinstance(next_run, datetime):
                continue
            if next_run <= limit:
                tasks.append((next_run, task_name))

        tasks.sort(key=lambda item: item[0])
        if tasks:
            scheduled = limit
            for _, task_name in tasks:
                self.config.modified[f'{task_name}.Scheduler.NextRun'] = scheduled
                scheduled = scheduled + timedelta(seconds=1)
            logger.info(f'Maintenance delay: rescheduled {len(tasks)} tasks starting at {limit}')
            self.config.update()

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
