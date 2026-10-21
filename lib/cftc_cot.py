"""CFTC Traders in Financial Futures (TFF) — public Socrata API."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

UA = "mosaicbot_themedata/0.1 (+cftc_cot)"
# Traders in Financial Futures (Combined), publicreporting.cftc.gov
TFF = "https://publicreporting.cftc.gov/resource/gpe5-46if.json"
# CME Bitcoin futures contract market code in TFF
CME_BTC_CODE = "133741"


def _get(params: dict[str, str]) -> list[dict[str, Any]]:
    url = f"{TFF}?{urlencode(params)}"
    req = Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urlopen(req, timeout=90) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if not isinstance(body, list):
        raise RuntimeError("unexpected CFTC TFF payload")
    return body


def fetch_bitcoin_lev_money_net() -> list[tuple[str, float]]:
    """Weekly (date, net contracts) leveraged-money long−short — CME Bitcoin.

    Closest free proxy to classic NonComm net in the TFF report.
    """
    rows = _get(
        {
            "$where": (
                f"commodity_name='BITCOIN' AND "
                f"cftc_contract_market_code='{CME_BTC_CODE}'"
            ),
            "$order": "report_date_as_yyyy_mm_dd ASC",
            "$limit": "5000",
        }
    )
    out: list[tuple[str, float]] = []
    for r in rows:
        raw = str(r.get("report_date_as_yyyy_mm_dd") or "")[:10]
        if len(raw) != 10:
            continue
        try:
            long_ = float(r.get("lev_money_positions_long") or 0)
            short = float(r.get("lev_money_positions_short") or 0)
        except (TypeError, ValueError):
            continue
        out.append((raw, long_ - short))
    return out


def fetch_bitcoin_open_interest() -> list[tuple[str, float]]:
    """Weekly (date, contracts) open interest — CME Bitcoin TFF."""
    rows = _get(
        {
            "$where": (
                f"commodity_name='BITCOIN' AND "
                f"cftc_contract_market_code='{CME_BTC_CODE}'"
            ),
            "$order": "report_date_as_yyyy_mm_dd ASC",
            "$limit": "5000",
        }
    )
    out: list[tuple[str, float]] = []
    for r in rows:
        raw = str(r.get("report_date_as_yyyy_mm_dd") or "")[:10]
        if len(raw) != 10:
            continue
        try:
            oi = float(r.get("open_interest_all") or 0)
        except (TypeError, ValueError):
            continue
        out.append((raw, oi))
    return out
