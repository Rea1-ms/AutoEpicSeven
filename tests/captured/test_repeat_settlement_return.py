# ruff: noqa: E402
import unittest
from module.config import server
server.set_lang("global_cn")
from tests.support.history_fixtures import read_input as load_image

from tasks.base.page import page_store
from tests.support.history_fixtures import build_combat

class AssetRecognitionTests(unittest.TestCase):
    def test_existing_store_marker_identifies_supported_return_page(self):
        for lang in ("cn", "global_cn"):
            with self.subTest(lang=lang):
                server.set_lang(lang)
                combat = build_combat("superior-fast-on.png", "Superior")
                combat.device.image = load_image(page_store.check_button.matched_button.file)
                self.assertEqual(combat._repeat_combat_return_page(), page_store)
        server.set_lang("global_cn")
