"""Synthetic arena entry observations with real intervals and entry handlers."""

from contextlib import ExitStack, contextmanager
from unittest.mock import Mock, patch

from module.config import server
from tests.support.interaction import InteractionReplay

server.set_lang('global_cn')

from tasks.arena.arena import Arena
from tasks.arena.assets.assets_arena import (
    ARENA_CHECK,
    SETTLING_INFO_ICON,
)


class ArenaEntryReplay(InteractionReplay):
    """Separate visible templates from dimmed or disabled controls."""

    def __init__(self, frames, clock):
        super().__init__(Arena, frames, clock)
        self.task.config.Emulator_PackageName = 'OVERSEA-Play'
        self.task.match_template_color = self.match_template_color
        self.task.ui_goto = Mock()
        self.task.handle_touch_to_close = Mock(return_value=False)
        self.task.handle_network_error = Mock(return_value=False)

    def template_visible(self, button, _image, **_kwargs):
        return button.name in self.frame['buttons']

    def color_ready(self, button, _image, **_kwargs):
        return (
            button.name in self.frame['buttons']
            and button.name not in self.frame.get('disabled', ())
        )

    def match_template_color(self, button, interval=0, **kwargs):
        if not self.color_ready(button, self.image):
            return False
        return self.appear(button, interval=interval, **kwargs)


@contextmanager
def entry_replay(frames, clock):
    """Retain the production page-ready checks; replace only their observations."""
    replay = ArenaEntryReplay(frames, clock)
    with ExitStack() as stack:
        for button in (ARENA_CHECK, SETTLING_INFO_ICON):
            stack.enter_context(patch.object(
                button, 'match_template_luma',
                side_effect=lambda image, asset=button, **kwargs: replay.template_visible(asset, image, **kwargs),
            ))
            stack.enter_context(patch.object(
                button, 'match_color',
                side_effect=lambda image, asset=button, **kwargs: replay.color_ready(asset, image, **kwargs),
            ))
        yield replay
