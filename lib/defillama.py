"""DefiLlama free APIs — stablecoin totals, DEX volume, chain TVL."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from urllib.request import Request, urlopen

UA = "mosaicbot_themedata/0.1 (+defillama)"
STABLE = "https://stablecoins.llama.fi"
API = "https://api.llama.fi"


def _get(base: str, path: str) -> Any:
    req = Request(
        f"{base}{path}",
        headers={"User-Agent": UA, "Accept": "application/json"},
    )
    with urlopen(req, timeout=90) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _ts_to_date(ts: Any) -> str | None:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError, OSError):
        return None


def fetch_total_stablecoin_circulating_monthly() -> list[tuple[str, float]]:
    """Monthly (YYYY-MM-01, USD) total stablecoin circulating (peggedUSD)."""
    rows = _get(STABLE, "/stablecoincharts/all")
    if not isinstance(rows, list):
        raise RuntimeError("unexpected stablecoincharts payload")
    by_month: dict[str, float] = {}
    for row in rows:
        d = _ts_to_date(row.get("date"))
        if not d:
            continue
        circ = (row.get("totalCirculatingUSD") or {}).get("peggedUSD")
        if circ is None:
            circ = (row.get("totalCirculating") or {}).get("peggedUSD")
        if circ is None:
            continue
        try:
            val = float(circ)
        except (TypeError, ValueError):
            continue
        by_month[f"{d[:7]}-01"] = val
    return [(k, by_month[k]) for k in sorted(by_month)]


def _weekly_sum_from_daily_chart(chart: list[Any]) -> list[tuple[str, float]]:
    from datetime import date as date_cls

    by_week: dict[str, float] = {}
    for row in chart:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        d = _ts_to_date(row[0])
        if not d:
            continue
        try:
            val = float(row[1])
        except (TypeError, ValueError):
            continue
        dt = datetime.fromisoformat(d).date()
        ahead = 6 - dt.weekday()
        week_end = date_cls.fromordinal(dt.toordinal() + ahead).isoformat()
        by_week[week_end] = by_week.get(week_end, 0.0) + val
    return [(k, by_week[k]) for k in sorted(by_week)]


def fetch_dex_volume_weekly() -> list[tuple[str, float]]:
    """Weekly Sunday-ending (date, USD) sum of aggregate DEX daily volume."""
    data = _get(
        API,
        "/overview/dexs?excludeTotalDataChart=false"
        "&excludeTotalDataChartBreakdown=true&dataType=dailyVolume",
    )
    return _weekly_sum_from_daily_chart(data.get("totalDataChart") or [])


def fetch_chain_dex_volume_weekly(chain: str = "Base") -> list[tuple[str, float]]:
    """Weekly Sunday-ending (date, USD) DEX volume for one chain."""
    data = _get(
        API,
        f"/overview/dexs/{chain}?excludeTotalDataChart=false"
        "&excludeTotalDataChartBreakdown=true&dataType=dailyVolume",
    )
    return _weekly_sum_from_daily_chart(data.get("totalDataChart") or [])


def fetch_chain_tvl_monthly(chain: str = "Base") -> list[tuple[str, float]]:
    """Monthly (YYYY-MM-01, USD) chain TVL — last print in month wins."""
    rows = _get(API, f"/v2/historicalChainTvl/{chain}")
    if not isinstance(rows, list):
        raise RuntimeError(f"unexpected historicalChainTvl payload for {chain}")
    by_month: dict[str, float] = {}
    for row in rows:
        d = _ts_to_date(row.get("date"))
        if not d:
            continue
        try:
            val = float(row.get("tvl"))
        except (TypeError, ValueError):
            continue
        by_month[f"{d[:7]}-01"] = val
    return [(k, by_month[k]) for k in sorted(by_month)]
