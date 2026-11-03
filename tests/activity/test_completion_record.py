"""Completion-record attendance: verified page entry without reward clicks."""


from module.config import server

server.set_lang("global_cn")

from tasks.activity.completion_record import CompletionRecord
from tests.support.activity_replay import (
    ATTENDANCE,
    HOME,
    ActivityReplayCase,
    capture,
)


class CompletionRecordReplayTests(ActivityReplayCase):
    def test_attendance_records_only_after_verified_page_without_any_click(self):
        r = self.replay(CompletionRecord, [capture(ATTENDANCE)])
        self.assertTrue(r.task.run_claim(navigate=False))
        self.assertEqual(len(r.task.config.values), 1)
        self.assertFalse(r.actions)

    def test_wrong_attendance_page_never_records_or_claims(self):
        r = self.replay(CompletionRecord, [capture(HOME)])
        self.assertFalse(r.task.run_claim(navigate=False))
        self.assertFalse(r.task.config.values)
        self.assertFalse(r.actions)
