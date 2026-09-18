"""Adapt the application's server setting to the independent game-info reader."""

from datetime import datetime

from module.config.server import server_family
from module.game_info.catalog import load_info


def level_cap(config, kind: str, now: datetime | None = None) -> int:
    return load_info().level_cap(kind, server_family(config.Emulator_PackageName), now)
