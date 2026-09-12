import logging
import random
import re
import socket
import time

from adbutils import AdbConnection, AdbTimeout
from lxml import etree

from module.base.decorator import cached_property
from module.logger import logger

RETRY_TRIES = 5
RETRY_DELAY = 3

class Uiautomator2LogHandler(logging.Handler):
    """Forward uiautomator2's public logging output to the project logger."""

    def emit(self, record):
        message = self.format(record)
        if record.levelno >= logging.ERROR:
            logger.error(message)
        elif record.levelno >= logging.WARNING:
            logger.warning(message)
        else:
            logger.info(message)


uiautomator2_logger = logging.getLogger('uiautomator2')
if not any(isinstance(handler, Uiautomator2LogHandler) for handler in uiautomator2_logger.handlers):
    uiautomator2_logger.addHandler(Uiautomator2LogHandler())
uiautomator2_logger.propagate = False


def is_port_using(port_num):
    """ if port is using by others, return True. else return False """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2)

    try:
        s.bind(('127.0.0.1', port_num))
        return False
    except OSError:
        # Address already bind
        return True
    finally:
        s.close()


def random_port(port_range):
    """ get a random port from port set """
    new_port = random.choice(list(range(*port_range)))
    if is_port_using(new_port):
        return random_port(port_range)
    else:
        return new_port


def recv_all(stream, chunk_size=4096, recv_interval=0.000) -> bytes:
    """
    Args:
        stream:
        chunk_size:
        recv_interval (float): Default to 0.000, use 0.001 if receiving as server

    Returns:
        bytes:

    Raises:
        AdbTimeout
    """
    if isinstance(stream, AdbConnection):
        stream = stream.conn
        stream.settimeout(10)
    else:
        stream.settimeout(10)

    try:
        fragments = []
        while 1:
            chunk = stream.recv(chunk_size)
            if chunk:
                fragments.append(chunk)
                # See https://stackoverflow.com/questions/23837827/python-server-program-has-high-cpu-usage/41749820#41749820
                time.sleep(recv_interval)
            else:
                break
        return remove_shell_warning(b''.join(fragments))
    except TimeoutError:
        raise AdbTimeout('adb read timeout')


def possible_reasons(*args):
    """
    Show possible reasons

        Possible reason #1: <reason_1>
        Possible reason #2: <reason_2>
    """
    for index, reason in enumerate(args):
        index += 1
        logger.critical(f'Possible reason #{index}: {reason}')


class PackageNotInstalled(Exception):
    pass


class ImageTruncated(Exception):
    pass


def retry_sleep(trial):
    # First trial
    if trial == 0:
        return 0
    # Failed once, fast retry
    elif trial == 1:
        return 0
    # Failed twice
    elif trial == 2:
        return 1
    # Failed more
    else:
        return RETRY_DELAY


def handle_adb_error(e):
    """
    Args:
        e (Exception):

    Returns:
        bool: If should retry
    """
    text = str(e)
    if 'not found' in text:
        # When you call `adb disconnect <serial>`
        # Or when adb server was killed (low possibility)
        # AdbError(device '127.0.0.1:59865' not found)
        logger.error(e)
        return True
    elif 'timeout' in text:
        # AdbTimeout(adb read timeout)
        logger.error(e)
        return True
    elif 'closed' in text:
        # AdbError(closed)
        # Usually after AdbTimeout(adb read timeout)
        # Disconnect and re-connect should fix this.
        logger.error(e)
        return True
    elif 'device offline' in text:
        # AdbError(device offline)
        # When a device that has been connected wirelessly is disconnected passively,
        # it does not disappear from the adb device list,
        # but will be displayed as offline.
        # In many cases, such as disconnection and recovery caused by network fluctuations,
        # or after VMOS reboot when running Alas on a phone,
        # the device is still available, but it needs to be disconnected and re-connected.
        logger.error(e)
        return True
    elif 'is offline' in text:
        # RuntimeError: USB device 127.0.0.1:7555 is offline
        # Raised by uiautomator2 when current adb service is killed by another version of adb service.
        logger.error(e)
        return True
    elif text == 'rest':
        # AdbError(rest)
        # Response telling adbd service has reset, client should reconnect
        logger.error(e)
        return True
    else:
        # AdbError()
        logger.exception(e)
        possible_reasons(
            'If you are using BlueStacks or LD player or WSA, please enable ADB in the settings of your emulator',
            'Emulator died, please restart emulator',
            'Serial incorrect, no such device exists or emulator is not running'
        )
        return False


