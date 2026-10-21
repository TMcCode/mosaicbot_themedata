"""OpenRouter Data API — public model rankings by token volume.

Docs: https://openrouter.ai/docs/cookbook/administration/data-api
Endpoint: GET /api/v1/datasets/rankings-daily (CC BY 4.0 with attribution).
Requires any valid OPENROUTER_API_KEY (same key as inference).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Any

UA = "mosaicbot_themedata/0.1 (+openrouter_rankings)"
API_URL = "https://openrouter.ai/api/v1/datasets/rankings-daily"
DATASET_FLOOR = date(2025, 1, 1)
MAX_SPAN_DAYS = 366
# Chart-friendly scale (OpenRouter UI quotes "T tokens").
TRILLION = 1e12


def api_key() -> str | None:
    key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    return key or None


def _http_get_json(url: str, *, key: str) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {key}",
            "User-Agent": UA,
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _clamp_start(d: date) -> date:
    return max(d, DATASET_FLOOR)


def _week_monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def fetch_rankings_daily(
    *,
    start: date | None = None,
    end: date | None = None,
    key: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch daily top-50 + other rows; chunk if window > 366 days.

    Returns (rows, meta) where meta includes as_of / citation fields from the
    last successful chunk.
    """
    api_key_val = key if key is not None else api_key()
    if not api_key_val:
        raise RuntimeError("OPENROUTER_API_KEY not set")

    end_d = end or (datetime.now(timezone.utc).date() - timedelta(days=1))
    start_d = _clamp_start(start or DATASET_FLOOR)
    if end_d < DATASET_FLOOR:
        raise RuntimeError(f"end_date {end_d} before dataset floor {DATASET_FLOOR}")
    if start_d > end_d:
        raise RuntimeError(f"start_date {start_d} after end_date {end_d}")

    all_rows: list[dict[str, Any]] = []
    last_meta: dict[str, Any] = {}
    cursor = start_d
    while cursor <= end_d:
        chunk_end = min(cursor + timedelta(days=MAX_SPAN_DAYS - 1), end_d)
        params = urllib.parse.urlencode(
            {
                "start_date": cursor.isoformat(),
                "end_date": chunk_end.isoformat(),
            }
        )
        url = f"{API_URL}?{params}"
        try:
            payload = _http_get_json(url, key=api_key_val)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:400]
            raise RuntimeError(f"OpenRouter rankings-daily HTTP {e.code}: {body}") from e
        rows = payload.get("data") or []
        if not isinstance(rows, list):
            raise RuntimeError("OpenRouter rankings-daily: data not a list")
        all_rows.extend(r for r in rows if isinstance(r, dict))
        meta = payload.get("meta")
        if isinstance(meta, dict):
            last_meta = meta
        cursor = chunk_end + timedelta(days=1)

    return all_rows, last_meta


def _author(permaslug: str) -> str:
    s = (permaslug or "").strip()
    if not s or s == "other":
        return "other"
    return s.split("/", 1)[0].lower()


def _parse_day(s: str) -> date | None:
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def weekly_author_buckets(
    rows: list[dict[str, Any]],
    *,
    exclude_incomplete_week: bool = True,
) -> dict[str, list[dict[str, float]]]:
    """Aggregate daily rankings into ISO-week (Monday) trillion-token series.

    Keys: total, openai, anthropic, other (non-openai/non-anthropic, incl. long tail).
    """
    # week -> author -> tokens
    by_week: dict[date, dict[str, float]] = {}
    for row in rows:
        day = _parse_day(str(row.get("date") or ""))
        if day is None:
            continue
        week = _week_monday(day)
        slug = str(row.get("model_permaslug") or "")
        try:
            tokens = float(row.get("total_tokens") or 0)
        except (TypeError, ValueError):
            continue
        if tokens <= 0:
            continue
        bucket = by_week.setdefault(week, {})
        author = _author(slug)
        bucket[author] = bucket.get(author, 0.0) + tokens
        bucket["__total__"] = bucket.get("__total__", 0.0) + tokens

    today = datetime.now(timezone.utc).date()
    this_week = _week_monday(today)

    out: dict[str, list[dict[str, float]]] = {
        "total": [],
        "openai": [],
        "anthropic": [],
        "other": [],
    }
    for week in sorted(by_week):
        if exclude_incomplete_week and week >= this_week:
            continue
        parts = by_week[week]
        total = float(parts.get("__total__", 0.0))
        openai = float(parts.get("openai", 0.0))
        anthropic = float(parts.get("anthropic", 0.0))
        other = max(0.0, total - openai - anthropic)
        d = week.isoformat()
        out["total"].append({"date": d, "value": round(total / TRILLION, 6)})
        out["openai"].append({"date": d, "value": round(openai / TRILLION, 6)})
        out["anthropic"].append({"date": d, "value": round(anthropic / TRILLION, 6)})
        out["other"].append({"date": d, "value": round(other / TRILLION, 6)})
    return out


def citation(meta: dict[str, Any] | None) -> str:
    as_of = (meta or {}).get("as_of") or datetime.now(timezone.utc).isoformat()
    return (
        f"Source: OpenRouter (openrouter.ai/rankings), as of {as_of}. "
        "Licensed under CC BY 4.0."
    )
