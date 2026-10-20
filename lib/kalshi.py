"""Kalshi — public read API for binary / bucket market probability history.

Uses daily candlesticks (mid of yes bid/ask close) → 0–100 probability points.
No auth for market + candlestick GETs on the elections trade API.

Also: snapshot helpers for counting “close” federal race markets (spend proxy).
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API = "https://api.elections.kalshi.com/trade-api/v2"
UA = "mosaicbot_themedata/0.1 (+kalshi)"


def _get_json(path: str, params: dict[str, Any] | None = None) -> Any:
    qs = f"?{urlencode(params)}" if params else ""
    url = f"{API}{path}{qs}"
    last: Exception | None = None
    for i in range(5):
        req = Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        try:
            with urlopen(req, timeout=45) as resp:
                raw = resp.read()
                status = getattr(resp, "status", None) or resp.getcode()
            if status != 200:
                raise RuntimeError(f"Kalshi HTTP {status} for {path}")
            return json.loads(raw.decode("utf-8"))
        except HTTPError as e:
            last = e
            if e.code == 429:
                time.sleep(1.5 * (i + 1))
                continue
            raise
        except Exception as e:
            last = e
            time.sleep(0.8 * (i + 1))
    assert last is not None
    raise last


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


def _market_mid(m: dict[str, Any]) -> float | None:
    bid = _dollars(m.get("yes_bid_dollars"))
    ask = _dollars(m.get("yes_ask_dollars"))
    if bid is not None and ask is not None:
        return (bid + ask) / 2.0
    last = _dollars(m.get("last_price_dollars"))
    if last is not None:
        return last
    if bid is not None:
        return bid
    return None


def _list_series_tickers() -> list[str]:
    body = _get_json("/series", {"limit": 20000})
    return [str(s.get("ticker") or "") for s in (body.get("series") or []) if s.get("ticker")]


def _is_senate_race_series(ticker: str) -> bool:
    # SENATEXX / SENATEXXS (special) — not CONTROLS, SENATEMOV, etc.
    return bool(re.match(r"^SENATE[A-Z]{2}S?$", ticker))


def _is_house_race_series(ticker: str) -> bool:
    # HOUSEMI4, HOUSECA13, HOUSENJ09 — district series with a digit
    if not ticker.startswith("HOUSE"):
        return False
    if ticker in {"HOUSE", "HOUSEMOV"}:
        return False
    return any(ch.isdigit() for ch in ticker)


def snapshot_close_federal_races(
    *,
    cycle_token: str = "-26-",
    lo: float = 0.35,
    hi: float = 0.65,
    sleep_s: float = 0.25,
) -> dict[str, Any]:
    """Count open 2026 Senate/House *party* markets priced near a toss-up.

    Uses Democratic ``…-26-D`` (or ``…-26-D``-style) legs only so each race
    counts once. Snapshot only — append history in the pack job.
    """
    series = _list_series_tickers()
    senate_series = sorted({t for t in series if _is_senate_race_series(t)})
    house_series = sorted({t for t in series if _is_house_race_series(t)})

    def _scan(series_list: list[str]) -> tuple[int, int, list[dict[str, Any]]]:
        scanned = 0
        close_rows: list[dict[str, Any]] = []
        for st in series_list:
            time.sleep(sleep_s)
            try:
                body = _get_json(
                    "/markets",
                    {"series_ticker": st, "status": "open", "limit": 20},
                )
            except Exception:
                continue
            for m in body.get("markets") or []:
                ticker = str(m.get("ticker") or "")
                if cycle_token not in ticker:
                    continue
                if not ticker.endswith("-D"):
                    continue
                mid = _market_mid(m)
                if mid is None:
                    continue
                scanned += 1
                if lo <= mid <= hi:
                    close_rows.append(
                        {
                            "series": st,
                            "ticker": ticker,
                            "mid": round(mid, 4),
                            "title": (m.get("title") or "")[:80],
                        }
                    )
        return scanned, len(close_rows), close_rows

    s_scan, s_close, s_rows = _scan(senate_series)
    h_scan, h_close, h_rows = _scan(house_series)
    today = datetime.now(timezone.utc).date().isoformat()
    return {
        "date": today,
        "senate_close": s_close,
        "house_close": h_close,
        "federal_close": s_close + h_close,
        "senate_scanned": s_scan,
        "house_scanned": h_scan,
        "lo": lo,
        "hi": hi,
        "cycle_token": cycle_token,
        "senate_close_markets": s_rows,
        "house_close_markets": h_rows,
    }
