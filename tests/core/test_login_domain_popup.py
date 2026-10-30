# ruff: noqa: E402
from module.config import server as _test_server

_test_server.set_lang("global_cn")

"""
Unit tests for login-domain popup handling.

Usage:
    .\\.venv\\Scripts\\python.exe -m unittest test.test_login_domain_popup
"""
import unittest
from types import SimpleNamespace


import module.config.server as server_

server_.lang = "cn"
server_.server = "CN-Official"

from tasks.login.assets.assets_login import LOGIN_ANNOUNCEMENT_CLOSE
from tasks.login.assets.assets_login_maintenance import ANNOUNCEMENT_CLOSE
from tasks.login.login import Login


class LoginDomainPopupTest(unittest.TestCase):
    def make_login(self, package_name: str) -> Login:
        login = Login.__new__(Login)
        login.config = SimpleNamespace(Emulator_PackageName=package_name)
        return login

    def test_announcement_close_has_priority(self):
        login = self.make_login("CN-Official")
        calls = []

        def fake_appear_then_click(button, interval=2):
            calls.append((button.name, interval))
            return button.name == ANNOUNCEMENT_CLOSE.name

        login.appear_then_click = fake_appear_then_click

        self.assertTrue(login._handle_login_domain_popup(interval=1))
        self.assertEqual(calls, [(ANNOUNCEMENT_CLOSE.name, 1)])

    def test_cn_specific_login_popup_only_checked_on_cn(self):
        login = self.make_login("CN-Official")
        calls = []

        def fake_appear_then_click(button, interval=2):
            calls.append(button.name)
            return button.name == LOGIN_ANNOUNCEMENT_CLOSE.name

        login.appear_then_click = fake_appear_then_click

        self.assertTrue(login._handle_login_domain_popup(interval=2))
        self.assertEqual(calls, [ANNOUNCEMENT_CLOSE.name, LOGIN_ANNOUNCEMENT_CLOSE.name])

    def test_cn_specific_login_popup_not_checked_on_oversea(self):
        login = self.make_login("OVERSEA-Play")
        calls = []

        def fake_appear_then_click(button, interval=2):
            calls.append(button.name)
            return False

        login.appear_then_click = fake_appear_then_click

        self.assertFalse(login._handle_login_domain_popup(interval=2))
        self.assertEqual(calls, [ANNOUNCEMENT_CLOSE.name])
