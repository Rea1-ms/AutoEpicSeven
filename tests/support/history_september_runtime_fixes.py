# ruff: noqa: E402
from module.config import server as _test_server

_test_server.set_lang("global_cn")


"""Offline regressions for the September 18 runtime logs; never connects to a device."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

WORKTREE = Path(__file__).resolve().parents[2]
PROJECT_ROOT = WORKTREE
import module.config.server as server
server.set_lang("global_cn")
from tasks.base.ui import UI
from tasks.dungeon.prepare import CombatPrepare
from tasks.store.current import CurrentStore


def timers(clock):
    class Timer:
        def __init__(self, limit, count=0):
            self.limit, self.deadline = limit, None
        def start(self):
            if self.deadline is None:
                self.reset()
            return self
        def reset(self):
            self.deadline = clock() + self.limit
            return self
        def clear(self):
            self.deadline = None
            return self
        def started(self):
            return self.deadline is not None
        def reached(self):
            return self.started() and clock() >= self.deadline
    return Timer


class Frames:
    def __init__(self, frames):
        self.frames, self.index = frames, -1
        self.device = SimpleNamespace(screenshot=self.screenshot, image=None)
        self.screenshot()
    def screenshot(self):
        self.index += 1
        if self.index >= len(self.frames):
            raise AssertionError("Flow did not finish on supplied screenshots")
        self.device.image = self.frames[self.index]
    @property
    def frame(self):
        return self.frames[self.index]


class FakePage:
    def __init__(self, name, parent=None):
        self.name, self.parent, self.check_button = name, parent, name
        self.links_need_match = set()
    def __str__(self):
        return self.name


class Navigation(Frames, UI):
    def __init__(self, frames):
        Frames.__init__(self, frames)
        self.clicks = []
        self.recoveries = 0
        self.device.click = lambda button: self.clicks.append((self.frame['at'], button))
    def _ui_build_dynamic_links(self):
        return {}
    def interval_clear(self, *args):
        pass
    def ui_page_appear(self, page, interval=0):
        assert interval == 0, "Recognition must not be hidden by click cooldown"
        return page in self.frame['pages']
    def ui_page_confirm(self, page):
        return False
    def _ui_get_link_button(self, page, target):
        return f"{page}->{target}"
    def _ui_record_transition(self, *args):
        pass
    def ui_button_interval_reset(self, *args):
        pass
    def handle_ui_recovery(self):
        active = self.frame.get('network', False)
        self.recoveries += int(active)
        return active
    def ui_additional(self):
        return False
    def handle_popup_confirm(self):
        return False




class Prepare(Frames, CombatPrepare):
    def __init__(self, frames):
        Frames.__init__(self, frames)
        self.actions = []
    def _is_prepare_page(self):
        return self.frame in ('off','ready')
    def _is_fast_combat_on(self):
        return self.frame == 'ready'
    def _handle_dungeon_additional(self):
        if self.frame == 'confirm':
            self.actions.append('confirm')
            return True
        return False
    def _is_fast_combat_locked(self):
        return False
    def _ensure_fast_combat_state(self, enabled):
        if self.frame == 'off':
            self.actions.append('enable')
            return False
        return True
    def _ocr_fast_combat_remaining_times(self):
        assert self.frame == 'ready'
        return 10
    def _combat_stage_stamina_cost(self):
        return 20
    def _combat_fast_count(self):
        return 10
    def _set_prepare_count(self, target, *args, **kwargs):
        self.actions.append(('count',target))
        return True




class Store(Frames, CurrentStore):
    def __init__(self, frames):
        Frames.__init__(self, frames)
        self.index = -1  # purchase loop always requests a fresh screenshot
        self.closes, self.network_calls, self.confirms = [], [], []
        self._log_purchase_debug = Mock()
    def appear(self, button, **kwargs):
        if button.name == 'TOUCH_TO_CLOSE':
            return self.frame in ('reward','reward_wait','network','network_wait')
        if button.name == 'NETWORK_ERROR_ABNORMAL':
            return self.frame in ('network','network_wait')
        return False
    def handle_network_error(self, **kwargs):
        self.network_calls.append(self.index)
        return self.frame == 'network'
    def handle_touch_to_close(self, **kwargs):
        if self.frame == 'reward':
            self.closes.append(self.index)
            return True
        return False
    def ui_additional(self):
        return False
    def _detect_purchase_popup_layout(self):
        return 'single' if self.frame == 'confirm' else 'unknown'
    def _is_on_any_store_page(self):
        return self.frame in ('item','store','reward','reward_wait','network','network_wait')
    def _has_item(self, *args):
        return self.frame != "loading"
    def _click_purchase_target(self, item):
        return self.frame == 'item'
    def _click_purchase_confirm(self, layout, **kwargs):
        if self.frame == 'confirm' and layout != 'unknown':
            self.confirms.append(self.index)
            return True
        return False

__all__ = ['Frames', 'Prepare', 'timers']
