"""Kalshi — public read API for binary / bucket market probability history.

Uses daily candlesticks (mid of yes bid/ask close) → 0–100 probability points.
No auth for market + candlestick GETs on the elections trade API.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API = "https://api.elections.kalshi.com/trade-api/v2"
UA = "mosaicbot_themedata/0.1 (+kalshi)"


def _get_json(path: str, params: dict[str, Any] | None = None) -> Any:
    qs = f"?{urlencode(params)}" if params else ""
    url = f"{API}{path}{qs}"
    req = Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urlopen(req, timeout=45) as resp:
        raw = resp.read()
        status = getattr(resp, "status", None) or resp.getcode()
    if status != 200:
        raise RuntimeError(f"Kalshi HTTP {status} for {path}")
    return json.loads(raw.decode("utf-8"))


def fetch_market(ticker: str) -> dict[str, Any]:
    body = _get_json(f"/markets/{ticker}")
    return body.get("market") or body


def _dollars(x: Any) -> float | None:
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def candlestick_mid_prob(stick: dict[str, Any]) -> float | None:
    """Yes probability 0–100 from a daily candlestick."""
    bid = _dollars(((stick.get("yes_bid") or {}).get("close_dollars")))
    ask = _dollars(((stick.get("yes_ask") or {}).get("close_dollars")))
    if bid is not None and ask is not None:
        return ((bid + ask) / 2.0) * 100.0
    close = _dollars(((stick.get("price") or {}).get("close_dollars")))
    if close is not None:
        return close * 100.0
    prev = _dollars(((stick.get("price") or {}).get("previous_dollars")))
    if prev is not None:
        return prev * 100.0
    return None


def fetch_daily_probability(
    *,
    series_ticker: str,
    market_ticker: str,
    lookback_days: int = 400,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Daily mid probability history + market meta snapshot."""
    meta = fetch_market(market_ticker)
    end_ts = int(time.time())
    start_ts = end_ts - max(lookback_days, 30) * 86400
    body = _get_json(
        f"/series/{series_ticker}/markets/{market_ticker}/candlesticks",
        {
            "start_ts": start_ts,
            "end_ts": end_ts,
            "period_interval": 1440,  # daily
        },
    )
    points: list[dict[str, Any]] = []
    for stick in body.get("candlesticks") or []:
        ts = stick.get("end_period_ts")
        prob = candlestick_mid_prob(stick)
        if ts is None or prob is None:
            continue
        day = datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat()
        points.append({"date": day, "value": round(prob, 4)})
    # Dedupe by date (keep last)
    by_day: dict[str, float] = {}
    for p in points:
        by_day[str(p["date"])] = float(p["value"])
    pts = [{"date": d, "value": by_day[d]} for d in sorted(by_day)]

    last = _dollars(meta.get("last_price_dollars"))
    if last is not None:
        today = datetime.now(timezone.utc).date().isoformat()
        if not pts or pts[-1]["date"] < today:
            pts.append({"date": today, "value": round(last * 100.0, 4)})
        else:
            pts[-1] = {"date": today, "value": round(last * 100.0, 4)}

    mm = {
        "ticker": market_ticker,
        "series_ticker": series_ticker,
        "event_ticker": meta.get("event_ticker"),
        "title": meta.get("title") or meta.get("yes_sub_title") or meta.get("no_sub_title"),
        "last_price": last,
        "volume": meta.get("volume_fp") or meta.get("volume"),
        "open_interest": meta.get("open_interest_fp") or meta.get("open_interest"),
        "close_time": meta.get("close_time"),
        "url": f"https://kalshi.com/markets/{series_ticker.lower()}",
    }
    return pts, mm
