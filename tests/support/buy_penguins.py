"""Explicit penguin frames; clicking never changes the observation sequence."""
from dataclasses import dataclass
from types import SimpleNamespace

from module.config import server

server.set_lang('global_cn')

from tasks.sanctuary.buy_penguins import BuyPenguins  # noqa: E402
from tests.support.offline import ROOT, fixture_image, record_action, record_frame  # noqa: E402

MANIFEST = ROOT / 'tests/fixtures/sanctuary/manifest.json'
CAPTURES = {
    'forest': '20261001-144421-780', 'shop': '20261001-144425-154',
    'one': '20261001-144428-274', 'max': '20261001-144430-043',
    'reward': '20261001-144433-233',
}


def pixel_task(scene):
    task = BuyPenguins.__new__(BuyPenguins)
    task.config = SimpleNamespace(Emulator_GameLanguage='cn')
    task.interval_timer = {}
    task.device = SimpleNamespace(image=fixture_image(MANIFEST, CAPTURES[scene]),
                                  stuck_record_add=lambda *args: None)
    return task


@dataclass(frozen=True)
class Frame:
    page: str = 'shop'
    balance: int | None = 1213099
    unit: int = 102
    quantity: tuple[int, int] | None = (50, 50)
    price: int | None = 5100
    seconds: float = 1


class ReplayDevice:
    def __init__(self, frames, clock):
        self.frames = frames
        self.clock = clock
        self.index = 0
        self.image = frames[0]
        self.actions = []
        record_frame('penguins-synthetic:0')

    def screenshot(self):
        self.index += 1
        if self.index >= len(self.frames):
            raise AssertionError('Penguin replay exhausted before a verified exit')
        self.image = self.frames[self.index]
        self.clock.advance(self.image.seconds)
        record_frame(f'penguins-synthetic:{self.index}')

    def click(self, button):
        self.actions.append(record_action((self.index, button.name)))


class PenguinReplay(BuyPenguins):
    def __init__(self, frames, clock):
        self.interval_timer = {}
        self.device = ReplayDevice(frames, clock)

    def _shop_is_ready(self):
        return self.device.image.page == 'shop'

    def _forest_is_ready(self):
        return self.device.image.page == 'forest'

    def _read_shop_values(self):
        frame = self.device.image
        return (frame.balance, frame.unit) if frame.balance is not None else None

    def _read_purchase_values(self):
        return self.device.image.quantity, self.device.image.price

    def appear(self, asset, interval=0, **kwargs):
        if interval and not self.interval_is_reached(asset, interval=interval):
            return False
        expected = {
            'PENGUIN_SHOP_CHECK': 'shop', 'PENGUIN_BUY': 'shop',
            'PENGUIN_SHOP_CLOSE': 'shop', 'ALTAR_OF_GROWTH': 'forest',
            'PENGUIN_PURCHASE_CHECK': 'purchase', 'PENGUIN_MAX': 'purchase',
            'PENGUIN_CONFIRM': 'purchase', 'PENGUIN_CANCEL': 'purchase',
            'PENGUIN_REWARD_CHECK': 'reward', 'PENGUIN_REWARD_CLOSE': 'reward',
        }.get(asset.name)
        matched = expected == self.device.image.page
        if matched and interval:
            self.interval_reset(asset, interval=interval)
        return matched

    def handle_network_error(self, **kwargs):
        if self.device.image.page == 'network':
            self.device.actions.append(record_action((self.device.index, 'network_retry')))
            return True
        return False
