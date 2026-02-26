from typing import Callable

from module.base.base import ModuleBase
from module.base.utils import color_similarity_2d
from module.logger import logger
from module.ocr.ocr import Duration, OcrWhiteLetterOnComplexBackground
from tasks.base.assets.assets_base_page import BACK
from tasks.base.assets.assets_base_popup import *


class PopupHandler(ModuleBase):
    # def reward_appear(self) -> bool:
    #     for button in GET_REWARD.buttons:
    #         image = self.image_crop(button.search, copy=False)
    #         image = color_similarity_2d(image, color=(203, 181, 132))
    #         if button.match_template(image, direct_match=True):
    #             return True
    #     return False
    #
    # def handle_reward(self, interval=5, click_button: ButtonWrapper = None) -> bool:
    #     """
    #     Args:
    #         interval:
    #         click_button: Set a button to click
    #
    #     Returns:
    #         If handled.
    #     """
    #     # Same as ModuleBase.match_template()
    #     self.device.stuck_record_add(GET_REWARD)
    #
    #     if interval and not self.interval_is_reached(GET_REWARD, interval=interval):
    #         return False
    #
    #     appear = self.reward_appear()
    #
    #     if click_button is None:
    #         if appear:
    #             self.device.click(GET_REWARD)
    #     else:
    #         if appear:
    #             logger.info(f'{GET_REWARD} -> {click_button}')
    #             self.device.click(click_button)
    #
    #     if appear and interval:
    #         self.interval_reset(GET_REWARD, interval=interval)
    #
    #     return appear
    #
    # def handle_battle_pass_notification(self, interval=5) -> bool:
    #     """
    #     Popup notification that you enter battle pass the first time.
    #
    #     Args:
    #         interval:
    #
    #     Returns:
    #         If handled.
    #     """
    #     if self.appear_then_click(BATTLE_PASS_NOTIFICATION, interval=interval):
    #         return True
    #     if self.appear(POPUP_BATTLE_PASS_UPDATE, interval=interval):
    #         logger.info(f'{POPUP_BATTLE_PASS_UPDATE} -> {BATTLE_PASS_NOTIFICATION}')
    #         self.device.click(BATTLE_PASS_NOTIFICATION)
    #         return True
    #
    #     return False
    #
    # def handle_monthly_card_reward(self, interval=1) -> bool:
    #     """
    #     Popup at 04:00 server time if you have purchased the monthly card.
    #
    #     Args:
    #         interval:
    #
    #     Returns:
    #         If handled.
    #     """
    #     if self.appear_then_click(MONTHLY_CARD_REWARD, interval=interval):
    #         # Language check at the first login of the day may fail due to popups
    #         # Retry later
    #         from tasks.base.main_page import MainPage
    #         if not MainPage._lang_check_success:
    #             MainPage._lang_checked = False
    #         return True
    #     if self.appear_then_click(MONTHLY_CARD_GET_ITEM, interval=interval):
    #         from tasks.base.main_page import MainPage
    #         if not MainPage._lang_check_success:
    #             MainPage._lang_checked = False
    #         return True
    #
    #     return False
    #
    def handle_popup_cancel(self, interval=2) -> bool:
        """
        Args:
            interval:

        Returns:
            If handled.
        """
        if self.appear_then_click(POPUP_CANCEL, interval=interval):
            return True

        return False

    def handle_popup_confirm(self, interval=2) -> bool:
        """
        Args:
            interval:

        Returns:
            If handled.
        """
        if self.appear_then_click(POPUP_CONFIRM, interval=interval):
            return True

        return False

    def handle_broadcast(self, interval=1) -> bool:
        """
        Handle chained broadcast popups.

        Flow:
            1) Click BROADCAST entry button to open detail.
            2) Click POPUP_CONFIRM to close the current detail.
            3) If there are more broadcast details, outer state loop will call this
               again and continue until all are cleared.

        Returns:
            If handled.
        """
        if not hasattr(self, '_broadcast_confirm_pending'):
            self._broadcast_confirm_pending = False
        # if not hasattr(self, '_broadcast_ocr_logged'):
        #     self._broadcast_ocr_logged = False

        if self.appear_then_click(BROADCAST, interval=interval):
            self._broadcast_confirm_pending = True
            # self._broadcast_ocr_logged = False
            logger.info('Broadcast opened')
            return True

        if self._broadcast_confirm_pending:
            # if not self._broadcast_ocr_logged:
            #     self.log_broadcast_ocr()
            #     self._broadcast_ocr_logged = True

            if self.appear_then_click(POPUP_CONFIRM, interval=interval):
                # self._broadcast_ocr_logged = False
                logger.info('Broadcast confirmed')
                return True

            if not self.appear(BROADCAST) and not self.appear(POPUP_CONFIRM):
                self._broadcast_confirm_pending = False

        return False

    def _broadcast_ocr_lang(self) -> str:
        lang = getattr(self.config, 'Emulator_GameLanguage', 'cn')
        if lang in ('auto', '', None):
            return 'cn'
        if lang in ('global_cn', 'zh', 'zh_cn'):
            return 'cn'
        if lang in ('global_en', 'en_us', 'en'):
            return 'en'
        return 'cn'

    def log_broadcast_ocr(self):
        lang = self._broadcast_ocr_lang()

        content_ocr = OcrWhiteLetterOnComplexBackground(
            OCR_BROADCAST_CONTENT, lang=lang, name='BroadcastContentOCR'
        )
        content = content_ocr.ocr_single_line(self.device.image)

        remain_ocr = OcrWhiteLetterOnComplexBackground(
            OCR_BROADCAST_REMAIN_TIME, lang=lang, name='BroadcastRemainOCR'
        )
        remain_text = remain_ocr.ocr_single_line(self.device.image)
        remain_duration = Duration(
            OCR_BROADCAST_REMAIN_TIME, lang=lang, name='BroadcastRemainDuration'
        ).format_result(remain_text)
        remain_minutes = int(remain_duration.total_seconds() // 60)

        logger.info(f'Broadcast OCR content: {content}')
        if remain_duration.total_seconds() > 0:
            logger.info(f'Broadcast OCR remain: {remain_text} ({remain_minutes} min)')
        else:
            logger.info(f'Broadcast OCR remain: {remain_text}')
    #
    # def handle_popup_single(self, interval=2) -> bool:
    #     """
    #     Popup with one single confirm button in the middle.
    #
    #     Args:
    #         interval:
    #
    #     Returns:
    #         If handled.
    #     """
    #     if self.appear_then_click(POPUP_SINGLE, interval=interval):
    #         return True
    #
    #     return False
    #
    # def handle_get_light_cone(self, interval=2) -> bool:
    #     """
    #     Popup when getting a light cone from Echo of War.
    #
    #     Args:
    #         interval:
    #
    #     Returns:
    #         If handled.
    #     """
    #     if self.appear(GET_LIGHT_CONE, interval=interval):
    #         logger.info(f'{GET_LIGHT_CONE} -> {GET_REWARD}')
    #         self.device.click(GET_REWARD)
    #         return True
    #
    #     return False
    #
    # def handle_get_character(self, interval=2) -> bool:
    #     """
    #     Popup when getting a character from rogue rewards.
    #
    #     Args:
    #         interval:
    #
    #     Returns:
    #         If handled.
    #     """
    #     if self.appear(GET_CHARACTER, interval=interval):
    #         logger.info(f'{GET_CHARACTER} -> {GET_REWARD}')
    #         self.device.click(GET_REWARD)
    #         return True
    #
    #     return False
    #
    # def handle_forgotten_hall_buff(self, interval=2):
    #     """
    #     Handle buff popups when entering forgotten hall
    #
    #     Returns:
    #         bool: If handled.
    #     """
    #     if self.match_template_luma(BUFF_Forgotten_Hall, interval=interval):
    #         logger.info(f'{BUFF_Forgotten_Hall} -> {BUFF_CLICK}')
    #         self.device.click(BUFF_CLICK)
    #         return True
    #     if self.match_template_luma(BUFF_Pure_Fiction, interval=interval):
    #         logger.info(f'{BUFF_Pure_Fiction} -> {BUFF_CLICK}')
    #         self.device.click(BUFF_CLICK)
    #         return True
    #     if self.match_template_luma(BUFF_Apocalyptic_Shadow, interval=interval):
    #         logger.info(f'{BUFF_Apocalyptic_Shadow} -> {BUFF_CLICK}')
    #         self.device.click(BUFF_CLICK)
    #         return True
    #     return False

    # def handle_ui_close(self, appear_button: ButtonWrapper | Callable, interval=2) -> bool:
    #     """
    #     Args:
    #         appear_button: Click if button appears
    #         interval:
    #
    #     Returns:
    #         If handled.
    #     """
    #     if callable(appear_button):
    #         if self.interval_is_reached(appear_button, interval=interval) and appear_button():
    #             logger.info(f'{appear_button.__name__} -> {CLOSE}')
    #             self.device.click(CLOSE)
    #             self.interval_reset(appear_button, interval=interval)
    #             return True
    #     else:
    #         if self.appear(appear_button, interval=interval):
    #             logger.info(f'{appear_button} -> {CLOSE}')
    #             self.device.click(CLOSE)
    #             return True
    #
    #     return False
    #
    def handle_ui_back(self, appear_button: ButtonWrapper | Callable, interval=2) -> bool:
        """
        Args:
            appear_button: Click if button appears
            interval:

        Returns:
            If handled.
        """
        if callable(appear_button):
            if self.interval_is_reached(appear_button, interval=interval) and appear_button():
                logger.info(f'{appear_button.__name__} -> {BACK}')
                self.device.click(BACK)
                self.interval_reset(appear_button, interval=interval)
                return True
        else:
            if self.appear(appear_button, interval=interval):
                logger.info(f'{appear_button} -> {BACK}')
                self.device.click(BACK)
                return True

        return False

    # E7
    def handle_touch_to_close(self, interval=2) -> bool:
        """
        Handle "Touch to close" popup in Epic Seven.

        Common popup that appears in various situations:
        - Game update notification
        - Network errors
        - Friendship points arrival

        Args:
            interval:

        Returns:
            If handled.
        """
        if self.appear_then_click(TOUCH_TO_CLOSE, interval=interval):
            return True
        return False

    def handle_network_error(self, interval=5) -> bool:
        """
        Handle network error popups in Epic Seven.

        Case 1: "与服务器的连接已中断" (Server connection lost)
            -> Click retry -> Will redirect to login
        Case 2: "网络连接异常" (Network connection abnormal)
        这里我为了偷懒，把 “设备时间异常” 作为同样逻辑处理了，反正都是需要踢回来到登录界面的
            -> Wait 3s -> Click retry -> Stay in place

        Args:
            interval:

        Returns:
            If handled.
        """
        # Case 1: Server connection lost
        if self.appear(NETWORK_ERROR_DISCONNECT, interval=interval):
            logger.warning('Network disconnected, clicking retry')
            self.device.click(TOUCH_TO_CLOSE)
            return True

        # Case 2: Network abnormal (need to wait before retry)
        if self.appear(NETWORK_ERROR_ABNORMAL, interval=interval):
            logger.warning('Network abnormal, waiting 3s before retry')
            self.device.sleep(3)
            self.device.click(TOUCH_TO_CLOSE)
            return True

        return False

    def handle_ad_buff_x_close(self, interval=2) -> bool:
        """
        Handle common ad/buff popup that has an "X" close button.

        Returns:
            If handled.
        """
        if self.appear_then_click(AD_BUFF_X_CLOSE, interval=interval):
            return True
        return False
