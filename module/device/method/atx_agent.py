import io
import json
import re
import shlex
import time
from typing import NamedTuple

import requests
from PIL import Image


class AtxAgentShellResponse(NamedTuple):
    output: str
    exit_code: int


class AtxAgentService:
    def __init__(self, client, name):
        self.client = client
        self.path = f'/services/{name}'

    def start(self):
        self.client._request('POST', self.path)

    def stop(self):
        self.client._request('DELETE', self.path)

    def running(self):
        return bool(self.client._request('GET', self.path).json().get('running'))


class AtxAgentTouch:
    ACTION_DOWN = 0
    ACTION_UP = 1
    ACTION_MOVE = 2

    def __init__(self, client):
        self.client = client

    def down(self, x, y):
        self.client.jsonrpc_call('injectInputEvent', (self.ACTION_DOWN, x, y, 0))
        return self

    def move(self, x, y):
        self.client.jsonrpc_call('injectInputEvent', (self.ACTION_MOVE, x, y, 0))
        return self

    def up(self, x, y):
        self.client.jsonrpc_call('injectInputEvent', (self.ACTION_UP, x, y, 0))
        return self


class AtxAgentClient:
    """Compatibility client for a remote, already-running atx-agent instance.

    uiautomator2 3.x intentionally supports ADB-connected devices only. This
    client owns the small HTTP surface that AutoEpicSeven still needs for its
    legacy DEVICE_OVER_HTTP mode, so no uiautomator2 private API is required.
    """

    def __init__(self, base_url):
        self.base_url = str(base_url).rstrip('/')
        if not re.match(r'^https?://', self.base_url):
            raise ValueError(f'Invalid atx-agent URL: {base_url}')
        self.session = requests.Session()
        self.session.trust_env = False

    def _request(self, method, path, **kwargs):
        response = self.session.request(method, self.base_url + path, **kwargs)
        response.raise_for_status()
        return response

    @property
    def touch(self):
        return AtxAgentTouch(self)

    def service(self, name):
        return AtxAgentService(self, name)

    def set_new_command_timeout(self, timeout):
        response = self._request('POST', '/newCommandTimeout', data=str(int(timeout)))
        data = response.json()
        if not data.get('success'):
            raise RuntimeError(data.get('description', 'Unable to set atx-agent command timeout'))

    def shell(self, cmdargs, stream=False, timeout=60):
        if isinstance(cmdargs, (list, tuple)):
            command = shlex.join([str(arg) for arg in cmdargs])
        elif isinstance(cmdargs, str):
            command = cmdargs
        else:
            raise TypeError('cmdargs type invalid', type(cmdargs))

        if stream:
            return self._request(
                'GET',
                '/shell/stream',
                params={'command': command},
                timeout=None,
                stream=True,
            )

        response = self._request(
            'POST',
            '/shell',
            data={'command': command, 'timeout': str(timeout)},
            timeout=timeout + 10,
        )
        data = response.json()
        exit_code = data.get('exitCode', 1 if data.get('error') else 0)
        return AtxAgentShellResponse(data.get('output', ''), exit_code)

    def jsonrpc_call(self, method, params=None, timeout=60):
        payload = {
            'jsonrpc': '2.0',
            'id': f'{method}-{time.time()}',
            'method': method,
            'params': params or [],
        }
        response = self._request(
            'POST',
            '/jsonrpc/0',
            headers={'Content-Type': 'application/json'},
            data=json.dumps(payload),
            timeout=timeout,
        )
        data = response.json()
        if data.get('error'):
            raise RuntimeError(f'atx-agent JSON-RPC error: {data["error"]}')
        return data.get('result')

    def screenshot(self, filename=None, format='pillow'):
        content = self._request('GET', '/screenshot/0', timeout=10).content
        if filename:
            with open(filename, 'wb') as file:
                file.write(content)
            return filename
        if format == 'raw':
            return content
        image = Image.open(io.BytesIO(content)).convert('RGB')
        if format == 'pillow':
            return image
        if format == 'opencv':
            import numpy as np
            return np.asarray(image)[:, :, ::-1].copy()
        raise ValueError(f'Invalid screenshot format: {format}')

    def click(self, x, y):
        return self.jsonrpc_call('click', (x, y))

    def long_click(self, x, y, duration=0.5):
        touch = self.touch
        touch.down(x, y)
        time.sleep(duration)
        touch.up(x, y)

    def swipe(self, fx, fy, tx, ty, duration=0.5):
        steps = max(2, int(duration * 200))
        return self.jsonrpc_call('swipe', (fx, fy, tx, ty, steps))

    def dump_hierarchy(self, compressed=False):
        content = self.jsonrpc_call('dumpWindowHierarchy', (compressed, None))
        if not content:
            raise RuntimeError('dump hierarchy is empty')
        return content

    def window_size(self):
        info = self._request('GET', '/info', timeout=10).json()
        width = info['display']['width']
        height = info['display']['height']
        rotation = info.get('displayRotation', info['display'].get('rotation', 0))
        if (width > height) != (rotation % 2 == 1):
            width, height = height, width
        return width, height

    def app_current(self):
        output = self.shell(['dumpsys', 'window', 'windows']).output
        match = re.search(
            r'mCurrentFocus=Window{.*\s+(?P<package>[^\s]+)/(?P<activity>[^\s}]+)',
            output,
        )
        if match:
            return match.groupdict()

        output = self.shell(['dumpsys', 'activity', 'activities']).output
        match = re.search(
            r'mResumedActivity: ActivityRecord\{.*?\s+(?P<package>[^\s]+)/(?P<activity>[^\s}]+)',
            output,
        )
        if match:
            return match.groupdict()
        raise RuntimeError("Couldn't get focused app")

    def app_info(self, package_name):
        data = self._request('GET', f'/packages/{package_name}/info', timeout=10).json()
        if not data.get('success'):
            raise RuntimeError(data.get('description', f'Package not found: {package_name}'))
        return data.get('data', {})

    def app_stop(self, package_name):
        return self.shell(['am', 'force-stop', package_name])

    def reset_uiautomator(self):
        service = self.service('uiautomator')
        service.stop()
        service.start()
