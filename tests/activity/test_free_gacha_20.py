# ruff: noqa: E402
from module.config import server as _test_server

_test_server.set_lang("global_cn")

import unittest
from pathlib import Path


WORKTREE = Path(__file__).resolve().parents[2]
WORKTREE_ROOT = WORKTREE

import module.config.server as server_  # noqa: E402
from tasks.base.page import Page, page_common_activity, page_main  # noqa: E402


class FakeDevice:
    def __init__(self):
        self.screenshot_count = 0

    def screenshot(self):
        self.screenshot_count += 1


class FreeGacha20Test(unittest.TestCase):
    def setUp(self):
        self.original_lang = server_.lang

    def tearDown(self):
        server_.lang = self.original_lang


    def test_common_activity_routes_to_and_from_main(self):
        Page.init_connection(page_common_activity)
        try:
            self.assertEqual(page_main.parent, page_common_activity)
        finally:
            Page.clear_connection()

        Page.init_connection(page_main)
        try:
            self.assertEqual(page_common_activity.parent, page_main)
        finally:
            Page.clear_connection()
