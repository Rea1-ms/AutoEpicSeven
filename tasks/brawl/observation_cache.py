"""Reuse verified panel text only inside one explicitly owned brawl run."""

from contextlib import contextmanager
from contextvars import ContextVar

import numpy as np

_ACTIVE_PANEL_CACHE = ContextVar("brawl_panel_cache", default=None)
_CHANGE_SCENES = frozenset((
    "combat", "round_clear", "round_failed", "hero_result", "hero_unlock",
    "rank_up", "level_up", "transmit_selection", "run_victory", "run_defeat", "lobby",
))


class PanelObservationCache:
    """Hold one measured panel and its current numeric glyph evidence.

    This cache owns no expected balances and never estimates a transaction.
    Every hit still requires a recognized current scene/mode, freshly located
    currency icons and identical glyphs in all five fields. Dark arena pixels
    behind the round label may animate without changing those glyphs. Keeping
    only the last signature means an old balance cannot reappear from a memo
    table after a purchase or a new round. Unknown frames do not renew or clear
    the record; they cannot retrieve it either. The explicit run scope closes
    on exceptions, so a restart cannot inherit previous account observations.
    """

    def __init__(self):
        self._key = None
        self._panel = None
        self._scene = None
        self._mode = None

    def invalidate(self):
        self._key = self._panel = None

    def observe_scene(self, scene, mode):
        if scene == "unknown":
            return
        if ((self._scene != scene and (scene in _CHANGE_SCENES or self._scene in _CHANGE_SCENES))
                or (mode is not None and self._mode is not None and mode != self._mode)):
            self.invalidate()
        self._scene = scene
        if mode is not None:
            self._mode = mode

    @staticmethod
    def signature(regions):
        signatures = []
        for region in regions:
            # The supported headers use white/yellow text; deployment numbers
            # are gray and retain their strokes above 130. Multiple thresholds
            # retain antialiasing evidence without incorporating the animated
            # dark backdrop. Exact equality is intentional: a changed digit,
            # displaced row or partial occlusion must cause another actual read.
            brightness = region.min(axis=2)
            masks = tuple(brightness > threshold for threshold in (130, 145, 180))
            if np.count_nonzero(masks[0]) < 4:
                # Upgrade dialogs dim the header below the normal text
                # thresholds. Keep exact pixels there instead of lowering a
                # glyph threshold into the arena background. This can only
                # reuse an identical region after a successful actual read;
                # a blank/unread field still fails the parser and is not saved.
                signatures.append((region.shape, "pixels", region.tobytes()))
            else:
                signatures.append((region.shape, "glyphs", *(np.packbits(mask).tobytes() for mask in masks)))
        return tuple(signatures)

    def read(self, mode, areas, regions, reader):
        signature = self.signature(regions)
        key = (mode, tuple(areas), signature)
        if signature is not None and key == self._key:
            return self._panel
        # Failed reads must not leave an older value available under a later
        # lookalike frame. Only a fully parsed new observation creates a record.
        self.invalidate()
        panel = reader()
        if signature is not None:
            self._key, self._panel = key, panel
        return panel


def active_panel_observation_cache():
    return _ACTIVE_PANEL_CACHE.get()


def invalidate_panel_observation_cache():
    cache = active_panel_observation_cache()
    if cache is not None:
        cache.invalidate()


@contextmanager
def panel_observation_cache():
    """Start an isolated observation lifetime; nested calls also start fresh."""
    cache = PanelObservationCache()
    token = _ACTIVE_PANEL_CACHE.set(cache)
    try:
        yield cache
    finally:
        cache.invalidate()
        _ACTIVE_PANEL_CACHE.reset(token)
