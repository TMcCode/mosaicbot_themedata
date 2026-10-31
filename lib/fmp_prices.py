"""FMP end-of-day prices → monthly average closes (freight / ETF proxies)."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from lib import fmp as fmplib


def fetch_monthly_avg_close(
    symbol: str,
    *,
    start: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Monthly mean close for ``symbol`` from FMP EOD history."""
    rows = fmplib._get(
        "historical-price-eod/full",
        {"symbol": str(symbol).upper()},
    )
    if not isinstance(rows, list) or not rows:
        raise RuntimeError(f"FMP EOD empty for {symbol}")

    by_month: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        d = str(row.get("date") or "")[:10]
        c = row.get("close")
        if len(d) < 10 or c is None or c == "":
            continue
        if start and d < start:
            continue
        try:
            by_month[f"{d[:7]}-01"].append(float(c))
        except (TypeError, ValueError):
            continue

    pts = [
        {"date": d, "value": round(sum(vs) / len(vs), 4)}
        for d, vs in sorted(by_month.items())
        if vs
    ]
    if not pts:
        raise RuntimeError(f"FMP EOD: no monthly points for {symbol}")

    meta = {
        "source": "fmp_eod",
        "symbol": str(symbol).upper(),
        "n_days": len(rows),
        "n_months": len(pts),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    return pts, meta
