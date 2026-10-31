"""Stack Exchange / Stack Overflow — weekly question counts by tag.

Public API 2.3. Optional ``STACK_EXCHANGE_KEY`` (or ``STACKOVERFLOW_KEY``)
raises the daily quota. Without a key, prefer short windows + local history
merge (caller) so we stay under ~300 req/day.

Strategy: paginate ``/questions`` for ``tagged`` + ``fromdate`` and bucket by
ISO week Monday. Sparse tags (post-ChatGPT SO decline) stay cheap.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Any

UA = "mosaicbot_themedata/0.1 (+lib.stackexchange; Stack Exchange API research use)"
API = "https://api.stackexchange.com/2.3"


def _key() -> str:
    return (
        os.environ.get("STACK_EXCHANGE_KEY")
        or os.environ.get("STACKOVERFLOW_KEY")
        or ""
    ).strip()


def _week_monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _http_get_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        # SO often gzip-encodes; urllib may or may not decode.
        raw = resp.read()
        if raw[:2] == b"\x1f\x8b":
            import gzip

            raw = gzip.decompress(raw)
        return json.loads(raw.decode("utf-8"))


def weekly_question_counts(
    tag: str,
    *,
    start: str | None = None,
    site: str = "stackoverflow",
    max_pages: int = 25,
) -> list[dict[str, Any]]:
    """Return ``[{date, value}, ...]`` weekly new-question counts for ``tag``.

    Without an app key, Stack Exchange caps pagination at page 25 — we fetch
    newest-first and stop at ``start`` or that cap (whichever first).
    """
    tag = (tag or "").strip()
    if not tag:
        raise ValueError("tag required")

    start_d = date.fromisoformat(start[:10]) if start else date(2024, 1, 1)
    key = _key()
    # Unauthenticated hard cap; with a key allow deeper history.
    page_cap = max_pages if not key else max(max_pages, 100)
    counts: dict[str, int] = {}
    page = 1
    reached_start = False
    while page <= page_cap:
        params: dict[str, str] = {
            "site": site,
            "tagged": tag,
            "sort": "creation",
            "order": "desc",
            "pagesize": "100",
            "page": str(page),
            "filter": "default",
        }
        if key:
            params["key"] = key
        url = f"{API}/questions?{urllib.parse.urlencode(params)}"
        try:
            payload = _http_get_json(url)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:400]
            raise RuntimeError(
                f"Stack Exchange questions tag={tag!r} HTTP {e.code}: {body}"
            ) from e

        backoff = payload.get("backoff")
        if isinstance(backoff, (int, float)) and backoff > 0:
            time.sleep(float(backoff) + 0.1)

        items = payload.get("items") or []
        for it in items:
            if not isinstance(it, dict):
                continue
            ts = it.get("creation_date")
            if not isinstance(ts, (int, float)):
                continue
            d = datetime.fromtimestamp(ts, timezone.utc).date()
            if d < start_d:
                reached_start = True
                continue
            mon = _week_monday(d).isoformat()
            counts[mon] = counts.get(mon, 0) + 1

        if reached_start or not payload.get("has_more") or not items:
            break
        page += 1
        time.sleep(0.05 if key else 0.12)

    if not counts:
        return []

    keys = sorted(counts)
    first = date.fromisoformat(keys[0])
    last = date.fromisoformat(keys[-1])
    today = datetime.now(timezone.utc).date()
    cur_mon = _week_monday(today)
    if last >= cur_mon:
        last = cur_mon - timedelta(days=7)
    if last < first:
        return []

    # Dense weekly (zeros for quiet weeks) within observed span.
    pts: list[dict[str, Any]] = []
    cur = first
    while cur <= last:
        ds = cur.isoformat()
        pts.append({"date": ds, "value": float(counts.get(ds, 0))})
        cur += timedelta(days=7)
    return pts
