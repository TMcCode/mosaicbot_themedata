"""Cloudflare Radar API — bot / AI-crawl traffic shares.

Requires ``CLOUDFLARE_API_TOKEN`` with Account → Radar → Read.

We deliberately avoid the AI-bots ``MIN0_MAX`` absolute series (window-
renormalized each fetch — not append-safe). Prefer:

- HTTP ``bot_class`` **PERCENTAGE** (bot share of Cloudflare HTTP)
- AI bots ``timeseries_groups/CRAWL_PURPOSE`` **PERCENTAGE** (Training share)

Docs: https://developers.cloudflare.com/radar/
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

UA = "mosaicbot_themedata/0.1 (+lib.cloudflare_radar)"
API = "https://api.cloudflare.com/client/v4"


def api_token() -> str | None:
    tok = (
        (os.environ.get("CLOUDFLARE_API_TOKEN") or "").strip()
        or (os.environ.get("CLOUDFLARE_API_KEY") or "").strip()
    )
    return tok or None


def _http_get_json(path: str, *, params: dict[str, str] | None = None) -> dict[str, Any]:
    tok = api_token()
    if not tok:
        raise RuntimeError("CLOUDFLARE_API_TOKEN not set")
    q = urllib.parse.urlencode(params or {})
    url = f"{API}/{path.lstrip('/')}?{q}" if q else f"{API}/{path.lstrip('/')}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {tok}",
            "User-Agent": UA,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:400]
        raise RuntimeError(f"Cloudflare Radar HTTP {e.code}: {body}") from e
    if not payload.get("success", True):
        raise RuntimeError(f"Cloudflare Radar error: {payload.get('errors')}")
    return payload


def _ts_to_date(ts: str) -> str:
    """Radar timestamps → YYYY-MM-DD (UTC day)."""
    s = str(ts).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return str(ts)[:10]
    return dt.astimezone(timezone.utc).date().isoformat()


def _drop_incomplete_week(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not points:
        return points
    # Radar includes a partial current week — drop last point if it's this week.
    today = datetime.now(timezone.utc).date().isoformat()
    last = points[-1]["date"]
    # If last point is within ~6 days of today, treat as incomplete.
    try:
        from datetime import date, timedelta

        ld = date.fromisoformat(last)
        if (date.fromisoformat(today) - ld).days < 7:
            return points[:-1]
    except ValueError:
        pass
    return points


def fetch_bot_share_pct(*, weeks: int = 52) -> list[dict[str, Any]]:
    """Weekly bot % of Cloudflare HTTP traffic (PERCENTAGE, durable).

    Radar caps history around ~52 weeks for this endpoint.
    """
    weeks = max(4, min(int(weeks), 52))
    payload = _http_get_json(
        "radar/http/timeseries_groups/bot_class",
        params={
            "aggInterval": "1w",
            "dateRange": f"{weeks}w",
            "format": "json",
        },
    )
    serie = (payload.get("result") or {}).get("serie_0") or {}
    timestamps = serie.get("timestamps") or []
    bots = serie.get("bot") or []
    pts: list[dict[str, Any]] = []
    for ts, val in zip(timestamps, bots):
        try:
            v = float(val)
        except (TypeError, ValueError):
            continue
        pts.append({"date": _ts_to_date(ts), "value": v})
    return _drop_incomplete_week(pts)


def fetch_ai_crawl_purpose_pct(
    purpose: str = "Training",
    *,
    weeks: int = 52,
) -> list[dict[str, Any]]:
    """Weekly share of AI-bot HTTP by crawl purpose (PERCENTAGE).

    ``purpose`` matches Radar dimension labels, e.g. ``Training``, ``Search``.
    Radar caps history around ~52 weeks.
    """
    weeks = max(4, min(int(weeks), 52))
    payload = _http_get_json(
        "radar/ai/bots/timeseries_groups/CRAWL_PURPOSE",
        params={
            "aggInterval": "1w",
            "dateRange": f"{weeks}w",
            "format": "json",
        },
    )
    serie = (payload.get("result") or {}).get("serie_0") or {}
    timestamps = serie.get("timestamps") or []
    if purpose not in serie:
        available = [k for k in serie.keys() if k != "timestamps"]
        raise RuntimeError(
            f"Radar crawl purpose {purpose!r} missing; have {available}"
        )
    vals = serie.get(purpose) or []
    pts: list[dict[str, Any]] = []
    for ts, val in zip(timestamps, vals):
        try:
            v = float(val)
        except (TypeError, ValueError):
            continue
        pts.append({"date": _ts_to_date(ts), "value": v})
    return _drop_incomplete_week(pts)


def fetch_ai_bot_ua_pct(
    user_agent: str,
    *,
    weeks: int = 52,
) -> list[dict[str, Any]]:
    """Weekly share of AI-bot traffic for one USER_AGENT label (PERCENTAGE)."""
    weeks = max(4, min(int(weeks), 52))
    payload = _http_get_json(
        "radar/ai/bots/timeseries_groups/USER_AGENT",
        params={
            "aggInterval": "1w",
            "dateRange": f"{weeks}w",
            "format": "json",
        },
    )
    serie = (payload.get("result") or {}).get("serie_0") or {}
    timestamps = serie.get("timestamps") or []
    if user_agent not in serie:
        available = [k for k in serie.keys() if k != "timestamps"]
        raise RuntimeError(
            f"Radar AI UA {user_agent!r} missing; have {available}"
        )
    vals = serie.get(user_agent) or []
    pts: list[dict[str, Any]] = []
    for ts, val in zip(timestamps, vals):
        try:
            v = float(val)
        except (TypeError, ValueError):
            continue
        pts.append({"date": _ts_to_date(ts), "value": v})
    return _drop_incomplete_week(pts)


def fetch_l7_mitigation_share_pct(
    product: str = "WAF",
    *,
    weeks: int = 52,
) -> list[dict[str, Any]]:
    """Weekly share of L7 mitigations by product (PERCENTAGE, durable).

    Absolute L7 attack volume is only exposed as ``MIN0_MAX`` (window-
    renormalized — not append-safe). Mitigation-product mix is PERCENTAGE.
    ``product`` examples: ``WAF``, ``DDOS``, ``BOT_MANAGEMENT``.
    """
    weeks = max(4, min(int(weeks), 52))
    payload = _http_get_json(
        "radar/attacks/layer7/timeseries_groups/mitigation_product",
        params={
            "aggInterval": "1w",
            "dateRange": f"{weeks}w",
            "format": "json",
        },
    )
    serie = (payload.get("result") or {}).get("serie_0") or {}
    timestamps = serie.get("timestamps") or []
    key = str(product or "WAF").strip().upper()
    # Radar labels are uppercase (WAF, DDOS, …).
    if key not in serie:
        # allow soft case match
        lower = {str(k).upper(): k for k in serie.keys() if k != "timestamps"}
        if key not in lower:
            raise RuntimeError(
                f"Radar L7 mitigation product {product!r} missing; have "
                f"{sorted(lower)}"
            )
        key = lower[key]
    vals = serie.get(key) or []
    pts: list[dict[str, Any]] = []
    for ts, val in zip(timestamps, vals):
        try:
            v = float(val)
        except (TypeError, ValueError):
            continue
        pts.append({"date": _ts_to_date(ts), "value": v})
    return _drop_incomplete_week(pts)
