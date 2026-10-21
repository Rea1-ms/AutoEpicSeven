"""Arena-only support rewards, popup confirmation and the 11:00 daily reset."""

import json
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta
from unittest.mock import patch

from module.config import server
from module.game_info.catalog import parse_info

server.set_lang("global_cn")

from tasks.activity import calendar, scheduling
from tasks.activity.assets.assets_activity_special_26_10_19 import (
    SUPPORT_GATE_CHECK,
    SUPPORT_TASK_GO,
    SUPPORT_TASK_RECEIVED,
    SUPPORT_TASK_REWARD_PENDING,
)
from tasks.activity.common_activity import CommonActivityBatch
from tasks.activity.limited_activity import LimitedActivityEntry
from tests.support.interaction import scene
from tests.support.offline import ControlledClock, record_frame
from tests.support.support_reward import (
    AVAILABLE,
    CAPTURES,
    EVENT_ID,
    HOME,
    INCOMPLETE,
    POPUP,
    RECEIVED,
    WEEKLY,
    SupportReplay,
    captured,
)

NOW = datetime.fromisoformat("2026-10-19T21:20:00+08:00")


class SupportRewardTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.clock = self.stack.enter_context(ControlledClock())

    def replay(self, frames):
        return SupportReplay(frames, self.clock)

    def test_real_assets_reject_other_pages_popup_and_moved_daily_row(self):
        self.assertIs(CommonActivityBatch.ACTIVITIES["support_reward"][1], SUPPORT_GATE_CHECK)
        for fixture in CAPTURES:
            with self.subTest(fixture=fixture):
                r = self.replay([captured(fixture)])
                ready = fixture in (AVAILABLE, INCOMPLETE, RECEIVED, WEEKLY)
                self.assertEqual(r.task.match_template_color(SUPPORT_GATE_CHECK), ready)
                self.assertEqual(
                    r.task._activity_selected(SUPPORT_GATE_CHECK), ready
                )
                self.assertEqual(
                    r.task.match_template_color(SUPPORT_TASK_REWARD_PENDING),
                    fixture == AVAILABLE,
                )
                if ready:
                    self.assertIs(
                        r.task._daily_state(),
                        {
                            AVAILABLE: False,
                            INCOMPLETE: False,
                            RECEIVED: True,
                            WEEKLY: None,
                        }[fixture],
                    )
                    # The page check must work with every sidebar highlight
                    # removed, without OCR or another navigation click.
                    r.image[:, :280] = 0
                    record_frame("synthetic:support-sidebar-hidden")
                    with patch("tasks.activity.navigation.Ocr") as ocr:
                        self.assertTrue(r.task.select_activity("应援任务", SUPPORT_GATE_CHECK))
                        ocr.return_value.detect_and_ocr.assert_not_called()
                    self.assertFalse(r.actions)

    def test_claim_popup_then_scan_weekly_and_daily_receipt(self):
        r = self.replay(
            [captured(AVAILABLE)] * 2
            + [captured(POPUP), captured(WEEKLY)] * 1
            + [captured(WEEKLY), captured(RECEIVED)]
        )
        self.assertTrue(r.task.run_claim(navigate=False))
        self.assertEqual(
            r.actions,
            [
                (0, "swipe"),
                (1, "SUPPORT_TASK_REWARD_PENDING"),
                (2, "TOUCH_TO_CLOSE"),
                (3, "swipe"),
                (4, "swipe"),
            ],
        )
        self.assertEqual(r.index, 5)
        self.assertIn(EVENT_ID, next(iter(r.task.config.values)))
        for button in (SUPPORT_TASK_GO, SUPPORT_TASK_RECEIVED):
            self.assertEqual(button.search, SUPPORT_TASK_REWARD_PENDING.search)

    def test_incomplete_daily_stays_eligible_and_never_clicks_go(self):
        r = self.replay([captured(INCOMPLETE)] * 3)
        self.assertTrue(r.task.run_claim(navigate=False))
        self.assertEqual(r.actions, [(0, "swipe"), (1, "swipe")])
        self.assertEqual(r.task.config.values, {})

    def test_receipt_before_popup_does_not_complete_claim(self):
        r = self.replay(
            [captured(AVAILABLE)] * 2
            + [captured(RECEIVED)] * 4
            + [captured(POPUP)]
            + [captured(RECEIVED)] * 3
        )
        self.assertTrue(r.task.run_claim(navigate=False))
        self.assertEqual(r.clicks("TOUCH_TO_CLOSE"), [6])
        self.assertEqual(r.index, 9)

    def test_missing_popup_times_out_without_recording_completion(self):
        r = self.replay(
            [captured(AVAILABLE)] * 2 + [captured(RECEIVED, seconds=1)] * 50
        )
        self.assertFalse(r.task.run_claim(navigate=False))
        self.assertEqual(r.task.config.values, {})
        self.assertEqual(r.clicks("SUPPORT_TASK_REWARD_PENDING"), [1])
        self.assertFalse(r.clicks("TOUCH_TO_CLOSE"))

    def test_dropped_claim_and_close_obey_production_intervals(self):
        r = self.replay(
            [captured(AVAILABLE)] * 8 + [captured(POPUP)] * 8 + [captured(RECEIVED)] * 3
        )
        self.assertTrue(r.task.run_claim(navigate=False))
        self.assertEqual(r.clicks("SUPPORT_TASK_REWARD_PENDING"), [1, 6])
        self.assertEqual(r.clicks("TOUCH_TO_CLOSE"), [8, 13])
        r.assert_retried(self, "SUPPORT_TASK_REWARD_PENDING", 2)
        r.assert_retried(self, "TOUCH_TO_CLOSE", 2)

    def test_network_dialog_cannot_replace_reward_popup(self):
        network = scene("NETWORK_ERROR_ABNORMAL", "TOUCH_TO_CLOSE")
        r = self.replay(
            [captured(AVAILABLE)] * 2
            + [network] * 2
            + [captured(RECEIVED)] * 4
            + [captured(POPUP)]
            + [captured(RECEIVED)] * 3
        )
        self.assertTrue(r.task.run_claim(navigate=False))
        self.assertEqual(r.clicks("TOUCH_TO_CLOSE"), [2, 8])
        self.assertEqual(r.index, 11)

    def test_synthetic_weekly_reward_is_claimed_before_daily_scan_finishes(self):
        r = self.replay(
            [captured(WEEKLY, weekly_claim=True)] * 2
            + [captured(POPUP)]
            + [captured(WEEKLY)] * 2
            + [captured(RECEIVED)]
        )
        self.assertTrue(r.task.run_claim(navigate=False))
        self.assertEqual(r.clicks("SUPPORT_TASK_REWARD_PENDING"), [1])
        self.assertEqual(r.clicks("TOUCH_TO_CLOSE"), [2])
        self.assertEqual(r.index, 5)

    def test_new_weekly_claim_waits_until_previous_popup_is_closed(self):
        r = self.replay(
            [captured(AVAILABLE)] * 2
            + [captured(WEEKLY, weekly_claim=True)] * 6
            + [captured(POPUP)]
            + [captured(WEEKLY, weekly_claim=True)] * 2
            + [captured(POPUP)] * 3
            + [captured(RECEIVED)] * 3
        )
        self.assertTrue(r.task.run_claim(navigate=False))
        self.assertEqual(r.clicks("SUPPORT_TASK_REWARD_PENDING"), [1, 10])
        self.assertEqual(r.clicks("TOUCH_TO_CLOSE"), [8, 13])
        self.assertEqual(r.index, 16)

    def test_bad_refresh_hour_is_rejected_before_scheduling(self):
        for hour in (-1, 24, True, "11"):
            with (
                self.subTest(hour=hour),
                self.assertRaisesRegex(ValueError, "refresh_hour"),
            ):
                parse_info(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "defaults": {
                                "support_reward": {
                                    "source": "Offline invalid hour",
                                    "refresh_hour": hour,
                                }
                            },
                            "events": [],
                        }
                    )
                )

    def test_wrong_tab_rejects_claim_without_actions(self):
        r = self.replay([captured(HOME)])
        self.assertFalse(r.task.run_claim(navigate=False))
        self.assertFalse(r.actions)

    def test_weekly_checkmark_cannot_complete_unfinished_daily(self):
        r = self.replay([captured(INCOMPLETE)] * 3)
        # Explicit synthetic weekly receipt: keep the daily row unchanged.
        original = r.task._daily_state
        states = []

        def read():
            image = r.image.copy()
            receipt = self.replay([captured(RECEIVED)]).image
            image[479:511, 1108:1154] = receipt[614:646, 1108:1154]
            r.image = image
            states.append(original())
            return states[-1]

        r.task._daily_state = read
        self.assertTrue(r.task.run_claim(navigate=False))
        self.assertEqual(states, [False, False])
        self.assertFalse(r.task.config.values)

    def test_arena_only_scheduling_uses_saved_switch_and_independent_receipt(self):
        r = self.replay([captured(INCOMPLETE)])
        event = next(
            e
            for e in calendar.active_activities(r.task.config, NOW)
            if e.event_id == EVENT_ID
        )
        self.stack.enter_context(
            patch.object(
                scheduling,
                "active_activities",
                side_effect=lambda config, *, task: (
                    (event,) if task == "LimitedActivity" else ()
                ),
            )
        )
        self.stack.enter_context(
            patch.object(scheduling, "aware_time", return_value=NOW)
        )
        scheduling.schedule_activity_after_battle(r.task.config)
        self.assertFalse(r.task.config.calls)
        scheduling.schedule_activity_after_battle(r.task.config, arena=True)
        self.assertEqual(
            r.task.config.calls, [("LimitedActivity", {"force_call": False})]
        )
        r.task.config.values["LimitedActivity.LimitedActivity.GetSupportReward"] = False
        self.assertFalse(
            scheduling.should_schedule_after_battle(r.task.config, arena=True)
        )
        r.task.config.values.clear()
        scheduling.mark_activity_checked(r.task.config, "meirin_reward_2026_10_08")
        self.assertTrue(
            scheduling.should_schedule_after_battle(r.task.config, arena=True)
        )
        r.task.config.values[scheduling._checked_path(r.task.config, EVENT_ID)] = (
            NOW.replace(tzinfo=None)
        )
        self.assertFalse(
            scheduling.should_schedule_after_battle(r.task.config, arena=True)
        )

    def test_calendar_boundaries_and_reset_ignore_scheduler_02(self):
        config = self.replay([captured(INCOMPLETE)]).task.config
        event = next(
            e for e in calendar.active_activities(config, NOW) if e.event_id == EVENT_ID
        )
        self.assertEqual(
            event.next_refresh(NOW).isoformat(), "2026-10-20T11:00:00+08:00"
        )
        config.values[scheduling._checked_path(config, EVENT_ID)] = NOW.replace(
            tzinfo=None
        )
        for at, expected in (
            ("2026-10-20T02:00:00+08:00", True),
            ("2026-10-20T10:59:59+08:00", True),
            ("2026-10-20T11:00:00+08:00", False),
        ):
            self.assertEqual(
                scheduling.is_activity_checked_in_window(
                    config, event, datetime.fromisoformat(at)
                ),
                expected,
            )
        for at, expected in (
            (event.start - timedelta(seconds=1), False),
            (event.start, True),
            (event.end, False),
        ):
            self.assertEqual(
                EVENT_ID
                in {e.event_id for e in calendar.active_activities(config, at)},
                expected,
            )
        for lang, package in (
            ("cn", "com.zlongame.cn.epicseven"),
            ("global_en", "com.stove.epic7.google"),
        ):
            server.set_lang(lang)
            config.Emulator_PackageName = package
            self.assertNotIn(
                EVENT_ID, {e.event_id for e in calendar.active_activities(config, NOW)}
            )

    def test_entry_batches_support_and_skips_disabled_or_checked(self):
        config = self.replay([captured(INCOMPLETE)]).task.config
        event = next(
            e for e in calendar.active_activities(config, NOW) if e.event_id == EVENT_ID
        )
        self.assertIn("support_reward", CommonActivityBatch.ACTIVITIES)
        with (
            patch("tasks.activity.entry.active_activities", return_value=(event,)),
            patch("tasks.activity.entry.CommonActivityBatch") as worker,
        ):
            worker.ACTIVITIES = {"support_reward": None}
            worker.return_value.run.return_value = True
            self.assertTrue(LimitedActivityEntry(config).run())
            worker.return_value.run.assert_called_once_with([event])
            worker.reset_mock()
            config.LimitedActivity_GetSupportReward = False
            self.assertTrue(LimitedActivityEntry(config).run())
            worker.assert_not_called()
