# ruff: noqa: E402
"""Current community scheduling rules; retired command contracts are excluded."""

import unittest
from datetime import datetime

from module.config import server

server.set_lang('global_cn')

from tasks.community_aio.community_aio import CommunityAio


class LegacyRuleTests(unittest.TestCase):
    def test_next_midnight_returns_datetime(self):
        self.assertIsInstance(CommunityAio._next_midnight(), datetime)
