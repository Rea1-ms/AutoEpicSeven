import argparse

import numpy as np


DEFAULT_FRAME_RATE = 30

SCREENSHOT_METHODS = {
    'auto': 'auto',
    'adb': 'ADB',
    'adbnc': 'ADB_nc',
    'adb_nc': 'ADB_nc',
    'uiautomator': 'uiautomator2',
    'uiautomator2': 'uiautomator2',
    'ascreencap': 'aScreenCap',
    'ascreencap_nc': 'aScreenCap_nc',
    'droidcast': 'DroidCast',
    'droidcast_raw': 'DroidCast_raw',
    'scrcpy': 'scrcpy',
    'nemu': 'nemu_ipc',
    'nemu_ipc': 'nemu_ipc',
    'ldopengl': 'ldopengl',
}

CONTROL_METHODS = {
    'adb': 'ADB',
    'uiautomator': 'uiautomator2',
    'uiautomator2': 'uiautomator2',
    'minitouch': 'minitouch',
    'maatouch': 'MaaTouch',
    'scrcpy': 'scrcpy',
    'nemu': 'nemu_ipc',
    'nemu_ipc': 'nemu_ipc',
    'hermit': 'Hermit',
}


def parse_frame_rate(value):
    """Validate a positive whole-number capture rate before connecting."""
    try:
        frame_rate = int(str(value))
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError('帧率必须是正整数') from exc
    if frame_rate <= 0:
        raise argparse.ArgumentTypeError('帧率必须是正整数')
    return frame_rate


def _normalize_method(value, methods, option_name):
    key = str(value or '').strip().lower()
    try:
        return methods[key]
    except KeyError as exc:
        choices = ', '.join(sorted(methods))
        raise argparse.ArgumentTypeError(
            f'{option_name} must be one of: {choices}'
        ) from exc


def parse_screenshot_method(value):
    return _normalize_method(value, SCREENSHOT_METHODS, '截图方式')


def parse_control_method(value):
    return _normalize_method(value, CONTROL_METHODS, '操控方式')


def handle_sensitive_info(image: np.ndarray, copy=True) -> np.ndarray:
    """Return a writable screenshot copy.

    The current game layout does not show an account UID in the lower-left
    corner, so the capture tool must preserve those pixels.  Keep this small
    helper for callers that need a private copy without changing the source
    frame in place.
    """
    if copy:
        image = image.copy()
    return image
