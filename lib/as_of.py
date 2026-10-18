"""Shared as_of helpers for scores + dispatcher freshness."""

from __future__ import annotations

from datetime import date, datetime, timezone


def last_complete_month_start(today: date | None = None) -> date:
    """First day of the last fully completed calendar month."""
    today = today or datetime.now(timezone.utc).date()
    if today.month == 1:
        return date(today.year - 1, 12, 1)
    return date(today.year, today.month - 1, 1)


def freshness_as_of(raw: date, *, today: date | None = None) -> date:
    """Clamp vendor/period as_of for "how stale is this pack?" checks.

    USASpending (and similar) can emit *future* or incomplete current-month
    ``YYYY-MM-01`` period_end dates. Those must not yield negative age.

    Do **not** clamp true daily vendor_as_of stamps (e.g. Kalshi on the 16th) —
    only future dates and period_end labels on the 1st of the current month.
    """
    today = today or datetime.now(timezone.utc).date()
    month_start = date(today.year, today.month, 1)
    # Future print → treat as last complete month for staleness.
    if raw > today:
        return last_complete_month_start(today)
    # Incomplete current month period_end (…-01 while still in that month).
    if raw.day == 1 and raw >= month_start:
        return last_complete_month_start(today)
    return raw


def monthly_as_of_cap_iso(now: datetime | None = None) -> str:
    """YYYY-MM-01 of last complete month (score history / latest cap)."""
    now = now or datetime.now(timezone.utc)
    return last_complete_month_start(now.date()).isoformat()
