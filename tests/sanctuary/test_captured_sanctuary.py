"""Pixel regressions on global-cn captures; replays are explicitly repeated views."""

import unittest
from types import SimpleNamespace

from module.config import server

server.set_lang('global_cn')

from tasks.base.assets.assets_base_popup import POPUP_CANCEL  # noqa: E402
from tasks.sanctuary.monthly import (  # noqa: E402
    CUSTODY, OCR_PURIFY_TIMES_FULL, OCR_PURIFY_TIMES_NOT_FULL, OcrPurifyTimes,
    REWARDS_TIER_A, REWARDS_TIER_B, REWARDS_TIER_S,
)
from tasks.sanctuary.monthly_deposit import deposit_tiers_increased, match_deposit_tiers  # noqa: E402
from tasks.sanctuary.sanctuary import Sanctuary  # noqa: E402
from tasks.sanctuary.assets.assets_sanctuary_forest_of_elves import ALTAR_OF_GROWTH, CLAIM_REWARDS  # noqa: E402
from tests.support.offline import ControlledClock  # noqa: E402
from tests.support.sanctuary import CapturedMonthlyReplay, captured_screenshot  # noqa: E402


CAPTURES = (
    '20261001-160533-108', '20261001-160538-174', '20261001-160544-910',
    '20261001-160549-774', '20261001-160552-887', '20261001-160555-309',
    '20261001-160557-977', '20261001-160606-837',
)
S_ONE = ('S', None, None, None, None)
S_TWO = ('S', 'S', None, None, None)
S_TWO_A = ('S', 'S', 'A', None, None)


def task_for_image(image):
    task = Sanctuary.__new__(Sanctuary)
    task.device = SimpleNamespace(image=image)
    task.appear = lambda asset, interval=0, **kwargs: asset.match_template(image, **kwargs)
    return task


class CapturedMonthlyTests(unittest.TestCase):
    def setUp(self):
        server.set_lang('global_cn')

    def test_monthly_deposit_slots_in_captured_frames(self):
        expected = (S_ONE, S_ONE, (None,) * 5, S_TWO, S_TWO, S_TWO, S_TWO, S_TWO_A)
        for fixture_id, tiers in zip(CAPTURES, expected):
            with self.subTest(fixture=fixture_id):
                self.assertEqual(match_deposit_tiers(captured_screenshot(fixture_id)), tiers)

    def test_captured_s_custody_adds_one_slot(self):
        before = match_deposit_tiers(captured_screenshot(CAPTURES[1]))
        after = match_deposit_tiers(captured_screenshot(CAPTURES[3]))
        self.assertEqual((before, after), (S_ONE, S_TWO))
        self.assertTrue(deposit_tiers_increased(before, after))

    def test_captured_a_custody_adds_one_slot(self):
        before = match_deposit_tiers(captured_screenshot(CAPTURES[6]))
        after = match_deposit_tiers(captured_screenshot(CAPTURES[7]))
        self.assertEqual((before, after), (S_TWO, S_TWO_A))
        self.assertTrue(deposit_tiers_increased(before, after))

    def test_refreshes_do_not_change_the_deposit_count(self):
        tiers = [match_deposit_tiers(captured_screenshot(fixture_id)) for fixture_id in CAPTURES[4:7]]
        self.assertEqual(tiers, [S_TWO] * 3)
        for before, after in zip(tiers, tiers[1:]):
            self.assertFalse(deposit_tiers_increased(before, after))

    def check_settle(self, before_id, after_id, expected):
        before = match_deposit_tiers(captured_screenshot(before_id))
        # These repeats model fresh screenshot requests seeing an unchanged
        # view. They are not two independently captured adjacent frames and
        # do not claim the entire manual sequence was run by the automation.
        repeats = 2 if expected else 16
        with ControlledClock() as clock:
            flow = CapturedMonthlyReplay(before_id, [after_id] * repeats, clock)
            result = flow.task._wait_monthly_custody_settle(None, before)
        self.assertEqual(result, expected)
        self.assertEqual(flow.frames_read, repeats)
        self.assertEqual(flow.actions, [])

    def test_custody_settle_accepts_captured_s_with_gray_button(self):
        self.assertFalse(CUSTODY.match_color(captured_screenshot(CAPTURES[3]), threshold=10))
        self.check_settle(CAPTURES[1], CAPTURES[3], True)

    def test_custody_settle_accepts_captured_a_with_gray_button(self):
        self.assertFalse(CUSTODY.match_color(captured_screenshot(CAPTURES[7]), threshold=10))
        self.check_settle(CAPTURES[6], CAPTURES[7], True)

    def test_unchanged_capture_cannot_confirm_custody(self):
        self.check_settle(CAPTURES[1], CAPTURES[1], False)

    def test_monthly_controls_and_popup_in_captured_frames(self):
        enabled = (False, True, False, False, True, True, True, False)
        for index, fixture_id in enumerate(CAPTURES):
            with self.subTest(fixture=fixture_id):
                image = captured_screenshot(fixture_id)
                task = task_for_image(image)
                self.assertEqual(CUSTODY.match_color(image, threshold=10), enabled[index])
                self.assertFalse(task._is_monthly_claimed())
                self.assertIs(task._is_monthly_deposit_box_full(), None if index == 2 else False)
                self.assertEqual(POPUP_CANCEL.match_template(image), index == 2)
                if index == 2:
                    self.assertFalse(deposit_tiers_increased(S_ONE, match_deposit_tiers(image)))
        for index, asset in ((1, REWARDS_TIER_S), (4, REWARDS_TIER_B), (5, REWARDS_TIER_A), (6, REWARDS_TIER_A)):
            self.assertTrue(asset.match_template(captured_screenshot(CAPTURES[index])))

    def test_resource_balance_ocr_in_captured_frames(self):
        full = OcrPurifyTimes(OCR_PURIFY_TIMES_FULL, lang='cn')
        not_full = OcrPurifyTimes(OCR_PURIFY_TIMES_NOT_FULL, lang='cn')
        for index, balance in ((0, 310), (1, 300), (3, 300), (4, 290), (5, 280), (6, 270), (7, 270)):
            with self.subTest(fixture=CAPTURES[index]):
                task = task_for_image(captured_screenshot(CAPTURES[index]))
                current, _, cost, layout = task._ocr_purify_times(full, not_full, preferred_layout='not_full')
                self.assertEqual((current, cost, layout), (balance, 10, 'not_full'))

    def test_capture_loader_rejects_another_server(self):
        with self.assertRaisesRegex(ValueError, 'Capture server mismatch'):
            captured_screenshot(CAPTURES[1], expected_server='cn')


class CapturedForestTests(unittest.TestCase):
    def test_forest_page_and_three_claimable_rewards(self):
        server.set_lang('global_cn')
        image = captured_screenshot('20260517-230524-881')
        self.assertTrue(ALTAR_OF_GROWTH.match_template(image))
        matches = CLAIM_REWARDS.match_multi_template(image)
        centers = sorted((int((item.area[0] + item.area[2]) / 2),
                          int((item.area[1] + item.area[3]) / 2)) for item in matches)
        self.assertEqual(centers, [(995, 262), (995, 378), (995, 493)])
        self.assertEqual(match_deposit_tiers(image), (None,) * 5)
        self.assertIsNone(task_for_image(image)._is_monthly_deposit_box_full())


if __name__ == '__main__':
    unittest.main()
