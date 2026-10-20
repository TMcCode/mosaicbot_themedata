"""FEC Independent Expenditures — monthly totals from official bulk CSVs.

Source: FEC S3 bulk downloads
  ``independent_expenditure_{cycle}.csv`` (Schedule E / IE filings).

Cleaning (v0):
- Keep latest ``file_num`` per (``spe_id``, ``tran_id``) to prefer amendments.
- Drop non-positive amounts.
- Drop single-txn amounts above ``max_txn_usd`` (junk / protest filings —
  e.g. multi-billion "TANKING" / "Warren Buffet" rows in 2026).
- Date: ``exp_date`` then ``dissem_dt`` (``DD-MMM-YY``).
- Merge cycles with per-month **max** (cycle files overlap at edges).

Point date = month-end (``as_of_semantics: period_end``).
"""

from __future__ import annotations

import csv
import io
import urllib.request
from calendar import monthrange
from collections import defaultdict
from datetime import datetime
from typing import Any

UA = "mosaicbot_themedata/0.1 (+lib.fec_ie)"
_S3 = (
    "https://cg-519a459a-0ea3-42c2-b7bc-fa1143481f74.s3-us-gov-west-1"
    ".amazonaws.com/bulk-downloads"
)
# Single IE > $50M is almost always junk or a bad amendment in the bulk file.
_DEFAULT_MAX_TXN = 50_000_000.0
_DATE_FMTS = ("%d-%b-%y", "%d-%b-%Y", "%m/%d/%Y", "%Y-%m-%d")


def _parse_dt(raw: str | None) -> datetime | None:
    s = (raw or "").strip()
    if not s:
        return None
    for fmt in _DATE_FMTS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _month_end(yyyy_mm: str) -> str:
    y, m = int(yyyy_mm[:4]), int(yyyy_mm[5:7])
    return f"{y:04d}-{m:02d}-{monthrange(y, m)[1]:02d}"


def _get_csv(url: str, *, timeout: float = 180) -> list[dict[str, str]]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        text = io.TextIOWrapper(resp, encoding="utf-8", errors="replace")
        return list(csv.DictReader(text))


def cycle_url(cycle: int) -> str:
    return f"{_S3}/{cycle}/independent_expenditure_{cycle}.csv"


def monthly_totals_for_cycle(
    cycle: int,
    *,
    max_txn_usd: float = _DEFAULT_MAX_TXN,
) -> tuple[dict[str, float], dict[str, Any]]:
    """Return ``{YYYY-MM: usd}`` and scrape stats for one election cycle file."""
    url = cycle_url(cycle)
    rows = _get_csv(url)
    by_key: dict[tuple[str | None, str | None], dict[str, str]] = {}
    for row in rows:
        key = (row.get("spe_id"), row.get("tran_id"))
        try:
            fn = int(row.get("file_num") or 0)
        except ValueError:
            fn = 0
        prev = by_key.get(key)
        if prev is None or fn >= int(prev.get("file_num") or 0):
            by_key[key] = row

    by_month: dict[str, float] = defaultdict(float)
    dropped_hi = 0
    skipped_date = 0
    skipped_amt = 0
    for row in by_key.values():
        dt = _parse_dt(row.get("exp_date")) or _parse_dt(row.get("dissem_dt"))
        if not dt:
            skipped_date += 1
            continue
        try:
            amt = float(str(row.get("exp_amo") or "0").replace(",", "").replace("$", ""))
        except ValueError:
            skipped_amt += 1
            continue
        if amt <= 0:
            skipped_amt += 1
            continue
        if amt > max_txn_usd:
            dropped_hi += 1
            continue
        by_month[dt.strftime("%Y-%m")] += amt

    meta = {
        "cycle": cycle,
        "url": url,
        "raw_rows": len(rows),
        "deduped_rows": len(by_key),
        "dropped_over_max_txn": dropped_hi,
        "skipped_no_date": skipped_date,
        "skipped_amt": skipped_amt,
        "max_txn_usd": max_txn_usd,
        "months": len(by_month),
    }
    return dict(by_month), meta


def monthly_points(
    *,
    cycles: tuple[int, ...] = (2022, 2024, 2026),
    max_txn_usd: float = _DEFAULT_MAX_TXN,
    start: str | None = None,
) -> tuple[list[dict[str, float]], list[dict[str, Any]]]:
    """Merge cycle files → month-end points (USD).

    Overlapping months take the **max** across cycle files (same filings can
    appear at cycle edges; max avoids double-count without undercounting a
    fresher file).
    """
    merged: dict[str, float] = {}
    metas: list[dict[str, Any]] = []
    for cycle in cycles:
        by_month, meta = monthly_totals_for_cycle(cycle, max_txn_usd=max_txn_usd)
        metas.append(meta)
        # Exclusive of current incomplete calendar month (period_end honesty).
        today = datetime.utcnow().strftime("%Y-%m")
        for ym, usd in by_month.items():
            if start and ym < start[:7]:
                continue
            if ym >= today:
                continue
            merged[ym] = max(merged.get(ym, 0.0), usd)

    points = [
        {"date": _month_end(ym), "value": float(usd)}
        for ym, usd in sorted(merged.items())
        if usd > 0
    ]
    return points, metas
