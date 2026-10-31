"""SEC EDGAR full-text — monthly count of Form 8-K Item 1.05 (material cyber incidents).

Item 1.05 took effect mid-December 2023. Uses the public EFTS search index
(``efts.sec.gov``) and keeps hits whose ``items`` list includes ``1.05``.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

UA = "mosaicbot_themedata/0.1 (research; sec-edgar read-only)"
EFTS = "https://efts.sec.gov/LATEST/search-index"
_PAGE = 100


def _get(url: str, *, retries: int = 4) -> dict[str, Any]:
    last: Exception | None = None
    for i in range(retries):
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last = e
            time.sleep(1.2 * (i + 1))
    assert last is not None
    raise RuntimeError(f"SEC EFTS failed: {last}") from last


def _month_end(d: date) -> str:
    if d.month == 12:
        return date(d.year, 12, 31).isoformat()
    return (date(d.year, d.month + 1, 1) - timedelta(days=1)).isoformat()


def monthly_item_105_counts(
    *,
    start: str = "2024-01-01",
    end: str | None = None,
) -> tuple[list[dict[str, float]], dict[str, Any]]:
    """Return monthly {date: month-end, value: count} for 8-K Item 1.05 filings."""
    start_d = date.fromisoformat(str(start)[:10])
    end_d = (
        date.fromisoformat(str(end)[:10])
        if end
        else datetime.now(timezone.utc).date()
    )
    if end_d < start_d:
        return [], {"hits": 0, "start": start_d.isoformat(), "end": end_d.isoformat()}

    by_month: dict[str, set[str]] = defaultdict(set)
    offset = 0
    total_hits = 0
    scanned = 0
    while True:
        qs = urllib.parse.urlencode(
            {
                "q": '"Item 1.05"',
                "forms": "8-K",
                "dateRange": "custom",
                "startdt": start_d.isoformat(),
                "enddt": end_d.isoformat(),
                "from": offset,
            }
        )
        payload = _get(f"{EFTS}?{qs}")
        hits = (payload.get("hits") or {}).get("hits") or []
        total_obj = (payload.get("hits") or {}).get("total") or {}
        if isinstance(total_obj, dict):
            total_hits = int(total_obj.get("value") or total_hits)
        else:
            total_hits = int(total_obj or total_hits)
        if not hits:
            break
        for h in hits:
            src = h.get("_source") if isinstance(h, dict) else None
            if not isinstance(src, dict):
                continue
            scanned += 1
            items = src.get("items") or []
            item_strs = [str(x).strip() for x in items]
            if "1.05" not in item_strs and not any(
                s.startswith("1.05") for s in item_strs
            ):
                continue
            fd = str(src.get("file_date") or "")[:10]
            adsh = str(src.get("adsh") or h.get("_id") or "")
            if not fd or fd < start_d.isoformat() or fd > end_d.isoformat():
                continue
            y, m, *_ = fd.split("-")
            me = _month_end(date(int(y), int(m), 1))
            by_month[me].add(adsh or f"{fd}:{scanned}")
        offset += len(hits)
        if offset >= total_hits or len(hits) < _PAGE:
            break
        time.sleep(0.35)

    # Dense monthly series from start month through end month (zeros ok).
    pts: list[dict[str, float]] = []
    cur = date(start_d.year, start_d.month, 1)
    last = date(end_d.year, end_d.month, 1)
    while cur <= last:
        me = _month_end(cur)
        pts.append({"date": me, "value": float(len(by_month.get(me) or ()))})
        if cur.month == 12:
            cur = date(cur.year + 1, 1, 1)
        else:
            cur = date(cur.year, cur.month + 1, 1)

    meta = {
        "source": "sec_efts_8k_item_1_05",
        "hits_reported": total_hits,
        "hits_scanned": scanned,
        "months": len(pts),
        "start": start_d.isoformat(),
        "end": end_d.isoformat(),
    }
    return pts, meta