def handle_unknown_host_service(e):
    """
    Args:
        e (Exception):

    Returns:
        bool: If should retry
    """
    text = str(e)
    if 'unknown host service' in text:
        # AdbError(unknown host service)
        # Another version of ADB service started, current ADB service has been killed.
        # Usually because user opened a Chinese emulator, which uses ADB from the Stone Age.
        logger.error(e)
        return True
    else:
        return False


def get_serial_pair(serial):
    """
    Args:
        serial (str):

    Returns:
        str, str: `127.0.0.1:5555+{X}` and `emulator-5554+{X}`, 0 <= X <= 32
    """
    if serial.startswith('127.0.0.1:'):
        try:
            port = int(serial[10:])
            if 5555 <= port <= 5555 + 32:
                return f'127.0.0.1:{port}', f'emulator-{port - 1}'
        except (ValueError, IndexError):
            pass
    if serial.startswith('emulator-'):
        try:
            port = int(serial[9:])
            if 5554 <= port <= 5554 + 32:
                return f'127.0.0.1:{port + 1}', f'emulator-{port}'
        except (ValueError, IndexError):
            pass

    return None, None


def remove_prefix(s, prefix):
    """
    Remove prefix of a string or bytes like `string.removeprefix(prefix)`, which is on Python3.9+

    Args:
        s (str, bytes):
        prefix (str, bytes):

    Returns:
        str, bytes:
    """
    return s[len(prefix):] if s.startswith(prefix) else s


def remove_suffix(s, suffix):
    """
    Remove suffix of a string or bytes like `string.removesuffix(suffix)`, which is on Python3.9+

    Args:
        s (str, bytes):
        suffix (str, bytes):

    Returns:
        str, bytes:
    """
    return s[:-len(suffix)] if s.endswith(suffix) else s


def remove_shell_warning(s):
    """
    Remove warnings from shell

    1. Warnings in VMOS shell
    https://github.com/LmeSzinc/AzurLaneAutoScript/issues/1425

    WARNING: linker: [vdso]: unused DT entry: type 0x70000001 arg 0x0\n\x89PNG\r\n\x1a\n\x00\x00\x00\rIH

    2. This linker thingy might appear multiple times when executing multiple commands

    mek_8q:/dev # getprop | grep gnss
    WARNING: linker: Warning: "[vdso]" unused DT entry: unknown processor-specific (type 0x70000001 arg 0x0) (ignoring)
    WARNING: linker: Warning: "[vdso]" unused DT entry: unknown processor-specific (type 0x70000001 arg 0x0) (ignoring)
    [init.svc.gnss_service]: [running]
    [init.svc_debug_pid.gnss_service]: [406]
    [ro.boottime.gnss_service]: [27308752875]

    3. Errors in waydroid screencap render
    https://github.com/LmeSzinc/AzurLaneAutoScript/issues/4760

    Failed to create //.cache for shader cache (Read-only file system)---disabling.\n

    4. Warning when taking screenshot from multiscreen device

    [Warning] Multiple displays were found, but no display id was specified! Defaulting to the first display found,
    however this default is not guaranteed to be consistent across captures.\n
    A display id should be specified.\n
    See "dumpsys SurfaceFlinger --display-id" for valid display IDs.\n
    \x89PNG...

    Args:
        s (T): bytes or str

    Returns:
        T:
    """
    if isinstance(s, bytes):
        while 1:
            if s.startswith(b'WARNING: linker:'):
                _, _, s = s.partition(b'\n')
            else:
                break
        if s.startswith(b'Failed to create'):
            _, _, s = s.partition(b'\n')
        if s.startswith(b'[Warning] Multiple displays'):
            _, _, s = s.partition(b'\n')
            if s.startswith(b'A display id'):
                _, _, s = s.partition(b'\n')
                if s.startswith(b'See "dumpsys'):
                    _, _, s = s.partition(b'\n')
    elif isinstance(s, str):
        while 1:
            if s.startswith('WARNING: linker:'):
                _, _, s = s.partition('\n')
            else:
                break
        if s.startswith('Failed to create'):
            _, _, s = s.partition('\n')
        if s.startswith('[Warning] Multiple displays'):
            _, _, s = s.partition('\n')
            if s.startswith('A display id'):
                _, _, s = s.partition('\n')
                if s.startswith('See "dumpsys'):
                    _, _, s = s.partition('\n')
    return s


