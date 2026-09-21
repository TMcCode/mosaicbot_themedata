"""Manifold Markets — public read API for binary market probability history.

History is reconstructed from bets (probAfter), then downsampled to daily.
Play-money market; use only when liquid enough to be a backdrop signal.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API = "https://api.manifold.markets/v0"
UA = "mosaicbot_themedata/0.1 (+manifold)"


def _get_json(path: str, params: dict[str, Any] | None = None) -> Any:
    qs = f"?{urlencode(params)}" if params else ""
    url = f"{API}{path}{qs}"
    req = Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urlopen(req, timeout=45) as resp:
        raw = resp.read()
        status = getattr(resp, "status", None) or resp.getcode()
    if status != 200:
        raise RuntimeError(f"Manifold HTTP {status} for {path}")
    return json.loads(raw.decode("utf-8"))


def fetch_market(market_id: str) -> dict[str, Any]:
    return _get_json(f"/market/{market_id}")


def _fetch_all_bets(contract_id: str, *, max_pages: int = 20) -> list[dict[str, Any]]:
    """Paginate bets newest-first; stop when a page is short."""
    out: list[dict[str, Any]] = []
    before: str | None = None
    for _ in range(max_pages):
        params: dict[str, Any] = {"contractId": contract_id, "limit": 1000}
        if before:
            params["before"] = before
        page = _get_json("/bets", params)
        if not isinstance(page, list) or not page:
            break
        out.extend(page)
        before = page[-1].get("id")
        if len(page) < 1000:
            break
        time.sleep(0.15)
    return out


def bets_to_daily_probability(bets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One point per UTC day = last probAfter that day (0–100)."""
    by_day: dict[str, float] = {}
    ordered = sorted(
        (b for b in bets if b.get("probAfter") is not None and b.get("createdTime")),
        key=lambda b: int(b["createdTime"]),
    )
    if ordered and ordered[0].get("probBefore") is not None:
        # Opening print on the first bet's day
        ms = int(ordered[0]["createdTime"])
        day0 = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).date().isoformat()
        by_day[day0] = float(ordered[0]["probBefore"]) * 100.0

    for b in ordered:
        ms = int(b["createdTime"])
        day = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).date().isoformat()
        by_day[day] = float(b["probAfter"]) * 100.0

    return [{"date": d, "value": by_day[d]} for d in sorted(by_day)]


def fetch_binary_daily_probability(
    market_id: str,
    *,
    include_current: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return (daily points 0–100, market meta)."""
    market = fetch_market(market_id)
    bets = _fetch_all_bets(market_id)
    pts = bets_to_daily_probability(bets)
    if include_current and market.get("probability") is not None:
        today = datetime.now(timezone.utc).date().isoformat()
        cur = float(market["probability"]) * 100.0
        if pts and pts[-1]["date"] == today:
            pts[-1]["value"] = cur
        else:
            pts.append({"date": today, "value": cur})
    meta = {
        "market_id": market_id,
        "question": market.get("question"),
        "url": market.get("url"),
        "volume": market.get("volume"),
        "unique_bettors": market.get("uniqueBettorCount"),
        "probability": market.get("probability"),
        "close_time": market.get("closeTime"),
        "is_resolved": bool(market.get("isResolved")),
    }
    return pts, meta
