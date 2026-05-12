from __future__ import annotations

import time

from module.base.base import ModuleBase
from module.base.utils import ensure_time
from module.config.config import TaskEnd
from module.config.utils import get_server_next_update
from module.logger import logger


class CommunityAio(ModuleBase):
    """
    Run community/aio.py once and schedule next execution at server 00:00.

    This task is intentionally script-driven (no in-game UI operations).
    """

    @staticmethod
    def _next_midnight() -> object:
        # Use server-time utilities so scheduler behavior remains consistent with ALAS.
        return get_server_next_update("00:00")

    @staticmethod
    def _log_output(message: str) -> None:
        for line in str(message).splitlines():
            if line.strip():
                logger.info(f"[CommunityAio] {line}")

    def _check_stop(self) -> None:
        stop_event = getattr(self.config, "stop_event", None)
        if stop_event is not None and stop_event.is_set():
            raise TaskEnd("CommunityAio stopped")

    def _sleep_with_stop(self, second) -> None:
        remain = ensure_time(second)
        while remain > 0:
            self._check_stop()
            chunk = min(remain, 0.2)
            self.device.sleep(chunk)
            remain -= chunk

    def run(self) -> bool:
        logger.hr("Community AIO", level=1)
        try:
            from community.aio import (
                DEFAULT_BROWSE_TARGET,
                DEFAULT_DELAY_MAX,
                DEFAULT_DELAY_MIN,
                DEFAULT_GOODS_PAGE_SIZE,
                DEFAULT_GOODS_PAGES,
                DEFAULT_LIKE_TARGET,
                DEFAULT_SHARE_TARGET,
                DEFAULT_TIMEOUT,
                DEFAULT_TOPIC_PAGE_SIZE,
                DEFAULT_TOPIC_PAGES,
                EpicSevenCommunityAIO,
                get_token_expiry,
                resolve_credentials_inputs,
            )

            credentials_file = str(getattr(self.config, "CommunityAio_CredentialsFile", "") or "").strip()
            user_id, auth_token, pd_did, pd_dvid, jsessionid = resolve_credentials_inputs(
                credentials_file=credentials_file or None,
                config_name=self.config.config_name,
            )
            token_expiry = get_token_expiry(auth_token)
            if token_expiry is not None and token_expiry <= int(time.time()):
                raise ValueError("提供的 token 已过期，请先刷新后再运行")

            bot = EpicSevenCommunityAIO(
                user_id=user_id,
                auth_token=auth_token,
                jsessionid=jsessionid,
                pd_did=pd_did,
                pd_dvid=pd_dvid,
                delay_min=DEFAULT_DELAY_MIN,
                delay_max=DEFAULT_DELAY_MAX,
                timeout=DEFAULT_TIMEOUT,
                sleep_func=self._sleep_with_stop,
                log_func=self._log_output,
                stop_checker=self._check_stop,
            )
            summary = bot.run(
                browse_target=DEFAULT_BROWSE_TARGET,
                like_target=DEFAULT_LIKE_TARGET,
                share_target=DEFAULT_SHARE_TARGET,
                topic_pages=DEFAULT_TOPIC_PAGES,
                topic_page_size=DEFAULT_TOPIC_PAGE_SIZE,
                goods_pages=DEFAULT_GOODS_PAGES,
                goods_page_size=DEFAULT_GOODS_PAGE_SIZE,
                skip_actions=False,
                skip_exchange=False,
            )
        except TaskEnd:
            raise
        except ValueError as exc:
            logger.error(f"CommunityAio: {exc}")
            self.config.task_delay(success=False)
            return False
        except Exception:
            logger.exception("CommunityAio failed")
            self.config.task_delay(success=False)
            return False

        logger.attr("CommunityAioSign", summary.get("sign_message"))
        if summary.get("skipped_reason"):
            logger.attr("CommunityAioSkipped", summary["skipped_reason"])

        # Business requirement: run once per day at 00:00.
        self.config.task_delay(target=self._next_midnight())
        return True
