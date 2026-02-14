from module.base.button import ButtonWrapper
from module.base.decorator import run_once
from module.base.timer import Timer
from module.exception import GameNotRunningError, GamePageUnknownError, HandledError
from module.logger import logger
from module.ocr.ocr import Ocr
from tasks.base.assets.assets_base_main_page import POPUP_OVERLAY, WHITE_STAR
from tasks.base.assets.assets_base_page import CLOSE
from tasks.base.assets.assets_base_popup import (
    NEW_CHARACTER_VIDEO_PASS,
    TOUCH_TO_CLOSE,
)
from tasks.base.main_page import MainPage
from tasks.base.page import Page, page_main
from tasks.base.popup import ANNOUNCEMENT_DONOT_REMIND
from tasks.login.assets.assets_login import LOGIN_CONFIRM
from tasks.login.assets.assets_login_popup import (
    AD_BUFF_CLOSE,
    CHECK_IN_CONFIRM,
    NEW_CHARACTER_CONFIRM,
)


class UI(MainPage):
    ui_current: Page
    ui_main_confirm_timer = Timer(0.2, count=0)

    def ui_page_appear(self, page, interval=0):
        """
        Args:
            page (Page):
            interval:
        """
        if page == page_main:
            return self.is_in_main(interval=interval)
        return self.appear(page.check_button, interval=interval)

    def ui_get_current_page(self, skip_first_screenshot=True):
        """
        Args:
            skip_first_screenshot:

        Returns:
            Page:

        Raises:
            GameNotRunningError:
            GamePageUnknownError:
        """
        logger.info("UI get current page")

        @run_once
        def app_check():
            if not self.device.app_is_running():
                raise GameNotRunningError("Game not running")

        @run_once
        def minicap_check():
            if self.config.Emulator_ControlMethod == "uiautomator2":
                self.device.uninstall_minicap()

        @run_once
        def rotation_check():
            self.device.get_orientation()

        @run_once
        def cloud_login():
            if self.config.is_cloud_game:
                from tasks.login.login import Login
                login = Login(config=self.config, device=self.device)
                self.device.dump_hierarchy()
                login.cloud_try_enter_game()

        timeout = Timer(10, count=20).start()
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
                if not hasattr(self.device, "image") or self.device.image is None:
                    self.device.screenshot()
            else:
                self.device.screenshot()

            # End
            if timeout.reached():
                break

            # Known pages
            for page in Page.iter_pages():
                if page.check_button is None:
                    continue
                if self.ui_page_appear(page=page):
                    logger.attr("UI", page.name)
                    self.ui_current = page
                    return page

            # Unknown page but able to handle
            logger.info("Unknown ui page")
            if self.ui_additional():
                timeout.reset()
                continue
            if self.handle_popup_single():
                timeout.reset()
                continue
            if self.handle_popup_confirm():
                timeout.reset()
                continue
            if self.handle_login_confirm():
                continue

            app_check()
            minicap_check()
            rotation_check()
            cloud_login()

        # Unknown page, need manual switching
        logger.warning("Unknown ui page")
        logger.attr("EMULATOR__SCREENSHOT_METHOD", self.config.Emulator_ScreenshotMethod)
        logger.attr("EMULATOR__CONTROL_METHOD", self.config.Emulator_ControlMethod)
        logger.attr("Lang", self.config.LANG)
        logger.warning("Starting from current page is not supported")
        logger.warning(f"Supported page: {[str(page) for page in Page.iter_pages()]}")
        logger.warning('Supported page: Any page with a "HOME" button on the upper-right')
        logger.critical("Please switch to a supported page before starting SRC")
        raise GamePageUnknownError

    def ui_goto(self, destination, skip_first_screenshot=True):
        """
        Args:
            destination (Page):
            skip_first_screenshot:
        """
        # Create connection
        Page.init_connection(destination)
        self.interval_clear(list(Page.iter_check_buttons()))

        logger.hr(f"UI goto {destination}")
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # Destination page
            if self.ui_page_appear(destination):
                logger.info(f'Page arrive: {destination}')
                if self.ui_page_confirm(destination):
                    logger.info(f'Page arrive confirm {destination}')
                break

            # Other pages
            clicked = False
            for page in Page.iter_pages():
                if page.parent is None or page.check_button is None:
                    continue
                if self.ui_page_appear(page, interval=5):
                    logger.info(f'Page switch: {page} -> {page.parent}')
                    self.handle_lang_check(page)
                    if self.ui_page_confirm(page):
                        logger.info(f'Page arrive confirm {page}')
                    button = page.links[page.parent]
                    self.device.click(button)
                    self.ui_button_interval_reset(button)
                    clicked = True
                    break
            if clicked:
                continue

            # Additional
            if self.ui_additional():
                continue
            # if self.handle_popup_single():
            #     continue
            if self.handle_popup_confirm():
                continue
            if self.handle_login_confirm():
                continue

        # Reset connection
        Page.clear_connection()

    def ui_ensure(self, destination, acquire_lang_checked=True, skip_first_screenshot=True):
        """
        Args:
            destination (Page):
            acquire_lang_checked:
            skip_first_screenshot:

        Returns:
            bool: If UI switched.
        """
        logger.hr("UI ensure")
        self.ui_get_current_page(skip_first_screenshot=skip_first_screenshot)

        self.ui_leave_special()

        if acquire_lang_checked:
            if self.acquire_lang_checked():
                self.ui_get_current_page(skip_first_screenshot=skip_first_screenshot)

        if self.ui_current == destination:
            logger.info("Already at %s" % destination)
            return False
        else:
            logger.info("Goto %s" % destination)
            self.ui_goto(destination, skip_first_screenshot=True)
            return True

    def ui_ensure_index(
            self,
            index,
            letter,
            next_button,
            prev_button,
            skip_first_screenshot=False,
            fast=True,
            interval=(0.2, 0.3),
    ):
        """
        Args:
            index (int):
            letter (Ocr, callable): OCR button.
            next_button (Button):
            prev_button (Button):
            skip_first_screenshot (bool):
            fast (bool): Default true. False when index is not continuous.
            interval (tuple, int, float): Seconds between two click.
        """
        logger.hr("UI ensure index")
        retry = Timer(1, count=2)
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if isinstance(letter, Ocr):
                current = letter.ocr_single_line(self.device.image)
            else:
                current = letter(self.device.image)

            logger.attr("Index", current)
            diff = index - current
            if diff == 0:
                break
            if current == 0:
                logger.warning(f'ui_ensure_index got an empty current value: {current}')
                continue

            if retry.reached():
                button = next_button if diff > 0 else prev_button
                if fast:
                    self.device.multi_click(button, n=abs(diff), interval=interval)
                else:
                    self.device.click(button)
                retry.reset()

    def ui_click(
            self,
            click_button,
            check_button,
            appear_button=None,
            additional=None,
            retry_wait=5,
            skip_first_screenshot=True,
    ):
        """
        Args:
            click_button (ButtonWrapper):
            check_button (ButtonWrapper, callable, list[ButtonWrapper], tuple[ButtonWrapper]):
            appear_button (ButtonWrapper, callable, list[ButtonWrapper], tuple[ButtonWrapper]):
            additional (callable):
            retry_wait (int, float):
            skip_first_screenshot (bool):
        """
        if appear_button is None:
            appear_button = click_button
        logger.info(f'UI click: {appear_button} -> {check_button}')

        def process_appear(button):
            if isinstance(button, ButtonWrapper):
                return self.appear(button)
            elif callable(button):
                return button()
            elif isinstance(button, (list, tuple)):
                for b in button:
                    if self.appear(b):
                        return True
                return False
            else:
                return self.appear(button)

        click_timer = Timer(retry_wait, count=retry_wait // 0.5)
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # End
            if process_appear(check_button):
                break

            # Click
            if click_timer.reached():
                if process_appear(appear_button):
                    self.device.click(click_button)
                    click_timer.reset()
                    continue
            if additional is not None:
                if additional():
                    continue

    def is_in_main(self, interval=0):
        """
        判断是否在主界面且无弹窗遮盖

        双重判断：
        1. WHITE_STAR 模板匹配 → 确认在主页（只有主页有此元素）
        2. POPUP_OVERLAY 颜色匹配 → 确认无弹窗遮罩（MENU纯色区域，遮罩时变暗）

        Args:
            interval:

        Returns:
            bool:
        """
        self.device.stuck_record_add(WHITE_STAR)

        if interval and not self.interval_is_reached(WHITE_STAR, interval=interval):
            return False

        appear = False
        # 1. 模板匹配：主界面标志存在
        if WHITE_STAR.match_template_luma(self.device.image):
            # 2. 颜色检测：MENU 区域颜色正常（无遮罩）
            if POPUP_OVERLAY.match_color(self.device.image, threshold=30):
                appear = True

        if appear and interval:
            self.interval_reset(WHITE_STAR, interval=interval)

        return appear

    def is_in_login_confirm(self, interval=0):
        self.device.stuck_record_add(LOGIN_CONFIRM)

        if interval and not self.interval_is_reached(LOGIN_CONFIRM, interval=interval):
            return False

        appear = LOGIN_CONFIRM.match_template_luma(self.device.image)

        if appear and interval:
            self.interval_reset(LOGIN_CONFIRM, interval=interval)

        return appear

    def handle_login_confirm(self):
        """
        If LOGIN_CONFIRM appears, do as task `Restart` not just clicking it
        """
        if self.is_in_login_confirm(interval=0):
            logger.warning('Login page appeared')
            from tasks.login.login import Login
            Login(self.config, device=self.device).handle_app_login()
            raise HandledError
        return False

    def ui_goto_main(self):
        return self.ui_ensure(destination=page_main)

    def ui_additional(self) -> bool:
        """
        Handle all possible popups during UI switching.
        处理 UI 切换过程中所有可能的弹窗

        E7 入场顺序：
        1. 签到奖励（确认）
        2. 新英雄入池（可选择跳过 - 确认）
        3. Buff 广告
        4. 捆绑礼包（轻触关闭）

        Returns:
            If handled any popup.
        """
        # === E7 登录弹窗处理 ===

        # 0. 新通知/情报 - 一日内不再提示
        if self.appear_then_click(ANNOUNCEMENT_DONOT_REMIND, interval=2):
            logger.info('Closed announcement today')
            return True

        # 1. 签到奖励 - 点击确认
        if self.appear_then_click(CHECK_IN_CONFIRM, interval=2):
            logger.info('Closed check-in reward popup')
            return True

        # 2. 新英雄入池 - 先尝试跳过视频，再点击确认
        if self.appear_then_click(NEW_CHARACTER_VIDEO_PASS, interval=2):
            logger.info('Skipped new character video')
            return True
        if self.appear_then_click(NEW_CHARACTER_CONFIRM, interval=2):
            logger.info('Closed new character popup')
            return True

        # 3. Buff 广告 - 点击关闭
        if self.appear_then_click(AD_BUFF_CLOSE, interval=2):
            logger.info('Closed buff ad popup')
            return True

        # 4. 各种捆绑礼包/公告 - 轻触关闭
        if self.handle_touch_to_close():
            logger.info('Closed popup via touch to close')
            return True

        # === 通用弹窗处理 ===
        # if self.handle_popup_single():
        #     return True
        # if self.handle_popup_confirm():
        #     return True

        return False

    def _ui_button_confirm(
            self,
            button,
            confirm=Timer(0.1, count=0),
            timeout=Timer(2, count=6),
            skip_first_screenshot=True
    ):
        confirm.reset()
        timeout.reset()
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning(f'_ui_button_confirm({button}) timeout')
                break

            if self.appear(button):
                if confirm.reached():
                    break
            else:
                confirm.reset()

    def ui_page_confirm(self, page):
        """
        Args:
            page (Page):

        Returns:
            bool: If handled
        """
        if page == page_main:
            self._ui_button_confirm(page.check_button)
            return True

        return False

    def ui_button_interval_reset(self, button):
        """
        Reset interval of some button to avoid mistaken clicks

        Args:
            button (Button):
        """
        pass

    def ui_leave_special(self):
        """
        E7: 暂无特殊界面需要离开，预留接口
        """
        return False
