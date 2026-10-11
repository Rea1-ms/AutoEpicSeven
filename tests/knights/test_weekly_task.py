# ruff: noqa: E402
"""Weekly-task page recognition and return navigation on registered captures."""

import unittest
from pathlib import Path
from unittest.mock import Mock

from module.config import server

server.set_lang('global_cn')

from tasks.base.assets.assets_base_popup import AD_BUFF_X_CLOSE
from tasks.base.page import Page, page_knights, page_knights_weekly_task
from tasks.knights.assets.assets_knights_activity_weekly_task_entry import WEEKLY_TASK_CHECK
from tasks.knights.assets.assets_knights_main_page import KNIGHTS_CHECK
from tests.support.history_main_navigation import CapturedRoute, frame, image_from_asset
from tests.support.offline import fixture_image


MANIFEST = Path(__file__).resolve().parents[1] / 'fixtures/knights/manifest.json'
CAPTURES = ('20261011-120648-865249', '20261011-120834-058')


class KnightsWeeklyPageTests(unittest.TestCase):
    def setUp(self):
        previous_language = server.lang
        self.addCleanup(server.set_lang, previous_language)
        self.addCleanup(Page.clear_connection)
        self.addCleanup(AD_BUFF_X_CLOSE.clear_offset)
        server.set_lang('global_cn')
        AD_BUFF_X_CLOSE.clear_offset()

    def test_weekly_identity_uses_point_icons_and_rejects_sibling_tabs(self):
        task = CapturedRoute([frame(0)])
        for capture in CAPTURES:
            with self.subTest(capture=capture):
                task.device.image = fixture_image(MANIFEST, capture)
                self.assertTrue(task.ui_page_appear(page_knights_weekly_task))
                self.assertTrue(WEEKLY_TASK_CHECK.matched_button.file.endswith('.2.png'))
                # The four-digit total uses the second icon template. The title
                # is not part of this language-independent page identity.
                task.device.image[56:126, 295:446] = 0
                self.assertTrue(task.ui_page_appear(page_knights_weekly_task))

        # Sibling tabs share the activity panel but neither is a weekly page.
        for capture in ('20261002-160820-406', '20261002-160823-507'):
            with self.subTest(capture=capture):
                task.device.image = fixture_image(MANIFEST, capture)
                self.assertFalse(task.ui_page_appear(page_knights_weekly_task))
        task.device.image = frame(0)['image']
        self.assertFalse(task.ui_page_appear(page_knights_weekly_task))

    def test_weekly_return_retries_visible_close_until_home_arrives(self):
        current = fixture_image(MANIFEST, CAPTURES[0])
        # Only the weekly page is captured. The final guild-home anchor is a
        # synthetic destination, not evidence of a successful real-device click.
        home = image_from_asset(KNIGHTS_CHECK.matched_button)
        frames = [frame(index * 0.5, current) for index in range(6)]
        frames.append(frame(3.0, home))
        task = CapturedRoute(frames)
        task.handle_ad_buff_x_close = Mock(side_effect=AssertionError('activity panel closed as ad'))

        task.run_route(page_knights)

        self.assertEqual(task.index, 6)
        self.assertEqual(
            [(at, button.name, area) for at, button, area in task.clicks],
            [(0.0, 'AD_BUFF_X_CLOSE', (731, 690, 745, 704)),
             (2.0, 'AD_BUFF_X_CLOSE', (731, 690, 745, 704))],
        )
        task.handle_ad_buff_x_close.assert_not_called()
        self.assertTrue(task.ui_page_appear(page_knights))

    def test_weekly_identity_rejects_missing_icon_after_second_frame_match(self):
        task = CapturedRoute([frame(0, fixture_image(MANIFEST, CAPTURES[1]))])
        self.assertTrue(task.ui_page_appear(page_knights_weekly_task))
        self.assertTrue(WEEKLY_TASK_CHECK.matched_button.file.endswith('.2.png'))
        # A cached match must not turn an absent point icon into a known page.
        # Mask both templates' search regions after first observing frame two.
        task.device.image[172:236, 320:394] = 0
        self.assertFalse(task.ui_page_appear(page_knights_weekly_task))
