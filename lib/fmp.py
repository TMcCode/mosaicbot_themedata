"""Financial Modeling Prep — cash-flow CapEx helpers.

Uses the stable cash-flow-statement endpoint (same as MosaicBotMain).
CapEx is reported as a cash outflow (negative); we publish abs() spend.

Oracle (and some others) file on fiscal quarter-ends that are not calendar
quarter-ends. Aggregates and stacks map each print onto a calendar quarter
end (Mar/Jun/Sep/Dec) so series share dates.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from lib.env import load_env, require_env

FMP_BASE = "https://financialmodelingprep.com/stable"
UA = "mosaicbot_themedata/0.1 (+fmp)"

# Cloud CapEx proxy for optical & fiber demand (Big-4 + Oracle OCI).
DEFAULT_HYPERSCALERS = ("MSFT", "GOOGL", "META", "AMZN", "ORCL")


def _api_key() -> str:
    load_env()
    return require_env("FMP_API_KEY")["FMP_API_KEY"].strip()


def _get(endpoint: str, params: dict[str, Any]) -> Any:
    q = dict(params)
    q["apikey"] = _api_key()
    url = f"{FMP_BASE}/{endpoint}?{urlencode(q)}"
    req = Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urlopen(req, timeout=45) as resp:
        raw = resp.read()
        status = getattr(resp, "status", None) or resp.getcode()
    if status != 200:
        raise RuntimeError(f"FMP HTTP {status} for {endpoint}")
    return json.loads(raw.decode("utf-8"))


def calendar_quarter_end(iso_date: str) -> str:
    """Map any period-end date onto calendar quarter end (Mar/Jun/Sep/Dec)."""
    y, m, _ = (int(x) for x in str(iso_date)[:10].split("-"))
    if m <= 3:
        return f"{y:04d}-03-31"
    if m <= 6:
        return f"{y:04d}-06-30"
    if m <= 9:
        return f"{y:04d}-09-30"
    return f"{y:04d}-12-31"


def fetch_quarterly_capex(
    symbol: str,
    *,
    limit: int = 40,
    calendar_bucket: bool = True,
    scale: float = 1.0,
) -> list[dict[str, Any]]:
    """Quarterly CapEx points for one ticker (abs spend).

    When calendar_bucket=True, dates are remapped to calendar Q ends and
    multiple prints in the same bucket are summed (rare).
    """
    rows = _get(
        "cash-flow-statement",
        {"symbol": symbol.upper(), "period": "quarter", "limit": int(limit)},
    )
    if not isinstance(rows, list):
        raise RuntimeError(f"FMP cash-flow unexpected for {symbol}: {type(rows)}")

    by_date: dict[str, float] = defaultdict(float)
    for row in rows:
        d = row.get("date")
        cap = row.get("capitalExpenditure")
        if not d or cap is None or cap == "":
            continue
        key = calendar_quarter_end(str(d)[:10]) if calendar_bucket else str(d)[:10]
        by_date[key] += abs(float(cap))

    out = [
        {
            "date": d,
            "value": round(v / scale, 4) if scale else round(v, 4),
            "symbol": symbol.upper(),
        }
        for d, v in sorted(by_date.items())
    ]
    return out


def fetch_capex_by_symbol(
    symbols: list[str] | tuple[str, ...] | None = None,
    *,
    limit: int = 40,
    scale: float = 1e9,
    calendar_bucket: bool = True,
    sleep_s: float = 0.15,
) -> dict[str, list[dict[str, Any]]]:
    """Fetch CapEx for each symbol (usd_billions when scale=1e9)."""
    tickers = [str(s).upper() for s in (symbols or DEFAULT_HYPERSCALERS)]
    out: dict[str, list[dict[str, Any]]] = {}
    for i, sym in enumerate(tickers):
        out[sym] = fetch_quarterly_capex(
            sym, limit=limit, calendar_bucket=calendar_bucket, scale=scale
        )
        if sleep_s and i + 1 < len(tickers):
            time.sleep(sleep_s)
    return out


def aggregate_capex_points(
    by_symbol: dict[str, list[dict[str, Any]]],
    *,
    require_all: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Sum CapEx across symbols on shared dates."""
    tickers = list(by_symbol.keys())
    by_date: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    per_ticker = {sym: len(pts) for sym, pts in by_symbol.items()}

    for sym, pts in by_symbol.items():
        for p in pts:
            d = p["date"]
            by_date[d] += float(p["value"])
            counts[d] += 1

    n = len(tickers)
    points: list[dict[str, Any]] = []
    for d in sorted(by_date):
        if require_all and counts[d] < n:
            continue
        points.append({"date": d, "value": round(by_date[d], 4)})

    meta = {
        "symbols": tickers,
        "require_all": require_all,
        "per_ticker_points": per_ticker,
        "quarters": len(points),
        "source": "fmp_cash_flow_statement",
        "unit": "usd_billions",
        "calendar_bucket": True,
    }
    return points, meta


def fetch_hyperscaler_capex_aggregate(
    symbols: list[str] | tuple[str, ...] | None = None,
    *,
    limit: int = 40,
    require_all: bool = True,
    scale: float = 1e9,
    sleep_s: float = 0.15,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Sum quarterly CapEx across hyperscalers (calendar-quarter aligned)."""
    by_sym = fetch_capex_by_symbol(
        symbols, limit=limit, scale=scale, sleep_s=sleep_s
    )
    return aggregate_capex_points(by_sym, require_all=require_all)
