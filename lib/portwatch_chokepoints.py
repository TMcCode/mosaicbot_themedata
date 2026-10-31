"""IMF PortWatch daily chokepoint tanker transit counts (ArcGIS FeatureServer).

Free public table ``Daily_Chokepoints_Data``. Field ``n_tanker`` = AIS-derived
tanker transit calls. History from ~2019; updates weekly (Tuesdays).

PortWatch ids used here:
  chokepoint4 — Bab el-Mandeb Strait
  chokepoint6 — Strait of Hormuz
  chokepoint7 — Cape of Good Hope

ArcGIS quirk: some ``resultOffset`` values 400 (including 0 for some portids).
We walk offsets ``None, 1000, 2000, …`` and skip failing pages.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

UA = "mosaicbot_themedata/0.1 (+portwatch_chokepoints; +https://stockcontext.info)"
_QUERY = (
    "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/"
    "Daily_Chokepoints_Data/FeatureServer/0/query"
)

CHOKEPOINTS: dict[str, dict[str, str]] = {
    "bab": {"portid": "chokepoint4", "name": "Bab el-Mandeb Strait"},
    "hormuz": {"portid": "chokepoint6", "name": "Strait of Hormuz"},
    "cape": {"portid": "chokepoint7", "name": "Cape of Good Hope"},
}


def _http_json(url: str) -> Any:
    req = Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _query_port(portid: str, *, page_size: int = 1000) -> list[dict[str, Any]]:
    """Fetch daily rows for one portid, skipping flaky ArcGIS offsets."""
    out: list[dict[str, Any]] = []
    pages_ok = 0
    consecutive_fail = 0
    # None = omit resultOffset (page 0). Then 1000, 2000, …
    offsets: list[int | None] = [None] + list(range(page_size, page_size * 8, page_size))
    for offset in offsets:
        params: dict[str, Any] = {
            "where": f"portid='{portid}'",
            "outFields": "date,n_tanker,ObjectId,portname",
            "returnGeometry": "false",
            "resultRecordCount": int(page_size),
            "f": "json",
        }
        if offset is not None:
            params["resultOffset"] = int(offset)

        feats: list[Any] = []
        err: Any = None
        for attempt in range(3):
            try:
                payload = _http_json(f"{_QUERY}?{urlencode(params)}")
            except Exception as e:
                err = e
                time.sleep(0.8 * (attempt + 1))
                continue
            if payload.get("error"):
                err = payload.get("error")
                time.sleep(0.8 * (attempt + 1))
                continue
            feats = payload.get("features") or []
            err = None
            break

        if err is not None:
            consecutive_fail += 1
            if consecutive_fail >= 3 and pages_ok:
                break
            continue

        consecutive_fail = 0
        if not feats:
            # Empty success — stop if we already have data past first page.
            if pages_ok:
                break
            continue

        pages_ok += 1
        for feat in feats:
            a = feat.get("attributes") or {}
            d = str(a.get("date") or "")[:10]
            v = a.get("n_tanker")
            if len(d) < 10 or v is None:
                continue
            try:
                out.append({"date": d, "value": float(v)})
            except (TypeError, ValueError):
                continue
        if len(feats) < int(page_size) and offset is not None:
            break
    return out


def fetch_daily_tanker_transits(
    chokepoint: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Daily ``n_tanker`` points for one named chokepoint (``cape``/``bab``/``hormuz``)."""
    key = str(chokepoint or "").strip().lower()
    spec = CHOKEPOINTS.get(key)
    if not spec:
        raise RuntimeError(f"unknown PortWatch chokepoint {chokepoint!r}")
    portid = spec["portid"]

    rows = _query_port(portid)
    by_date: dict[str, float] = {}
    for p in rows:
        by_date[p["date"]] = float(p["value"])
    out = [{"date": d, "value": by_date[d]} for d in sorted(by_date)]
    if not out:
        raise RuntimeError(f"PortWatch {key}: no n_tanker rows for {portid}")

    meta = {
        "source": "imf_portwatch",
        "chokepoint": key,
        "portid": portid,
        "portname": spec["name"],
        "url": _QUERY,
        "as_of": out[-1]["date"],
        "n_days": len(out),
        "raw_rows": len(rows),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    return out, meta


def to_weekly_sum(daily: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sum daily tanker counts into ISO weeks (week-ending Sunday as date)."""
    buckets: dict[str, float] = defaultdict(float)
    for p in daily:
        d = date.fromisoformat(p["date"])
        end = d + timedelta(days=(6 - d.weekday()) % 7)
        if d.weekday() == 6:
            end = d
        buckets[end.isoformat()] += float(p["value"])
    return [
        {"date": d, "value": round(v, 2)}
        for d, v in sorted(buckets.items())
    ]


def to_monthly_sum(daily: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, float] = defaultdict(float)
    for p in daily:
        key = f"{p['date'][:7]}-01"
        buckets[key] += float(p["value"])
    return [
        {"date": d, "value": round(v, 2)}
        for d, v in sorted(buckets.items())
    ]
