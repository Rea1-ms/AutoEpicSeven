"""Registered donation captures and explicit frames with real action intervals."""

from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np

# The caller selects its server before importing business modules. In
# particular, isolated CN capture checks must not silently use global assets.
from module.base.button import ClickButton
from tasks.base.assets.assets_base_popup import TOUCH_TO_CLOSE
from tasks.base.ui import UI
from tasks.knights.donate import DonationQuote
from tasks.knights.support import KnightsSupportMixin
from tests.support.interaction import InteractionReplay, scene
from tests.support.offline import ROOT, fixture_image

MANIFEST = ROOT / 'tests/fixtures/knights/manifest.json'
PENGUIN = 'beginner_penguin'
FLOWER = 'lower_level_fairy_flower'


class DonationTask(KnightsSupportMixin, UI):
    pass


def pixel_task(capture):
    task = object.__new__(DonationTask)
    task.config = SimpleNamespace(Emulator_GameLanguage='auto',
                                  Knights_SupportLowerLevelFairyFlower=True,
                                  Knights_SupportBeginnerPenguin=True)
    task.device = SimpleNamespace(image=fixture_image(MANIFEST, capture),
                                  stuck_record_add=lambda *args: None)
    task.interval_timer = {}
    task._prepare_support_search_area()
    return task


def listing(*rows, view=0, stocks=None, seconds=0.5, completed=1, bottom=False):
    """Rows are item, stock, quantity, recipient and action Y; one completed row is visible by default."""
    buttons = ('SUPPORT_CHECK', 'HIT_BOTTOM') if bottom else ('SUPPORT_CHECK',)
    return scene(*buttons, rows=rows, view=view, stocks=stocks, seconds=seconds, completed=completed)


def quote(row):
    item, stock, quantity, recipient, center = row
    return DonationQuote(item, ClickButton((1082, center - 10, 1123, center + 10),
                                           name=f'donate_{recipient}'),
                         stock, quantity, recipient.encode())


class DonationReplay(InteractionReplay):
    def __init__(self, frames, clock, *, penguin=True, flower=True):
        super().__init__(DonationTask, frames, clock, guards=False)
        self.task.config.Knights_SupportLowerLevelFairyFlower = flower
        self.task.config.Knights_SupportBeginnerPenguin = penguin
        self.task._prepare_support_search_area()
        self.task.is_in_main = Mock(return_value=False)
        self.task.handle_network_error = self.network
        self.task.handle_touch_to_close = lambda interval=2: self.task.appear_then_click(
            TOUCH_TO_CLOSE, interval=interval)
        self.task._donation_page_ready = self.page_ready
        self.task._donation_view = self.view
        self.task._donation_list_entries = self.entries
        self.task._find_donate_action_pairs = self.pairs
        self.task._donation_quote = self.read_quote
        self.task._donation_identity = self.identity
        self.task._donation_inventory = self.inventory
        self.task._donation_text = self.read_text

    def screenshot(self):
        super().screenshot()
        if 'capture' in self.frame:
            self.image = fixture_image(MANIFEST, self.frame['capture'])

    def appear(self, button, interval=0, **kwargs):
        if isinstance(self.image, np.ndarray):
            if interval and not self.task.interval_is_reached(button, interval=interval):
                return False
            found = button.match_template(self.image)
            if found and interval:
                self.task.interval_reset(button, interval=interval)
            return found
        return super().appear(button, interval=interval, **kwargs)

    def page_ready(self):
        if isinstance(self.image, np.ndarray):
            return KnightsSupportMixin._donation_page_ready(self.task)
        return 'SUPPORT_CHECK' in self.frame['buttons'] and not self.frame.get('overlay', False)

    def view(self):
        if isinstance(self.image, np.ndarray):
            return KnightsSupportMixin._donation_view(self.task)
        return str(self.frame['view']).encode(), np.array([self.frame['view'] * 10], dtype=np.int16)

    def entries(self):
        if isinstance(self.image, np.ndarray):
            return KnightsSupportMixin._donation_list_entries(self.task)
        actions = [quote(row).button for row in self.frame['rows']]
        completed = [ClickButton((1112, 180 + i * 130, 1138, 198 + i * 130))
                     for i in range(self.frame['completed'])]
        return actions, completed

    def pairs(self, plans, action_buttons=None):
        if isinstance(self.image, np.ndarray):
            return KnightsSupportMixin._find_donate_action_pairs(self.task, plans, action_buttons=action_buttons)
        enabled = {plan.name for plan in plans}
        return [(row[0], quote(row).button) for row in self.frame['rows'] if row[0] in enabled]

    def read_quote(self, item, button):
        if isinstance(self.image, np.ndarray):
            return KnightsSupportMixin._donation_quote(self.task, item, button)
        row = next(row for row in self.frame['rows'] if quote(row).button.name == button.name)
        return quote(row) if row[1] is not None and row[2] is not None else None

    def identity(self, button):
        if isinstance(self.image, np.ndarray):
            return KnightsSupportMixin._donation_identity(self.task, button)
        return button.name.removeprefix('donate_').encode()

    def inventory(self, plans, item):
        if isinstance(self.image, np.ndarray):
            return KnightsSupportMixin._donation_inventory(self.task, plans, item)
        if self.frame.get('stocks') is not None:
            return self.frame['stocks'].get(item)
        stocks = {row[1] for row in self.frame['rows'] if row[0] == item and row[1] is not None}
        return stocks.pop() if len(stocks) == 1 else None

    def read_text(self, area, name):
        if isinstance(self.image, np.ndarray):
            return KnightsSupportMixin._donation_text(self.task, area, name)
        return self.frame.get('result', '')

    def network(self):
        if self.frame.get('network'):
            self.click(ClickButton((1, 1, 2, 2), name='NETWORK_RETRY'))
            return True
        return False
