"""Carrot reward recognition, production-clock replays and event integration."""

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from module.config import server

server.set_lang("global_cn")

from module.config.config_updater import ConfigUpdater
from module.game_info.catalog import load_info
from module.ocr.ocr import Ocr
from tasks.activity import calendar, scheduling
from tasks.activity.assets.assets_activity_carrot_reward import (
    CARROT_REWARD_CHECK,
    CARROT_REWARD_POPUP_CHECK,
    OCR_CARROT_POINTS,
)
from tasks.activity.assets.assets_activity_common import COMMON_ACTIVITY_LIST
from tasks.activity.legacy.carrot_reward_2026_11_12.carrot_reward import CarrotReward
from tasks.activity.common_activity import CommonActivityBatch
from tests.support.activity_replay import ActivityReplayCase, capture
from tests.support.carrot_reward import (
    AVAILABLE,
    CAKE,
    EVENT_ID,
    POPUP,
    RECEIVED,
    RULES,
    CarrotReplay,
)
from tests.support.interaction import scene

NOW = datetime.fromisoformat("2026-11-12T20:20:00+08:00")
ARCHIVE_CALENDAR = Path("tasks/activity/legacy/carrot_reward_2026_11_12/calendar.yaml")


class CarrotRewardTests(ActivityReplayCase):
    def replay_carrot(self, frames):
        return CarrotReplay(frames, self.clock)

    def test_real_pages_and_four_independent_tier_states(self):
        for fixture, expected in ((AVAILABLE, ["pending"] * 4), (RECEIVED, ["received"] * 4)):
            r = self.replay_carrot([capture(fixture)])
            self.assertTrue(r.task.match_template_color(CARROT_REWARD_CHECK))
            self.assertEqual([r.task._reward_state(area) for area in CarrotReward.REWARD_AREAS], expected)
            self.assertFalse(r.task.match_template_color(CARROT_REWARD_POPUP_CHECK))
        for fixture in (CAKE, RULES, POPUP):
            r = self.replay_carrot([capture(fixture)])
            self.assertFalse(r.task.match_template_color(CARROT_REWARD_CHECK))
            self.assertEqual(r.task.match_template_color(CARROT_REWARD_POPUP_CHECK), fixture == POPUP)

    def test_real_ocr_reads_complete_points_and_sidebar_name(self):
        r = self.replay_carrot([capture(AVAILABLE)])
        self.assertEqual(Ocr(OCR_CARROT_POINTS, lang="cn").ocr_single_line(r.image).strip(), "880")
        names = Ocr(COMMON_ACTIVITY_LIST, lang="cn").detect_and_ocr(r.image)
        self.assertTrue(any("一起收集胡萝卜" in row.ocr_text.replace(" ", "") for row in names), names)

    def test_claim_once_popup_and_all_four_receipts(self):
        r = self.replay_carrot([capture(AVAILABLE), capture(POPUP), capture(RECEIVED)])
        self.assertTrue(r.task.run_claim(navigate=False))
        self.assertEqual(r.actions, [(0, "CARROT_CLAIM_200"), (1, "TOUCH_TO_CLOSE")])
        self.assertEqual(r.index, 2)
        self.assertEqual(list(r.task.config.values), [scheduling._checked_path(r.task.config, EVENT_ID)])

    def test_already_received_finishes_without_clicks(self):
        r = self.replay_carrot([capture(RECEIVED)])
        self.assertTrue(r.task.run_claim(navigate=False))
        self.assertFalse(r.actions)
        self.assertTrue(r.task.config.values)

    def test_receipts_before_popup_do_not_complete_pending_claim(self):
        r = self.replay_carrot([capture(AVAILABLE)] + [capture(RECEIVED)] * 4
                               + [capture(POPUP), capture(RECEIVED)])
        self.assertTrue(r.task.run_claim(navigate=False))
        self.assertEqual(r.actions, [(0, "CARROT_CLAIM_200"), (5, "TOUCH_TO_CLOSE")])
        self.assertEqual(r.index, 6)

    def test_dropped_claim_and_close_retry_only_their_own_action(self):
        r = self.replay_carrot([capture(AVAILABLE)] * 7 + [capture(POPUP)] * 7 + [capture(RECEIVED)])
        self.assertTrue(r.task.run_claim(navigate=False))
        r.assert_retried(self, "CARROT_CLAIM_200", 2)
        r.assert_retried(self, "TOUCH_TO_CLOSE", 2)
        self.assertEqual(r.clicks("CARROT_CLAIM_200"), [0, 5])
        self.assertEqual(r.clicks("TOUCH_TO_CLOSE"), [7, 12])
        self.assertFalse(r.clicks("CARROT_CLAIM_400"))
        self.assertEqual(r.index, 14)

    def test_partial_receipts_keep_tier_identity(self):
        r = self.replay_carrot([capture(AVAILABLE, carrot_receipts=(0,)), capture(POPUP), capture(RECEIVED)])
        self.assertEqual([r.task._reward_state(area) for area in CarrotReward.REWARD_AREAS],
                         ["received", "pending", "pending", "pending"])
        self.assertTrue(r.task.run_claim(navigate=False))
        self.assertEqual(r.actions, [(0, "CARROT_CLAIM_400"), (1, "TOUCH_TO_CLOSE")])
        self.assertEqual(r.click_areas[0][2], CarrotReward.REWARD_AREAS[1])

    def test_zero_and_unfinished_points_keep_battle_recheck(self):
        for text, receipts in (("0", ()), ("199", ()), ("399", (0,)), ("799", (0, 1, 2))):
            r = self.replay_carrot([capture(AVAILABLE, carrot_locked=True, carrot_receipts=receipts)])
            with patch("tasks.activity.legacy.carrot_reward_2026_11_12.carrot_reward.Ocr") as ocr:
                ocr.return_value.ocr_single_line.return_value = text
                self.assertTrue(r.task.run_claim(navigate=False))
                self.assertEqual(ocr.call_args.kwargs["lang"], "cn")
            self.assertFalse(r.actions)
            self.assertFalse(r.task.config.values)

    def test_unknown_points_and_eligible_missing_controls_never_finish(self):
        for text in ("", "unreadable", "880", "-1", "880/800", "200"):
            r = self.replay_carrot([capture(AVAILABLE, carrot_locked=True, seconds=1)] * 48)
            with patch("tasks.activity.legacy.carrot_reward_2026_11_12.carrot_reward.Ocr") as ocr:
                ocr.return_value.ocr_single_line.return_value = text
                self.assertFalse(r.task.run_claim(navigate=False))
            self.assertFalse(r.actions)
            self.assertFalse(r.task.config.values)

    def test_missing_reward_popup_cannot_complete_or_extend_deadline(self):
        r = self.replay_carrot([capture(AVAILABLE)] + [capture(RECEIVED, seconds=1)] * 48)
        self.assertFalse(r.task.run_claim(navigate=False))
        self.assertEqual(r.actions, [(0, "CARROT_CLAIM_200")])
        self.assertFalse(r.task.config.values)

    def test_slow_repeated_claim_frames_still_respect_original_deadline(self):
        r = self.replay_carrot([capture(AVAILABLE, seconds=6)] * 10)
        self.assertFalse(r.task.run_claim(navigate=False))
        self.assertEqual(r.clicks("CARROT_CLAIM_200"), list(range(8)))
        self.assertEqual(r.index, 8)
        self.assertFalse(r.task.config.values)

    def test_recovery_from_existing_reward_popup(self):
        r = self.replay_carrot([capture(POPUP), capture(RECEIVED)])
        self.assertTrue(r.task.run_claim(navigate=False))
        self.assertEqual(r.actions, [(0, "TOUCH_TO_CLOSE")])
        self.assertTrue(r.task.config.values)

    def test_cake_and_comparison_rules_are_never_claimed(self):
        for fixture in (CAKE, RULES):
            r = self.replay_carrot([capture(fixture)])
            self.assertFalse(r.task.run_claim(navigate=False))
            self.assertFalse(r.actions)
            self.assertFalse(r.task.config.values)

    def test_network_popup_does_not_acknowledge_reward(self):
        network = scene("NETWORK_ERROR_ABNORMAL", "TOUCH_TO_CLOSE")
        r = self.replay_carrot([capture(AVAILABLE), network] + [capture(RECEIVED)] * 4
                               + [capture(POPUP), capture(RECEIVED)])
        self.assertTrue(r.task.run_claim(navigate=False))
        self.assertEqual(r.clicks("TOUCH_TO_CLOSE"), [1, 6])
        self.assertEqual(r.index, 7)

    def test_independent_navigation_confirms_carrot_target(self):
        r = self.replay_carrot([capture(RECEIVED)])
        r.task.ui_goto = Mock()
        r.task.select_activity = Mock(wraps=r.task.select_activity)
        self.assertTrue(r.task.run_claim())
        r.task.select_activity.assert_called_once_with("一起收集胡萝卜", CARROT_REWARD_CHECK)
        self.assertFalse(r.actions)

    def test_real_sidebar_selection_then_claim_never_repeats_tab(self):
        r = self.replay_carrot([capture(CAKE), capture(CAKE), capture(AVAILABLE),
                               capture(POPUP), capture(RECEIVED)])
        r.task.ui_goto = Mock()
        self.assertTrue(r.task.run_claim())
        self.assertEqual([index for index, _ in r.actions], [1, 2, 3])
        self.assertEqual(r.actions[1:], [(2, "CARROT_CLAIM_200"), (3, "TOUCH_TO_CLOSE")])
        left, top, right, bottom = r.click_areas[0][2]
        self.assertTrue(0 <= left < right <= 260 and 195 <= top < bottom <= 265)
        self.assertTrue(r.task.config.values)

    def test_real_common_batch_dispatches_claim_and_preserves_page_search(self):
        r = self.replay(CommonActivityBatch, [capture(AVAILABLE), capture(POPUP), capture(RECEIVED)])
        worker = object.__new__(CarrotReward)
        worker.device, worker.config, worker.interval_timer = r, r.task.config, {}
        worker.activity_id = EVENT_ID
        pending = {EVENT_ID: ("一起收集胡萝卜", CARROT_REWARD_CHECK, worker)}
        searches = tuple(button.search for button in CARROT_REWARD_CHECK.iter_buttons())
        with patch.object(r.task, "FULL_PAGE_MARKERS", CarrotReward.FULL_PAGE_MARKERS), patch(
            "tasks.activity.common_activity.Ocr"
        ) as ocr:
            ocr.return_value.detect_and_ocr.side_effect = AssertionError("Selected page must skip sidebar OCR")
            self.assertTrue(r.task._run_pending(pending))
        self.assertFalse(pending)
        self.assertEqual(r.actions, [(0, "CARROT_CLAIM_200"), (1, "TOUCH_TO_CLOSE")])
        self.assertEqual(tuple(button.search for button in CARROT_REWARD_CHECK.iter_buttons()), searches)
        self.assertTrue(r.task.config.values)

    def test_calendar_boundaries_server_support_and_batch_registration(self):
        r = self.replay_carrot([capture(RECEIVED)])
        event = calendar.ActivityWindow.from_period(load_info(ARCHIVE_CALENDAR).periods[0])
        self.assertEqual(event.event_id, EVENT_ID)
        self.assertFalse(event.contains(event.start - timedelta(seconds=1)))
        self.assertTrue(event.contains(event.start))
        self.assertTrue(event.contains(event.end - timedelta(seconds=1)))
        self.assertFalse(event.contains(event.end))
        self.assertEqual(event.end.isoformat(), "2026-11-26T11:00:00+08:00")
        self.assertNotIn("carrot_reward", CommonActivityBatch.ACTIVITIES)
        for package, lang in (("CN-Official", "cn"), ("OVERSEA-Play", "global_en"),
                              ("OVERSEA-Play", "global_cn")):
            r.task.config.Emulator_PackageName = package
            self.assertNotIn(EVENT_ID, {e.event_id for e in calendar.active_activities(
                r.task.config, NOW, assets_lang=lang)})

    def test_visibility_uses_saved_profile_and_preserves_choices(self):
        updater = ConfigUpdater()
        data = {"Alas": {"Emulator": {"PackageName": "OVERSEA-Play", "GameLanguage": "cn"}},
                "LimitedActivity": {"LimitedActivity": {"GetCarrotReward": False}}}
        key = "LimitedActivity.LimitedActivity.GetCarrotReward"
        with patch.object(calendar, "datetime", wraps=datetime) as clock:
            clock.now.return_value = NOW
            with patch.object(server, "lang", "cn"):
                self.assertIn(key, updater.get_hidden_args(data))
            for package, lang in (("CN-Official", "cn"), ("OVERSEA-Play", "en"), ("auto", "cn")):
                data["Alas"]["Emulator"].update(PackageName=package, GameLanguage=lang)
                self.assertIn(key, updater.get_hidden_args(data))
            data["Alas"]["Emulator"].update(PackageName="OVERSEA-Play", GameLanguage="cn")
            clock.now.return_value = datetime.fromisoformat("2026-11-26T11:00:00+08:00")
            self.assertIn(key, updater.get_hidden_args(data))
        self.assertFalse(data["LimitedActivity"]["LimitedActivity"]["GetCarrotReward"])
        self.assertFalse(updater.config_update(data)["LimitedActivity"]["LimitedActivity"]["GetCarrotReward"])

    def test_archived_claim_preserves_daily_receipt_without_battle_recheck(self):
        r = self.replay_carrot([capture(RECEIVED)])
        config = r.task.config
        with patch.object(
            scheduling, "get_server_last_update", return_value=datetime.now() - timedelta(days=1)
        ) as reset:
            self.assertFalse(scheduling.is_activity_checked_today(config, EVENT_ID))
            self.assertTrue(r.task.run_claim(navigate=False))
            self.assertTrue(scheduling.is_activity_checked_today(config, EVENT_ID))
            reset.return_value = datetime.now().replace(microsecond=0) + timedelta(days=1)
            self.assertFalse(scheduling.is_activity_checked_today(config, EVENT_ID))
        window = calendar.ActivityWindow.from_period(load_info(ARCHIVE_CALENDAR).periods[0])
        with patch.object(scheduling, "active_activities", return_value=(window,)):
            for arena in (False, True):
                self.assertFalse(scheduling.should_schedule_after_battle(config, arena=arena))
