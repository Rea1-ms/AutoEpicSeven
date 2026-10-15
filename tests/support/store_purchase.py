"""Store purchase observations with real pixels or explicitly synthetic states."""

from pathlib import Path
from unittest.mock import Mock

from module.config import server
from tests.support.interaction import InteractionReplay, scene
from tests.support.offline import fixture_image, record_frame

server.set_lang('global_cn')

from tasks.store.assets.assets_store_actions import (
    BUY_CONFIRM_MULTI,
    BUY_CONFIRM_SINGLE,
    BUY_MAX,
    BUY_MIN,
    BUY_TIMES_MINUS,
    BUY_TIMES_PLUS,
)
from tasks.store.assets.assets_store_items import DAILY_FREE_ITEM
from tasks.store.current import CurrentStore
from tasks.store.purchase import ItemPurchasePlan

MANIFEST = Path(__file__).resolve().parents[1] / 'fixtures/historical/manifest.json'
AVAILABLE = 'store-daily-free-available-20261014'
REWARD = 'store-daily-free-reward-20261014'
CLAIMED = 'store-daily-free-claimed-20261014'


def free_plan():
    return ItemPurchasePlan('daily_free_item', DAILY_FREE_ITEM, direct_click=True,
                            requires_reward_popup=True)


def captured_frame(fixture, *, hide_close=False):
    return scene(fixture=fixture, hide_close=hide_close)


class StorePurchaseReplay(InteractionReplay):
    """Keep the production loop, timers and clicks; observations never follow clicks."""

    def __init__(self, frames, clock):
        self.images = {}
        self.action_times = []
        super().__init__(CurrentStore, frames, clock)
        for name, asset in (
            ('buy_confirm_single_asset', BUY_CONFIRM_SINGLE), ('buy_confirm_multi_asset', BUY_CONFIRM_MULTI),
            ('buy_min_asset', BUY_MIN), ('buy_max_asset', BUY_MAX),
            ('buy_times_minus_asset', BUY_TIMES_MINUS), ('buy_times_plus_asset', BUY_TIMES_PLUS),
        ):
            setattr(self.task, name, asset)
        self.task._load_shared_item_search()
        self.task._match_purchaseable_item = self.purchaseable
        self.task._has_item = self.has_item
        self.task.match_template_color = self.match_template_color
        self.task.match_template_luma = self.match_template_luma
        self.task.ui_additional = self.ui_additional
        # Failure reporting is supplied by the runner. The synthetic frames
        # deliberately have no image pixels for the production debug matcher.
        self.task._log_purchase_debug = Mock()

    def click(self, button):
        super().click(button)
        self.action_times.append((button.name, self.clock.now))

    def screenshot(self):
        super().screenshot()
        if self.frame.get('fixture'):
            fixture = self.frame['fixture']
            if fixture not in self.images:
                self.images[fixture] = fixture_image(MANIFEST, fixture)
            self.image = self.images[fixture].copy()
            record_frame(fixture)
            if self.frame.get('hide_close'):
                # Deliberately simulated disappearance of the close artwork;
                # the reward overlay remains. This is not a fourth capture.
                self.image[457:715, 622:770] = 0
                record_frame('synthetic:reward-overlay-without-close-artwork')

    def appear(self, button, interval=0, **kwargs):
        if self.frame.get('fixture'):
            return CurrentStore.appear(self.task, button, interval=interval, **kwargs)
        return super().appear(button, interval=interval, **kwargs)

    def match_template_color(self, button, interval=0, **kwargs):
        if self.frame.get('fixture'):
            return CurrentStore.match_template_color(self.task, button, interval=interval, **kwargs)
        return self.appear(button, interval=interval, **kwargs)

    def match_template_luma(self, button, interval=0, **kwargs):
        if self.frame.get('fixture'):
            return CurrentStore.match_template_luma(self.task, button, interval=interval, **kwargs)
        return self.appear(button, interval=interval, **kwargs)

    def purchaseable(self, asset):
        if self.frame.get('fixture'):
            return CurrentStore._match_purchaseable_item(self.task, asset)
        return asset.name in self.frame['buttons']

    def has_item(self, asset, image):
        if self.frame.get('fixture'):
            return CurrentStore._has_item(asset, image)
        return asset.name in self.frame['buttons']

    def ui_additional(self):
        if self.frame.get('fixture'):
            return CurrentStore.ui_additional(self.task)
        # The only generic popup in synthetic scenes is the same close action
        # used by production ui_additional; unrelated events are absent.
        return self.task.handle_touch_to_close()
