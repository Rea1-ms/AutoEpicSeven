"""Observed daily world-boss attempts and the stamina retained for them."""

import re
from datetime import timedelta

from module.config.utils import get_server_last_update, get_server_next_update
from module.logger import logger
from module.ocr.language import resolve_ocr_lang
from module.ocr.ocr import Ocr
from tasks.base.resource_bar import RESOURCE_BAR_LAYOUT_COMBAT
from tasks.base.resource_scheduling import (
    RESOURCE_RECOVERY_SECONDS,
    RESOURCE_REFRESH_RETRY_MINUTES,
    ResourceCoordinator,
    config_value,
    local_now,
)
from tasks.knights.assets.assets_knights_world_boss_entry_resources import (
    OCR_WORLD_BOSS_ATTEMPTS,
)

WORLD_BOSS_STAMINA_COST = 20
WORLD_BOSS_DAILY_ATTEMPTS = 2


def _world_boss_context(config):
    coordinator = ResourceCoordinator(config)
    day = get_server_last_update(config.Scheduler_ServerUpdate).isoformat()
    return coordinator, day


def record_world_boss_attempts(config, remaining):
    """Persist only observed attempts, partitioned by account/server/reset."""
    coordinator, day = _world_boss_context(config)
    if not coordinator.available:
        return
    root, bucket = coordinator._load()
    bucket['world_boss'] = {'day': day, 'remaining': remaining}
    coordinator._save(root)


def world_boss_stamina_reserve(config):
    if (not config_value(config, 'Knights.Scheduler.Enable', False)
            or not config_value(config, 'Knights.Knights.WorldBoss', False)):
        return 0
    coordinator, day = _world_boss_context(config)
    _, bucket = coordinator._load()
    observed = bucket.get('world_boss', {})
    # Until today's screen has been observed, retain the explicitly agreed
    # two-entry allowance. Never derive today's remainder from this process's
    # completed-round counter: the user may have played manually or restarted.
    remaining = observed.get('remaining') if observed.get('day') == day else None
    if remaining not in (0, 1, 2):
        remaining = WORLD_BOSS_DAILY_ATTEMPTS
    return remaining * WORLD_BOSS_STAMINA_COST


class WorldBossResourcesMixin:
    @staticmethod
    def _parse_world_boss_attempts(text):
        matched = re.fullmatch(r'([0-2])/2', re.sub(r'\s+', '', text))
        return int(matched[1]) if matched else None

    def _read_world_boss_attempts(self):
        text = Ocr(OCR_WORLD_BOSS_ATTEMPTS, lang=resolve_ocr_lang(self.config),
                   name='WorldBossRemainingAttempts').ocr_single_line(self.device.image)
        logger.attr('WorldBossAttemptsRaw', text)
        remaining = self._parse_world_boss_attempts(text)
        if remaining is not None:
            self._world_boss_remaining_attempts = remaining
            record_world_boss_attempts(self.config, remaining)
        return remaining

    def _read_world_boss_stamina(self):
        parsed = self.inspect_resource_bar_status(
            layout=RESOURCE_BAR_LAYOUT_COMBAT, layout_name='WorldBoss',
        ).final
        if parsed:
            ResourceCoordinator(self.config).observe(parsed, 'WorldBoss')
        return parsed.get('stamina') if parsed else None

    def _defer_world_boss(self, stamina=None):
        now = local_now().replace(microsecond=0)
        target = now + timedelta(minutes=RESOURCE_REFRESH_RETRY_MINUTES)
        if stamina is not None and stamina.value < WORLD_BOSS_STAMINA_COST <= stamina.total:
            target = now + timedelta(seconds=(WORLD_BOSS_STAMINA_COST - stamina.value)
                                     * RESOURCE_RECOVERY_SECONDS['stamina'])
        self._world_boss_retry_target = min(
            target, get_server_next_update(self.config.Scheduler_ServerUpdate),
        )
        if stamina is None:
            ResourceCoordinator(self.config).request('Knights', keys=('stamina',))

    def _schedule_world_boss_wait(self):
        if getattr(self, '_world_boss_retry_target', None) is not None:
            ResourceCoordinator(self.config).wait('Knights', 'stamina', WORLD_BOSS_STAMINA_COST)
