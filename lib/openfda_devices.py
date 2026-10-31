"""openFDA device 510(k) / PMA decision counts for genomic Dx keywords.

Monthly bucket of decision dates — soft regulatory-activity pulse for
liquid biopsy / NGS / MRD-class IVD clearances (not a Galleri PMA clock).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from typing import Any

UA = "mosaicbot_themedata/0.1 (+lib.openfda_devices; openFDA research use)"
BASE = "https://api.fda.gov"

# Broad device-name / description tokens; openFDA text search is noisy —
# treat as directional activity, not a product registry.
DEFAULT_QUERY = (
    '(device_name:("liquid biopsy" OR ctDNA OR "circulating tumor" OR '
    'genomic OR sequencing OR "next generation" OR NGS OR "molecular '
    'diagnostic" OR "companion diagnostic") OR '
    'openfda.device_name:("liquid biopsy" OR genomic OR sequencing OR NGS))'
)


def _get(path: str, params: dict[str, str]) -> dict[str, Any]:
    url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _daily_to_monthly(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_m: dict[str, float] = defaultdict(float)
    for row in results:
        t = str(row.get("time") or "")
        if len(t) < 6:
            continue
        # openFDA count keys are YYYYMMDD
        ym = f"{t[:4]}-{t[4:6]}-01"
        by_m[ym] += float(row.get("count") or 0)
    return [{"date": d, "value": by_m[d]} for d in sorted(by_m)]


def monthly_decision_counts(
    *,
    endpoint: str = "510k",
    query: str | None = None,
    start: str = "20180101",
    end: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """``endpoint`` is ``510k`` or ``pma``."""
    ep = endpoint.strip().lower()
    if ep not in ("510k", "pma"):
        raise RuntimeError(f"unsupported openFDA endpoint {endpoint!r}")
    q = (query or DEFAULT_QUERY).strip()
    end_s = end or "20991231"
    search = f"decision_date:[{start} TO {end_s}] AND ({q})"
    # openFDA count pagination: fetch up to ~1000 day-buckets via limit.
    all_rows: list[dict[str, Any]] = []
    skip = 0
    notes: list[str] = []
    while True:
        try:
            payload = _get(
                f"/device/{ep}.json",
                {
                    "search": search,
                    "count": "decision_date",
                    "limit": "1000",
                    "skip": str(skip),
                },
            )
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            if e.code == 404 or "NOT_FOUND" in body:
                notes.append(f"{ep}: no matches")
                break
            raise RuntimeError(f"openFDA HTTP {e.code}: {body[:200]}") from e
        rows = list(payload.get("results") or [])
        if not rows:
            break
        all_rows.extend(rows)
        if len(rows) < 1000:
            break
        skip += len(rows)
        time.sleep(0.25)
        if skip > 20000:
            notes.append(f"{ep}: truncated skip={skip}")
            break
    pts = _daily_to_monthly(all_rows)
    # Drop future months
    from datetime import date as _date

    today = _date.today().isoformat()[:7] + "-01"
    pts = [p for p in pts if p["date"] <= today]
    meta = {
        "source": f"openfda.device.{ep}",
        "query": q,
        "day_buckets": len(all_rows),
        "months": len(pts),
        "notes": notes,
    }
    return pts, meta
