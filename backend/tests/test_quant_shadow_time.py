import unittest
from datetime import date, datetime, timedelta, timezone

from app.quant_shadow_time import CompletedSession, seven_session_target


class ShadowTimeTests(unittest.TestCase):
    def setUp(self):
        dates = [date(2026, 11, day) for day in (9, 10, 11, 12, 13, 16, 17, 18)]
        self.sessions = tuple(
            CompletedSession(day, datetime(day.year, day.month, day.day, 21, tzinfo=timezone.utc))
            for day in dates
        )

    def test_matures_at_close_of_seventh_subsequent_session(self):
        first, last = self.sessions[0], self.sessions[-1]
        self.assertIsNone(seven_session_target(
            self.sessions, first.date, last.close - timedelta(microseconds=1)
        ))
        self.assertEqual(seven_session_target(self.sessions, first.date, last.close), last.date)

    def test_missing_decision_and_duplicate_sessions_are_rejected(self):
        with self.assertRaises(ValueError):
            seven_session_target(self.sessions, date(2026, 11, 15), self.sessions[-1].close)
        with self.assertRaises(ValueError):
            seven_session_target(self.sessions * 2, self.sessions[0].date, self.sessions[-1].close)
