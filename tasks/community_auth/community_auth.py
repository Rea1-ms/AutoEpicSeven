from __future__ import annotations

import os
import time

from module.config.config import AzurLaneConfig
from module.config.deep import deep_get
from module.logger import logger


class CommunityAuth:
    """
    Wait for Electron community login to refresh local credentials.

    Pages:
        in: WebUI Tool page
        out: WebUI Tool page
    """

    def __init__(self, config):
        self.config = config

    @staticmethod
    def _credentials_path(config) -> str:
        from community.aio import get_default_credentials_path

        configured_path = str(
            getattr(config, "CommunityAuth_CredentialsFile", "")
            or getattr(config, "CommunityAio_CredentialsFile", "")
            or deep_get(config.data, "CommunityAio.CommunityAio.CredentialsFile", default="")
            or ""
        ).strip()
        return configured_path or get_default_credentials_path(config.config_name)

    @staticmethod
    def _credentials_status(credentials_path: str) -> tuple[bool, str]:
        from community.aio import get_token_expiry, load_credentials_file

        if not os.path.exists(credentials_path):
            return False, "Credential file not found yet"

        try:
            credentials = load_credentials_file(credentials_path)
        except ValueError as exc:
            return False, f"Credential file is invalid: {exc}"

        missing = [
            key for key in ("token", "pd_did", "pd_dvid")
            if not credentials.get(key)
        ]
        if missing:
            return False, f"Credential file is incomplete: missing {', '.join(missing)}"

        expiry = get_token_expiry(credentials["token"])
        if expiry is not None and expiry <= int(time.time()):
            return False, "Credential token is expired"

        return True, "Credential file is ready"

    def run(self) -> bool:
        logger.hr("Community Auth", level=1)
        credentials_path = self._credentials_path(self.config)
        wait_timeout = int(getattr(self.config, "CommunityAuth_WaitTimeout", 300) or 300)
        logger.attr("CredentialsFile", credentials_path)
        logger.attr("WaitTimeout", f"{wait_timeout}s")
        logger.info("Please finish login in the Electron community login window.")

        deadline = time.time() + max(wait_timeout, 1)
        last_message = None
        while time.time() < deadline:
            ok, message = self._credentials_status(credentials_path)
            if message != last_message:
                logger.info(message)
                last_message = message
            if ok:
                logger.info("Community credential capture completed.")
                return True
            time.sleep(1)

        logger.warning("Community credential capture timed out.")
        return False


def run_tool(config_name: str) -> bool:
    config = AzurLaneConfig(config_name, task="CommunityAuth")
    return CommunityAuth(config).run()
