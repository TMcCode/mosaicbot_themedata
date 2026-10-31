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


def _norm_strike(strike: str | float | int | None) -> str | None:
    """Normalize ``5.8`` / ``T5.8`` / ``t5.8`` → ``T5.8``."""
    if strike is None or strike == "":
        return None
    s = str(strike).strip().upper()
    if not s:
        return None
    if not s.startswith("T"):
        s = f"T{s}"
    return s


def list_series_markets(
    series_ticker: str,
    *,
    status: str | None = "open",
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List markets under a series (status ``open`` ≈ active contracts)."""
    params: dict[str, Any] = {
        "series_ticker": str(series_ticker).strip().upper(),
        "limit": int(limit),
    }
    if status:
        params["status"] = status
    body = _get_json("/markets", params)
    return list(body.get("markets") or [])


def market_matches_strike(market: dict[str, Any], strike: str) -> bool:
    want = _norm_strike(strike)
    if not want:
        return True
    ticker = str(market.get("ticker") or "").upper()
    if ticker.endswith(f"-{want}") or f"-{want}-" in f"-{ticker}-":
        return True
    # Subtitle e.g. "Above 5.8 million barrels/day"
    blob = " ".join(
        str(market.get(k) or "")
        for k in ("yes_sub_title", "no_sub_title", "title", "subtitle")
    ).upper()
    num = want[1:]  # 5.8
    return bool(num and re.search(rf"(?:ABOVE|OVER|>)\s*{re.escape(num)}\b", blob))


def market_matches_roll(market: dict[str, Any], match: str | None) -> bool:
    """True if market ticker contains the year-stable ``match`` fragment."""
    if not match:
        return True
    want = str(match).strip().upper()
    if not want:
        return True
    if want.startswith("T") and re.fullmatch(r"T[0-9.]+", want):
        return market_matches_strike(market, want)
    ticker = str(market.get("ticker") or "").upper()
    return want in ticker


def resolve_series_market(
    series_ticker: str,
    *,
    strike: str | float | int | None = None,
    match: str | None = None,
    preferred_ticker: str | None = None,
) -> dict[str, Any]:
    """Pick the live market for an annual/rolling Kalshi series.

    Prefer ``preferred_ticker`` while it is still active and matches
    ``strike`` / ``match``. Otherwise choose the open market matching the
    roll fingerprint with the farthest ``close_time``. Raises if none match
    (caller should omit the series from the pack / page).
    """
    series = str(series_ticker or "").strip().upper()
    if not series:
        raise RuntimeError("kalshi series_ticker required")
    want_strike = _norm_strike(strike)
    want_match = str(match or want_strike or "").strip().upper() or None
    preferred = str(preferred_ticker or "").strip().upper() or None

    open_mkts = list_series_markets(series, status="open")
    # API sometimes returns status=active without filtering — keep open-ish.
    active = [
        m
        for m in open_mkts
        if str(m.get("status") or "").lower() in ("", "open", "active", "initialized")
    ]
    if not active:
        active = open_mkts

    if preferred:
        for m in active:
            if str(m.get("ticker") or "").upper() == preferred:
                if market_matches_roll(m, want_match):
                    return m

    matched = [m for m in active if market_matches_roll(m, want_match)]
    if not matched:
        tickers = [str(m.get("ticker") or "") for m in active[:12]]
        raise RuntimeError(
            f"no open Kalshi market for {series} match={want_match or '*'}; "
            f"open={tickers}"
        )

    def _close_key(m: dict[str, Any]) -> str:
        return str(m.get("close_time") or m.get("expiration_time") or "")

    matched.sort(key=_close_key, reverse=True)
    return matched[0]


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
        "status": meta.get("status"),
        "url": f"https://kalshi.com/markets/{series_ticker.lower()}",
    }
    return pts, mm


def fetch_daily_probability_auto(
    *,
    series_ticker: str,
    strike: str | float | int | None = None,
    match: str | None = None,
    preferred_ticker: str | None = None,
    lookback_days: int = 400,
    prior_ticker: str | None = None,
    prior_points: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Resolve open market for series+strike/match and pull that contract's path.

    On rollover (resolved ticker ≠ prior/preferred), **reset** the published
    series to the new contract only — do not stitch old-year odds onto the new
    path (different underlyings). ``prior_points`` are returned in meta as
    ``archived_points`` for optional offline retention, not merged into the chart.

    If no open successor matches, raises ``RuntimeError`` so the pack omits the
    series (chart drops from the page) rather than showing a stale path.
    """
    prev = str(prior_ticker or preferred_ticker or "").strip().upper() or None
    resolved = resolve_series_market(
        series_ticker,
        strike=strike,
        match=match,
        preferred_ticker=preferred_ticker,
    )
    ticker = str(resolved.get("ticker") or "").strip()
    if not ticker:
        raise RuntimeError(f"Kalshi resolve returned empty ticker for {series_ticker}")
    pts, mm = fetch_daily_probability(
        series_ticker=series_ticker,
        market_ticker=ticker,
        lookback_days=lookback_days,
    )
    rolled = bool(prev and prev != ticker.upper())
    archived: list[dict[str, Any]] = []
    if rolled and prior_points:
        archived = [
            {"date": str(p.get("date") or "")[:10], "value": float(p["value"])}
            for p in prior_points
            if p.get("date") and p.get("value") is not None
        ]
    mm = {
        **mm,
        "resolved_ticker": ticker,
        "preferred_ticker": preferred_ticker,
        "prior_ticker": prior_ticker or preferred_ticker,
        "strike": _norm_strike(strike),
        "match": str(match or _norm_strike(strike) or "").strip().upper() or None,
        "rolled": rolled,
        "reset_on_roll": True,
        "archived_points": archived,
        "title": resolved.get("title")
        or resolved.get("yes_sub_title")
        or mm.get("title"),
        "close_time": resolved.get("close_time") or mm.get("close_time"),
        "status": resolved.get("status") or mm.get("status"),
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
