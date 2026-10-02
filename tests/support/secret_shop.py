"""Account-free shop frames; actions never advance the replay implicitly."""
import json
from contextlib import contextmanager
from dataclasses import dataclass
from types import SimpleNamespace

from module.config import server

server.set_lang('global_cn')

from module.base.button import ClickButton  # noqa: E402
from tasks.secret_shop.secret_shop import SecretShop  # noqa: E402
from tests.support.offline import ROOT, fixture_image, record_action, record_frame  # noqa: E402

MANIFEST = ROOT / 'tests/fixtures/secret_shop/manifest.json'


def captures():
    return json.loads(MANIFEST.read_text(encoding='utf-8'))['fixtures']


def capture(fixture_id):
    return fixture_image(MANIFEST, fixture_id)


class Config:
    Emulator_GameLanguage = 'cn'
    SecretShop_OnlyFree = True
    SecretShop_MaxRefresh = 1
    SecretShop_BuyCovenantBookmark = True
    SecretShop_BuyMysticMedal = True

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
        self.delays = []
        self.stored = SimpleNamespace(Gold=SimpleNamespace(value=0), Skystone=SimpleNamespace(value=0))

    def task_delay(self, **kwargs):
        self.delays.append(kwargs)

    @contextmanager
    def multi_set(self):
        yield


@dataclass(frozen=True)
class Frame:
    page: str = 'shop'
    balance: tuple[int, int] | None = (1000000, 100)
    target: str | None = None
    stable: bool = True
    seconds: float = 1


class ReplayDevice:
    def __init__(self, frames, clock):
        self.frames = frames
        self.clock = clock
        self.index = 0
        self.actions = []
        self.image = frames[0]
        record_frame('secret-shop-synthetic:0')

    def screenshot(self):
        self.index += 1
        if self.index >= len(self.frames):
            raise AssertionError('Secret shop replay exhausted before task exit')
        self.image = self.frames[self.index]
        self.clock.advance(self.image.seconds)
        record_frame(f'secret-shop-synthetic:{self.index}')

    def click(self, button):
        self.actions.append(record_action((self.index, button.name)))

    def swipe(self, *args, **kwargs):
        self.actions.append(record_action((self.index, 'swipe')))

    def save_screenshot(self, **kwargs):
        self.actions.append(record_action((self.index, 'diagnostic')))

    def app_is_running(self):
        return True

    def stuck_record_add(self, *args):
        pass


class ShopReplay(SecretShop):
    """Replace recognition only; execute the real task and payment state machine."""
    def __init__(self, frames, clock, **config):
        super().__init__(Config(**config), ReplayDevice(frames, clock))
        self.written_balances = []

    def _read_shop_balance(self):
        return self.device.image.balance

    def _shop_is_ready(self):
        return self.device.image.page == 'shop'

    def _is_shop_stable(self):
        return self._shop_is_ready() and self.device.image.stable

    def _find_target_buy_buttons(self):
        kind = self.device.image.target
        if kind == 'covenant' and (not self.buy_covenant or self._covenant_purchased_this_round):
            return []
        if kind == 'mystic' and (not self.buy_mystic or self._mystic_purchased_this_round):
            return []
        return [(kind, ClickButton((1100, 150, 1240, 190), name=kind))] if kind else []

    def appear(self, button, interval=0, **kwargs):
        if interval and not self.interval_is_reached(button, interval=interval):
            return False
        matched = self.device.image.page == {
            'BUY_CONFIRM': 'buy', 'REFRESH_CONFIRM': 'refresh', 'REFRESH': 'shop',
        }.get(button.name, 'not-a-page')
        if matched and interval:
            self.interval_reset(button, interval=interval)
        return matched

    def appear_then_click(self, button, **kwargs):
        if self.appear(button, **kwargs):
            self.device.click(button)
            return True
        return False

    def handle_network_error(self, **kwargs):
        if self.device.image.page == 'network':
            self.device.actions.append(record_action((self.device.index, 'network_retry')))
            return True
        return False

    def ocr_resource_bar_status(self, **kwargs):
        return None

    def write_resource_bar_status(self, parsed):
        self.written_balances.append(parsed)
        return True

    def _delay_to_auto_refresh(self):
        self.config.task_delay(minute=10)


def pixel_task(fixture_id):
    return SecretShop(Config(), SimpleNamespace(image=capture(fixture_id), stuck_record_add=lambda *a: None))


class CapturedReplayDevice(ReplayDevice):
    """Actual pixels, with explicit repeated captures rather than invented video."""
    def __init__(self, fixture_ids, clock):
        self.frames = fixture_ids
        self.clock = clock
        self.index = 0
        self.actions = []
        self.image = capture(fixture_ids[0])

    def screenshot(self):
        self.index += 1
        if self.index >= len(self.frames):
            raise AssertionError('Captured secret shop replay exhausted before task exit')
        self.clock.advance(1)
        self.image = capture(self.frames[self.index])
