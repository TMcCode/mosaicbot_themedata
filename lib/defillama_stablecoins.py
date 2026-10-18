"""DefiLlama stablecoin circulating history (free, no key)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from urllib.request import Request, urlopen

UA = "mosaicbot_themedata/0.1 (+defillama_stablecoins)"
BASE = "https://stablecoins.llama.fi"


def _get(path: str) -> Any:
    req = Request(
        f"{BASE}{path}",
        headers={"User-Agent": UA, "Accept": "application/json"},
    )
    with urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_circulating_monthly(asset_id: int = 2) -> list[tuple[str, float]]:
    """Return monthly (YYYY-MM-01, circulating USD) for a DefiLlama pegged asset.

    Default asset_id=2 is USD Coin (USDC).
    """
    data = _get(f"/stablecoin/{int(asset_id)}")
    tokens = data.get("tokens") or []
    by_month: dict[str, float] = {}
    for row in tokens:
        ts = row.get("date")
        circ = (row.get("circulating") or {}).get("peggedUSD")
        if ts is None or circ is None:
            continue
        try:
            dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
            val = float(circ)
        except (TypeError, ValueError, OSError):
            continue
        # Last print in month wins (history densifies over time).
        by_month[f"{dt.year:04d}-{dt.month:02d}-01"] = val
    return [(d, by_month[d]) for d in sorted(by_month)]