class HierarchyButton:
    """
    Convert UI hierarchy to an object like the Button in Alas.
    """
    _name_regex = re.compile('@.*?=[\'\"](.*?)[\'\"]')

    def __init__(self, hierarchy: etree._Element, xpath: str):
        self.hierarchy = hierarchy
        self.xpath = xpath
        self.nodes = hierarchy.xpath(xpath)

    @cached_property
    def name(self):
        res = HierarchyButton._name_regex.findall(self.xpath)
        if res:
            return res[0]
        else:
            return self.xpath

    @cached_property
    def count(self):
        return len(self.nodes)

    @cached_property
    def exist(self):
        return self.count == 1

    @cached_property
    def attrib(self):
        if self.exist:
            return self.nodes[0].attrib
        else:
            return {}

    @cached_property
    def area(self):
        if self.exist:
            bounds = self.attrib.get("bounds")
            lx, ly, rx, ry = map(int, re.findall(r"\d+", bounds))
            return lx, ly, rx, ry
        else:
            return None

    @cached_property
    def size(self):
        if self.area is not None:
            lx, ly, rx, ry = self.area
            return rx - lx, ry - ly
        else:
            return None

    @cached_property
    def button(self):
        return self.area

    def __bool__(self):
        return self.exist

    def __str__(self):
        return self.name

    """
    Element props
    """

    def _get_bool_prop(self, prop: str) -> bool:
        return self.attrib.get(prop, "").lower() == 'true'

    @cached_property
    def index(self) -> int:
        try:
            return int(self.attrib.get("index", 0))
        except IndexError:
            return 0

    @cached_property
    def text(self) -> str:
        return self.attrib.get("text", "").strip()

    @cached_property
    def resourceId(self) -> str:
        return self.attrib.get("resourceId", "").strip()

    @cached_property
    def package(self) -> str:
        return self.attrib.get("resourceId", "").strip()

    @cached_property
    def description(self) -> str:
        return self.attrib.get("resourceId", "").strip()

    @cached_property
    def checkable(self) -> bool:
        return self._get_bool_prop('checkable')

    @cached_property
    def clickable(self) -> bool:
        return self._get_bool_prop('clickable')

    @cached_property
    def enabled(self) -> bool:
        return self._get_bool_prop('enabled')

    @cached_property
    def fucusable(self) -> bool:
        return self._get_bool_prop('fucusable')

    @cached_property
    def focused(self) -> bool:
        return self._get_bool_prop('focused')

    @cached_property
    def scrollable(self) -> bool:
        return self._get_bool_prop('scrollable')

    @cached_property
    def longClickable(self) -> bool:
        return self._get_bool_prop('longClickable')

    @cached_property
    def password(self) -> bool:
        return self._get_bool_prop('password')

    @cached_property
    def selected(self) -> bool:
        return self._get_bool_prop('selected')


class AreaButton:
    def __init__(self, area, name='AREA_BUTTON'):
        self.area = area
        self.color = ()
        self.name = name
        self.button = area

    def __str__(self):
        return self.name

    def __bool__(self):
        # Cannot appear
        return False
