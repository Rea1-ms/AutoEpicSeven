import module.config.server as server
from module.exception import ScriptError
from module.logger import logger
from tasks.base.page import Page, page_main
from tasks.base.popup import PopupHandler


class MainPage(PopupHandler):
    _lang_checked = False
    _lang_check_success = True

    def check_lang_from_map_plane(self) -> str | None:
        """
        AES no longer uses HSR map-plane OCR to infer assets language.

        The active assets language is already derived from
        `Emulator_PackageName` + `Emulator_GameLanguage` during device
        connection, so this hook only marks the check as completed and keeps
        the existing UI flow intact.
        """
        logger.info(f'check_lang_from_map_plane skipped, use configured lang={server.lang}')
        MainPage._lang_checked = True
        MainPage._lang_check_success = True
        return server.lang

    def handle_lang_check(self, page: Page):
        """
        Args:
            page:

        Returns:
            bool: If checked
        """
        if MainPage._lang_checked:
            return False
        if page != page_main:
            return False

        self.check_lang_from_map_plane()
        return True

    def acquire_lang_checked(self):
        """
        Returns:
            bool: If checked
        """
        if MainPage._lang_checked:
            return False

        logger.info('acquire_lang_checked')
        try:
            self.ui_goto(page_main)
        except AttributeError:
            logger.critical('Method ui_goto() not found, class MainPage must be inherited by class UI')
            raise ScriptError

        self.check_lang_from_map_plane()
        return True
