from __future__ import annotations

import json
import os
import time

from module.config.config import AzurLaneConfig
from module.config.deep import deep_get
from module.logger import logger


class CommunityAuth:
    """
    Wait for Electron community login to refresh local credentials.

    Polls a signal file written by Electron on login window close:
      - {"ok": true, ...}  → credentials captured, validate and finish
      - {"ok": false, ...} → user closed window without logging in

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
    def _signal_path(config) -> str:
        from community.aio import get_login_result_path
        return get_login_result_path(config.config_name)

    @staticmethod
    def _read_signal(signal_path: str) -> dict | None:
        if not os.path.exists(signal_path):
            return None
        try:
            with open(signal_path, "r", encoding="utf-8") as fp:
                return json.load(fp)
        except (OSError, json.JSONDecodeError):
            return None

    @staticmethod
    def _credentials_valid(credentials_path: str) -> tuple[bool, str]:
        from community.aio import get_token_expiry, load_credentials_file

        if not os.path.exists(credentials_path):
            return False, "Credential file not found after capture"

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

        return True, "Credentials captured and valid"

    def run(self) -> bool:
        logger.hr("Community Auth", level=1)
        credentials_path = self._credentials_path(self.config)
        signal_path = self._signal_path(self.config)
        wait_timeout = int(getattr(self.config, "CommunityAuth_WaitTimeout", 300) or 300)
        logger.attr("WaitTimeout", f"{wait_timeout}s")

        # Clean stale signal so we only react to this session's result
        if os.path.exists(signal_path):
            try:
                os.remove(signal_path)
            except OSError:
                pass

        logger.info("Waiting for Electron login window result...")

        deadline = time.time() + max(wait_timeout, 1)
        while time.time() < deadline:
            signal = self._read_signal(signal_path)
            if signal is not None:
                if signal.get("ok"):
                    ok, message = self._credentials_valid(credentials_path)
                    logger.info(message)
                    return ok
                else:
                    logger.info("Login window closed without capturing credentials.")
                    return False
            time.sleep(1)

        logger.warning("Community credential capture timed out.")
        return False


def run_tool(config_name: str) -> bool:
    config = AzurLaneConfig(config_name, task="CommunityAuth")
    return CommunityAuth(config).run()
