import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PIL import Image  # noqa: E402

from dev_tools.capture.capture_utils import (  # noqa: E402
    DEFAULT_FRAME_RATE,
    handle_sensitive_info,
    parse_frame_rate,
)
from module.config.config import AzurLaneConfig  # noqa: E402
from module.config.utils import alas_instance  # noqa: E402
from module.device.pkg_resources import get_distribution  # noqa: E402

# Patch pkg_resources before importing the connection layer and uiautomator2.
_ = get_distribution

from module.device.connection import Connection, ConnectionAttr  # noqa: E402
from module.device.device import Device  # noqa: E402
from module.logger import logger  # noqa: E402

"""
A tool to take screenshots on device

Usage:
    python -m dev_tools.capture.screenshot
"""


class EmptyConnection(Connection):
    def __init__(self):
        ConnectionAttr.__init__(self, AzurLaneConfig('template'))

        logger.hr('Detect device')
        print()
        print('这里是你本机可用的模拟器serial:')
        devices = self.list_device()

        # Show available devices
        available = devices.select(status='device')
        for device in available:
            print(device.serial)
        if not len(available):
            print('No available devices')

        # Show unavailable devices if having any
        unavailable = devices.delete(available)
        if len(unavailable):
            print('Here are the devices detected but unavailable')
            for device in unavailable:
                print(f'{device.serial} ({device.status})')


def normalize_device_name(name):
    name = str(name or 'aes').strip().strip('"').strip()
    if name.isdigit():
        return f'127.0.0.1:{name}'
    return name


def create_device(name='aes', frame_rate=DEFAULT_FRAME_RATE):
    """Create a Device through the project's configured capture stack."""
    frame_rate = parse_frame_rate(frame_rate)
    name = normalize_device_name(name)
    if name in alas_instance():
        print(f'{name} is an existing config file')
        device = Device(name, scrcpy_frame_rate=frame_rate)
    else:
        print(f'{name} is a device serial')
        config = AzurLaneConfig('template')
        config.override(
            Emulator_Serial=name,
            Emulator_PackageName='auto',
            Emulator_ScreenshotMethod='scrcpy',
        )
        device = Device(config, scrcpy_frame_rate=frame_rate)

    device.disable_stuck_detection()
    device.screenshot_interval_set(0.)
    return device


def take_screenshot(device):
    """Capture one writable RGB frame from a configured Device."""
    image = device.screenshot()
    return handle_sensitive_info(image, copy=True)


def save_screenshot(device, output='./screenshots/dev_screenshots'):
    print('截图中...')
    image = take_screenshot(device)
    return save_frame(image, output)


def save_frame(image, output='./screenshots/dev_screenshots'):
    """Save source pixels without preview overlays or a second device capture."""
    os.makedirs(output, exist_ok=True)
    now = datetime.strftime(datetime.now(), '%Y-%m-%d_%H-%M-%S-%f')
    file = f'{output}/{now}.png'
    Image.fromarray(handle_sensitive_info(image, copy=True)).save(file)
    print(f'截图已保存到: {file}')
    return file


def create_preview(device, output='./screenshots/dev_screenshots'):
    """Share the existing crop canvas with manual capture and device controls."""
    from dev_tools.assets.asset_crop_ui import InteractiveCropper
    from module.base.button import ClickButton

    def click(x, y):
        # A one-pixel button preserves the displayed coordinate instead of
        # introducing a randomized click area for this manual developer action.
        device.click(ClickButton(area=(x, y, x + 1, y + 1), name='DEV_PREVIEW'), control_check=False)

    return InteractiveCropper(
        frame_getter=lambda: take_screenshot(device),
        on_capture=lambda image: save_frame(image, output),
        on_click=click,
        on_swipe=lambda start, end, duration: device.swipe(start, end, duration=duration),
        on_back=lambda: device.adb_shell(['input', 'keyevent', '4']),
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description='设备截图、操控与素材框选')
    target = parser.add_mutually_exclusive_group()
    target.add_argument('--config', default='aes', help='使用已有项目配置，默认 aes')
    target.add_argument('--serial', help='设备地址或端口号')
    parser.add_argument('--fps', type=parse_frame_rate, default=DEFAULT_FRAME_RATE,
                        help='视频帧率上限，正整数，默认 30')
    parser.add_argument('--console', action='store_true', help='使用原有终端与全局热键模式')
    args = parser.parse_args(argv)
    if not args.console:
        from dev_tools.assets.asset_crop import main as crop_main

        target_args = ['--serial', args.serial] if args.serial else ['--config', args.config]
        target_args += ['--fps', str(args.fps)]
        result = crop_main(['live', *target_args])
        return 0 if result == 130 else result

    from pynput import keyboard  # ty: ignore[unresolved-import]

    _ = EmptyConnection()
    name = args.serial or args.config
    if not args.serial and args.config == 'aes':
        name = input(
            '输入aes配置文件名称，或者模拟器serial，或者模拟器端口号: (默认输入 "aes"):\n'
            '例如："aes", "127.0.0.1:16384", "7555"\n'
        )
    device = create_device(name, frame_rate=args.fps)
    output = './screenshots/dev_screenshots'
    os.makedirs(output, exist_ok=True)
    print('')
    print(f'截图将保存到: {output}')

    def screenshot():
        save_screenshot(device, output=output)

    # Bind global shortcut
    global_key = 'F3'

    def on_press(key):
        if str(key) == f'Key.{global_key.lower()}':
            screenshot()

    listener = keyboard.Listener(on_press=on_press)
    listener.start()

    while 1:
        print()
        _ = input(
            f'按 <回车键> 或者按快捷键 <{global_key}> 截一张图（快捷键全局生效）:'
        )
        screenshot()


if __name__ == '__main__':
    raise SystemExit(main())
