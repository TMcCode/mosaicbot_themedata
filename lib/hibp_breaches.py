"""Have I Been Pwned — public breach catalogue (no API key).

``GET /api/v3/breaches`` returns the full corpus. We bucket by ``AddedDate``
(when HIBP disclosed the breach — market urgency) and optionally sum
``PwnCount``. Spam-list rows are dropped by default.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

UA = "mosaicbot_themedata/0.1 (+hibp-breaches; research)"
HIBP = "https://haveibeenpwned.com/api/v3/breaches"


def _month_end(d: date) -> str:
    if d.month == 12:
        return date(d.year, 12, 31).isoformat()
    return (date(d.year, d.month + 1, 1) - timedelta(days=1)).isoformat()


def _get_breaches() -> list[dict[str, Any]]:
    req = urllib.request.Request(
        HIBP,
        headers={"User-Agent": UA, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            rows = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"HIBP HTTP {e.code}: {body}") from e
    if not isinstance(rows, list):
        raise RuntimeError(f"HIBP unexpected payload: {type(rows)}")
    return rows


def monthly_breach_adds(
    *,
    start: str = "2018-01-01",
    end: str | None = None,
    exclude_spam: bool = True,
    metric: str = "count",
) -> tuple[list[dict[str, float]], dict[str, Any]]:
    """Monthly series of HIBP breach disclosures.

    ``metric``: ``count`` (number of breaches added) or ``pwn_count``
    (sum of PwnCount for breaches added that month).
    """
    start_d = date.fromisoformat(str(start)[:10])
    end_d = (
        date.fromisoformat(str(end)[:10])
        if end
        else datetime.now(timezone.utc).date()
    )
    rows = _get_breaches()
    by_month_count: dict[str, int] = defaultdict(int)
    by_month_pwn: dict[str, float] = defaultdict(float)
    kept = 0
    skipped_spam = 0
    for b in rows:
        if not isinstance(b, dict):
            continue
        if exclude_spam and b.get("IsSpamList"):
            skipped_spam += 1
            continue
        added = str(b.get("AddedDate") or "")[:10]
        if not added or added < start_d.isoformat() or added > end_d.isoformat():
            continue
        y, m, *_ = added.split("-")
        me = _month_end(date(int(y), int(m), 1))
        by_month_count[me] += 1
        try:
            by_month_pwn[me] += float(b.get("PwnCount") or 0)
        except (TypeError, ValueError):
            pass
        kept += 1

    use_pwn = str(metric or "count").strip().lower() in (
        "pwn_count",
        "pwncount",
        "accounts",
    )
    pts: list[dict[str, float]] = []
    cur = date(start_d.year, start_d.month, 1)
    last = date(end_d.year, end_d.month, 1)
    while cur <= last:
        me = _month_end(cur)
        if use_pwn:
            val = float(by_month_pwn.get(me) or 0.0)
        else:
            val = float(by_month_count.get(me) or 0.0)
        pts.append({"date": me, "value": val})
        if cur.month == 12:
            cur = date(cur.year + 1, 1, 1)
        else:
            cur = date(cur.year, cur.month + 1, 1)

    meta = {
        "source": "hibp_breaches",
        "metric": "pwn_count" if use_pwn else "count",
        "breaches_kept": kept,
        "skipped_spam": skipped_spam,
        "corpus": len(rows),
        "start": start_d.isoformat(),
        "end": end_d.isoformat(),
    }
    return pts, meta
