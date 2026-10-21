"""CoinGecko free public API helpers (no key; rate-limit politely)."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any
from urllib.request import Request, urlopen

UA = "mosaicbot_themedata/0.1 (+coingecko)"
BASE = "https://api.coingecko.com/api/v3"


def _get(path: str) -> Any:
    req = Request(
        f"{BASE}{path}",
        headers={"User-Agent": UA, "Accept": "application/json"},
    )
    last: Exception | None = None
    for i in range(4):
        try:
            with urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            last = e
            time.sleep(1.5 * (i + 1))
    assert last is not None
    raise last


def _ms_to_date(ms: Any) -> str | None:
    try:
        return datetime.fromtimestamp(float(ms) / 1000.0, tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError, OSError):
        return None


def fetch_exchange_volume_monthly_usd(
    exchange_id: str = "coinbase-exchange",
    *,
    days: int = 365,
) -> list[tuple[str, float]]:
    """Monthly (YYYY-MM-01, USD) exchange spot volume.

    CoinGecko volume_chart is BTC notional; multiply by BTC USD close and
    sum into calendar months (The Block-style monthly Coinbase proxy).
    """
    vol_rows = _get(f"/exchanges/{exchange_id}/volume_chart?days={int(days)}")
    time.sleep(1.2)
    px = _get(f"/coins/bitcoin/market_chart?vs_currency=usd&days={int(days)}")
    prices = px.get("prices") or []
    px_by_day: dict[str, float] = {}
    for row in prices:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        d = _ms_to_date(row[0])
        if not d:
            continue
        try:
            px_by_day[d] = float(row[1])
        except (TypeError, ValueError):
            continue

    by_month: dict[str, float] = {}
    for row in vol_rows or []:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        d = _ms_to_date(row[0])
        if not d:
            continue
        price = px_by_day.get(d)
        if price is None:
            # nearest prior day
            prior = [k for k in px_by_day if k <= d]
            price = px_by_day[max(prior)] if prior else None
        if price is None:
            continue
        try:
            btc_vol = float(row[1])
        except (TypeError, ValueError):
            continue
        month = f"{d[:7]}-01"
        by_month[month] = by_month.get(month, 0.0) + btc_vol * price
    return [(k, by_month[k]) for k in sorted(by_month)]
