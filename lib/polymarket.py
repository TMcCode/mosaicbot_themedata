"""Polymarket — public read API for binary market probability history.

Discovery: gamma-api (event slug → clob token ids + live outcome prices).
History: CLOB prices-history (yes token) → daily 0–100 points.

No auth for these GETs. New markets often have short CLOB history — pack jobs
should append to a local jsonl (same pattern as DRAMeXchange) so weekly runs
densify the series going forward.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
UA = "mosaicbot_themedata/0.1 (+polymarket)"


def _get_json(url: str) -> Any:
    last: Exception | None = None
    for i in range(5):
        req = Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        try:
            with urlopen(req, timeout=45) as resp:
                raw = resp.read()
                status = getattr(resp, "status", None) or resp.getcode()
            if status != 200:
                raise RuntimeError(f"Polymarket HTTP {status} for {url}")
            return json.loads(raw.decode("utf-8"))
        except HTTPError as e:
            last = e
            if e.code in (429, 503):
                time.sleep(1.5 * (i + 1))
                continue
            raise
        except Exception as e:
            last = e
            time.sleep(0.8 * (i + 1))
    assert last is not None
    raise last


def _parse_jsonish(val: Any) -> Any:
    if isinstance(val, str):
        try:
            return json.loads(val)
        except json.JSONDecodeError:
            return val
    return val


def fetch_event(slug: str) -> dict[str, Any]:
    body = _get_json(f"{GAMMA}/events?slug={quote(slug)}")
    if isinstance(body, list):
        if not body:
            raise RuntimeError(f"Polymarket event not found: {slug}")
        return body[0]
    if isinstance(body, dict) and body.get("slug"):
        return body
    raise RuntimeError(f"Polymarket unexpected event payload for {slug}")


def resolve_market(
    *,
    event_slug: str,
    question_contains: str | None = None,
    token_id: str | None = None,
) -> dict[str, Any]:
    """Return market dict with yes_token_id + yes_price (0–1)."""
    event = fetch_event(event_slug)
    markets = event.get("markets") or []
    needle = (question_contains or "").strip().lower()
    chosen: dict[str, Any] | None = None
    for m in markets:
        ids = _parse_jsonish(m.get("clobTokenIds")) or []
        if not isinstance(ids, list) or not ids:
            continue
        yes_token = str(ids[0])
        if token_id and yes_token != str(token_id):
            continue
        q = (m.get("question") or m.get("groupItemTitle") or "").lower()
        if needle and needle not in q:
            continue
        prices = _parse_jsonish(m.get("outcomePrices")) or []
        yes_price = None
        if isinstance(prices, list) and prices:
            try:
                yes_price = float(prices[0])
            except (TypeError, ValueError):
                yes_price = None
        chosen = {
            **m,
            "event_slug": event.get("slug") or event_slug,
            "event_title": event.get("title"),
            "yes_token_id": yes_token,
            "yes_price": yes_price,
            "url": f"https://polymarket.com/event/{event.get('slug') or event_slug}",
        }
        if token_id or needle:
            break
    if not chosen:
        raise RuntimeError(
            f"Polymarket market not found slug={event_slug!r} "
            f"contains={question_contains!r} token={token_id!r}"
        )
    return chosen


def fetch_clob_daily_history(token_id: str) -> list[dict[str, Any]]:
    """Daily yes probability points (0–100) from CLOB prices-history."""
    # Prefer longest available window.
    hist: list[dict[str, Any]] = []
    for interval, fidelity in (("max", 1440), ("1m", 1440), ("1w", 60)):
        qs = urlencode(
            {"market": token_id, "interval": interval, "fidelity": fidelity}
        )
        try:
            body = _get_json(f"{CLOB}/prices-history?{qs}")
        except Exception:
            continue
        rows = body.get("history") or []
        if not rows:
            continue
        by_day: dict[str, float] = {}
        for row in rows:
            ts = row.get("t")
            p = row.get("p")
            if ts is None or p is None:
                continue
            day = datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat()
            by_day[day] = float(p) * 100.0
        hist = [{"date": d, "value": round(by_day[d], 4)} for d in sorted(by_day)]
        if hist:
            break
        time.sleep(0.2)
    return hist


def points_from_history(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    by_day: dict[str, float] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        d = row.get("date")
        v = row.get("value")
        if d is None or v is None:
            continue
        by_day[str(d)[:10]] = float(v)
    return [{"date": d, "value": by_day[d]} for d in sorted(by_day)]


def append_point(
    path: Path,
    *,
    date: str,
    value: float,
    meta: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Append/overwrite one day in jsonl history; return full point list."""
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = points_from_history(path)
    by_day = {p["date"]: p["value"] for p in existing}
    by_day[str(date)[:10]] = float(value)
    # Rewrite compact history (one line per day) for stable merges.
    lines = []
    for d in sorted(by_day):
        row: dict[str, Any] = {"date": d, "value": round(float(by_day[d]), 4)}
        if meta and d == str(date)[:10]:
            row.update({k: v for k, v in meta.items() if k not in row})
        lines.append(json.dumps(row, separators=(",", ":")))
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return [{"date": d, "value": by_day[d]} for d in sorted(by_day)]


def fetch_daily_probability(
    *,
    event_slug: str,
    question_contains: str | None = None,
    token_id: str | None = None,
    history_path: Path | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Resolve market, merge CLOB history + optional local jsonl, pin today."""
    market = resolve_market(
        event_slug=event_slug,
        question_contains=question_contains,
        token_id=token_id,
    )
    yes_token = str(market["yes_token_id"])
    clob_pts = fetch_clob_daily_history(yes_token)

    by_day: dict[str, float] = {}
    if history_path is not None:
        for p in points_from_history(history_path):
            by_day[p["date"]] = float(p["value"])
    for p in clob_pts:
        by_day[p["date"]] = float(p["value"])

    today = datetime.now(timezone.utc).date().isoformat()
    live = market.get("yes_price")
    if live is not None:
        by_day[today] = float(live) * 100.0

    # Persist full merged history (CLOB can be short; local densifies weekly).
    if history_path is not None and by_day:
        meta = {
            "token_id": yes_token,
            "event_slug": market.get("event_slug"),
            "question": market.get("question"),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "source": "polymarket",
        }
        history_path.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        for d in sorted(by_day):
            row: dict[str, Any] = {
                "date": d,
                "value": round(float(by_day[d]), 4),
            }
            if d == today:
                row.update(meta)
            lines.append(json.dumps(row, separators=(",", ":")))
        history_path.write_text(
            "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8"
        )

    pts = [{"date": d, "value": round(by_day[d], 4)} for d in sorted(by_day)]
    mm = {
        "token_id": yes_token,
        "event_slug": market.get("event_slug"),
        "question": market.get("question"),
        "title": market.get("event_title"),
        "yes_price": live,
        "volume": market.get("volumeNum") or market.get("volume"),
        "liquidity": market.get("liquidityNum") or market.get("liquidity"),
        "url": market.get("url"),
        "condition_id": market.get("conditionId"),
    }
    return pts, mm
