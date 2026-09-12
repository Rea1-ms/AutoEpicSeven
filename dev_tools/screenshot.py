import os
import sys
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402

from dev_tools.capture_utils import handle_sensitive_info  # noqa: E402
from module.config.config import AzurLaneConfig  # noqa: E402
from module.config.utils import alas_instance  # noqa: E402

from module.device.connection import Connection, ConnectionAttr  # noqa: E402
from module.device.device import Device  # noqa: E402
from module.logger import logger  # noqa: E402

"""
A tool to take screenshots on device

Usage:
    python -m dev_tools.screenshot
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


def create_device(name='aes'):
    """Create a Device through the project's configured capture stack."""
    name = normalize_device_name(name)
    if name in alas_instance():
        print(f'{name} is an existing config file')
        device = Device(name)
    else:
        print(f'{name} is a device serial')
        config = AzurLaneConfig('template')
        config.override(
            Emulator_Serial=name,
            Emulator_PackageName='auto',
            Emulator_ScreenshotMethod='auto',
        )
        device = Device(config)

    device.disable_stuck_detection()
    device.screenshot_interval_set(0.)
    return device


def take_screenshot(device):
    """Capture one privacy-safe RGB frame from a configured Device."""
    image = device.screenshot()
    return handle_sensitive_info(image, copy=True)


def save_screenshot(device, output='./screenshots/dev_screenshots'):
    print('截图中...')
    image = take_screenshot(device)
    os.makedirs(output, exist_ok=True)
    now = datetime.strftime(datetime.now(), '%Y-%m-%d_%H-%M-%S-%f')
    file = f'{output}/{now}.png'
    Image.fromarray(image).save(file)
    print(f'截图已保存到: {file}')
    return file


def main():
    from pynput import keyboard  # ty: ignore[unresolved-import]

    _ = EmptyConnection()
    name = input(
        '输入aes配置文件名称，或者模拟器serial，或者模拟器端口号: (默认输入 "aes"):\n'
        '例如："aes", "127.0.0.1:16384", "7555"\n'
    )
    device = create_device(name)
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
    main()
