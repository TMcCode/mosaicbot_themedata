"""OpenTable State of the Industry — US seated diners YoY.

Public JSON: https://www.opentable.com/c/wapi/r9-ot-soti/v1/data
Cite OpenTable and link back to https://www.opentable.com/c/state-of-industry/

Series: United States ``rolling7DayAverageYoY`` (online reservations only),
downsampled to one point per week (Sunday / last obs in ISO week).

Akamai sometimes blocks non-browser clients — soft-fail and keep local history.
"""

from __future__ import annotations

import json
import urllib.error
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

API_URL = "https://www.opentable.com/c/wapi/r9-ot-soti/v1/data"
PAGE_URL = "https://www.opentable.com/c/state-of-industry/"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
HISTORY_NAME = "opentable_us_seated_history.jsonl"
US_ID = "840"


def fetch_payload() -> dict[str, Any]:
    req = Request(
        API_URL,
        headers={
            "User-Agent": UA,
            "Accept": "application/json,text/plain,*/*",
            "Referer": PAGE_URL,
            "Origin": "https://www.opentable.com",
        },
    )
    try:
        with urlopen(req, timeout=90) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"OpenTable SOTI HTTP {e.code}: {detail}") from e


def us_rolling7_points(payload: dict[str, Any]) -> list[dict[str, float]]:
    sd = payload.get("seatedDiners") or {}
    headers = sd.get("dailyHeaders") or []
    countries = sd.get("countries") or []
    us = next((c for c in countries if str(c.get("id")) == US_ID), None)
    if not us:
        raise RuntimeError("United States (840) missing from OpenTable SOTI payload")
    vals = us.get("rolling7DayAverageYoY") or []
    pts: list[dict[str, float]] = []
    for i, d in enumerate(headers):
        if i >= len(vals) or vals[i] is None:
            continue
        try:
            v = float(vals[i])
        except (TypeError, ValueError):
            continue
        pts.append({"date": str(d)[:10], "value": round(v, 2)})
    return pts


def weekly_last(points: list[dict[str, float]]) -> list[dict[str, float]]:
    by_week: dict[str, dict[str, float]] = {}
    for p in points:
        dt = date.fromisoformat(str(p["date"])[:10])
        iso = dt.isocalendar()
        key = f"{iso.year:04d}-W{iso.week:02d}"
        by_week[key] = p
    return sorted(by_week.values(), key=lambda p: p["date"])


def load_history(path: Path) -> dict[str, dict[str, Any]]:
    by: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return by
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        d = row.get("date")
        if d:
            by[str(d)] = row
    return by


def write_history(path: Path, by: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(by.values(), key=lambda r: r.get("date") or "")
    path.write_text(
        "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows),
        encoding="utf-8",
    )


def scrape_and_upsert(
    out_dir: Path,
    *,
    fetched_at: str | None = None,
) -> tuple[list[dict[str, float]], str]:
    """Pull SOTI, upsert weekly US YoY history. Returns (weekly_points, note)."""
    fetched_at = fetched_at or datetime.now(timezone.utc).isoformat()
    hist_path = out_dir / HISTORY_NAME
    by = load_history(hist_path)
    payload = fetch_payload()
    daily = us_rolling7_points(payload)
    weekly = weekly_last(daily)
    for p in weekly:
        by[p["date"]] = {
            "date": p["date"],
            "value": p["value"],
            "source": API_URL,
            "cite": PAGE_URL,
            "fetched_at": fetched_at,
            "metric": "rolling7DayAverageYoY",
            "geo": "United States",
        }
    write_history(hist_path, by)
    pts = [
        {"date": d, "value": float(row["value"])}
        for d, row in by.items()
        if row.get("value") is not None
    ]
    pts.sort(key=lambda p: p["date"])
    note = f"OpenTable US seated YoY weekly ({len(pts)} pts, {len(daily)} daily raw)"
    return pts, note


def points_from_history(out_dir: Path) -> list[dict[str, float]]:
    by = load_history(out_dir / HISTORY_NAME)
    pts = [
        {"date": d, "value": float(row["value"])}
        for d, row in by.items()
        if row.get("value") is not None
    ]
    pts.sort(key=lambda p: p["date"])
    return pts
