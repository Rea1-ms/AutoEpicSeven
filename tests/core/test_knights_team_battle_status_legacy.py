# ruff: noqa: E402
"""Historical manual assertions adapted to the account-free offline runner."""
import unittest
from module.config import server as _test_server
_test_server.set_lang("global_cn")

from tasks.knights.team_battle_status import TeamBattleCrestStatus

class ManualChecks(unittest.TestCase):
    def test_valid_and_invalid_crest_counts(self):
        assert TeamBattleCrestStatus(current=3, remain=3, total=3).is_valid()
        assert TeamBattleCrestStatus(current=0, remain=0, total=3).is_valid()
        assert not TeamBattleCrestStatus(current=3, remain=3, total=34).is_valid()
        assert not TeamBattleCrestStatus(current=4, remain=4, total=3).is_valid()
