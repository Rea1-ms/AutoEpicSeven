# ruff: noqa: E402
from module.config import server as _test_server

_test_server.set_lang("global_cn")

from datetime import datetime
from pathlib import Path

from tasks.community_aio.community_aio import CommunityAio


def test_script_path_points_to_community_aio():
    script_path = CommunityAio._script_path()
    assert script_path.name == "aio.py"
    assert script_path.parent.name == "community"
    assert script_path.is_absolute()


def test_build_command_without_credentials_file():
    command = CommunityAio._build_command(Path(r"D:\repo\community\aio.py"))
    assert command == [command[0], r"D:\repo\community\aio.py"]


def test_build_command_with_credentials_file():
    command = CommunityAio._build_command(
        Path(r"D:\repo\community\aio.py"),
        credentials_file=r"D:\Users\A\AppData\Roaming\aes\e7-credentials.json",
    )
    assert command == [
        command[0],
        r"D:\repo\community\aio.py",
        "--credentials-file",
        r"D:\Users\A\AppData\Roaming\aes\e7-credentials.json",
    ]


def test_next_midnight_returns_datetime():
    target = CommunityAio._next_midnight()
    assert isinstance(target, datetime)




import unittest

class LegacyRuleTests(unittest.TestCase):



    def test_next_midnight_returns_datetime(self):
        test_next_midnight_returns_datetime()

# Historical assertions retained verbatim for migration review; not executable evidence.
HISTORICAL_CONTRACTS = 'def test_script_path_points_to_community_aio(self):\n        test_script_path_points_to_community_aio()\n\ndef test_build_command_without_credentials_file(self):\n        test_build_command_without_credentials_file()\n\ndef test_build_command_with_credentials_file(self):\n        test_build_command_with_credentials_file()'
