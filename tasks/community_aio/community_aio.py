from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from module.base.base import ModuleBase
from module.config.utils import get_server_next_update
from module.logger import logger


class CommunityAio(ModuleBase):
    """
    Run community/aio.py once and schedule next execution at server 00:00.

    This task is intentionally script-driven (no in-game UI operations).
    """

    @staticmethod
    def _repo_root() -> Path:
        return Path(__file__).resolve().parents[2]

    @classmethod
    def _script_path(cls) -> Path:
        return cls._repo_root() / "community" / "aio.py"

    @staticmethod
    def _next_midnight() -> object:
        # Use server-time utilities so scheduler behavior remains consistent with ALAS.
        return get_server_next_update("00:00")

    @staticmethod
    def _iter_lines(text: str) -> list[str]:
        return [line for line in text.splitlines() if line.strip()]

    @classmethod
    def _build_command(cls, script_path: Path, credentials_file: str = "") -> list[str]:
        # Reuse current interpreter to keep dependencies/environment aligned with AES runtime.
        command = [sys.executable, str(script_path)]
        if credentials_file:
            command.extend(["--credentials-file", credentials_file])
        return command

    def run(self) -> bool:
        logger.hr("Community AIO", level=1)

        script_path = self._script_path()
        if not script_path.is_file():
            logger.error(f"CommunityAio: script not found: {script_path}")
            self.config.task_delay(success=False)
            return False

        credentials_file = str(getattr(self.config, "CommunityAio_CredentialsFile", "") or "").strip()
        command = self._build_command(script_path, credentials_file=credentials_file)
        logger.attr("CommunityAioCmd", " ".join(command))

        env = os.environ.copy()
        env.setdefault("PYTHONIOENCODING", "utf-8")
        result = subprocess.run(
            command,
            cwd=str(self._repo_root()),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        for line in self._iter_lines(result.stdout or ""):
            logger.info(f"[CommunityAio] {line}")
        for line in self._iter_lines(result.stderr or ""):
            logger.warning(f"[CommunityAio][stderr] {line}")

        logger.attr("CommunityAioExitCode", result.returncode)
        if result.returncode == 0:
            # Business requirement: run once per day at 00:00.
            self.config.task_delay(target=self._next_midnight())
            return True

        self.config.task_delay(success=False)
        return False
