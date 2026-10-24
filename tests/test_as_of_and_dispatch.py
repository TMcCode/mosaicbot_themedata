"""Freshness clamp + dispatcher due/stale catch-up."""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lib.as_of import freshness_as_of, last_complete_month_start  # noqa: E402
from jobs.dispatch_due import is_due  # noqa: E402


class FreshnessAsOfTests(unittest.TestCase):
    def test_week_ending_future_clamps_to_today(self):
        today = date(2026, 10, 23)  # Fri
        week_ending = date(2026, 10, 25)  # Sun
        self.assertEqual(freshness_as_of(week_ending, today=today), today)

    def test_future_period_end_clamps_to_last_complete_month(self):
        today = date(2026, 10, 23)
        self.assertEqual(
            freshness_as_of(date(2026, 12, 1), today=today),
            date(2026, 9, 1),
        )

    def test_incomplete_current_month_period_end(self):
        today = date(2026, 10, 23)
        self.assertEqual(
            freshness_as_of(date(2026, 10, 1), today=today),
            date(2026, 9, 1),
        )

    def test_past_vendor_as_of_unchanged(self):
        today = date(2026, 10, 23)
        self.assertEqual(
            freshness_as_of(date(2026, 10, 16), today=today),
            date(2026, 10, 16),
        )

    def test_last_complete_month_start(self):
        self.assertEqual(last_complete_month_start(date(2026, 10, 23)), date(2026, 9, 1))
        self.assertEqual(last_complete_month_start(date(2026, 1, 5)), date(2025, 12, 1))


class DispatchDueTests(unittest.TestCase):
    def _pack(self, **due):
        return {
            "id": "lab_economics",
            "enabled": True,
            "due": due,
            "freshness": {},
        }

    def test_week_ending_not_stale_midweek(self):
        pack = self._pack(weekdays=["Tue"], max_age_days=8)
        status = {"packs": {}}
        today = date(2026, 10, 23)  # Fri
        with patch("jobs.dispatch_due._read_freshness_as_of", return_value=date(2026, 10, 25)):
            due, reason = is_due(pack, today=today, status=status, force=False)
        self.assertFalse(due)
        self.assertIn("fresh", reason)
        self.assertNotIn("stale", reason)

    def test_stale_skipped_after_recent_success(self):
        """Monthly print still Sept, but we already refreshed yesterday → skip."""
        pack = self._pack(day_of_month_from=12, day_of_month_to=22, max_age_days=25)
        status = {
            "packs": {
                "lab_economics": {
                    "ok": True,
                    "last_success_at": "2026-10-22T14:00:00+00:00",
                    "as_of": "2026-09-01",
                }
            }
        }
        today = date(2026, 10, 23)  # outside 12-22 window
        with patch(
            "jobs.dispatch_due._read_freshness_as_of", return_value=date(2026, 9, 1)
        ):
            due, reason = is_due(pack, today=today, status=status, force=False)
        self.assertFalse(due)
        self.assertIn("recent success", reason)
        self.assertIn("stale", reason)

    def test_stale_catchup_when_success_aged_out(self):
        pack = self._pack(day_of_month_from=12, day_of_month_to=22, max_age_days=25)
        status = {
            "packs": {
                "lab_economics": {
                    "ok": True,
                    "last_success_at": "2026-09-20T14:00:00+00:00",
                    "as_of": "2026-09-01",
                }
            }
        }
        today = date(2026, 10, 23)
        with patch(
            "jobs.dispatch_due._read_freshness_as_of", return_value=date(2026, 9, 1)
        ):
            due, reason = is_due(pack, today=today, status=status, force=False)
        self.assertTrue(due)
        self.assertIn("stale", reason)

    def test_in_window_still_runs_despite_recent_success(self):
        pack = self._pack(weekdays=["Fri"], max_age_days=8)
        status = {
            "packs": {
                "lab_economics": {
                    "ok": True,
                    "last_success_at": "2026-10-16T14:00:00+00:00",
                    "as_of": "2026-10-16",
                }
            }
        }
        today = date(2026, 10, 23)  # Fri
        with patch(
            "jobs.dispatch_due._read_freshness_as_of", return_value=date(2026, 10, 16)
        ):
            due, reason = is_due(pack, today=today, status=status, force=False)
        self.assertTrue(due)
        self.assertIn("weekday=Fri", reason)


if __name__ == "__main__":
    unittest.main()
