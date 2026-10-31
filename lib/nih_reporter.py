"""NIH RePORTER v2 — monthly award amounts for genomics Dx keywords.

Pages project search results and buckets ``award_notice_date`` into months.
Soft federal research-funding pulse adjacent to (but distinct from) NHGRI
USASpending grant series on sequencing_platforms.
"""

from __future__ import annotations

import json
import time
import urllib.request
from collections import defaultdict
from datetime import date
from typing import Any

UA = "mosaicbot_themedata/0.1 (+lib.nih_reporter; NIH RePORTER research use)"
API = "https://api.reporter.nih.gov/v2/projects/search"

DEFAULT_TEXT = (
    'liquid biopsy OR ctDNA OR "circulating tumor DNA" OR '
    '"minimal residual disease" OR MRD OR "multi-cancer early detection" '
    'OR MCED OR "companion diagnostic" OR "molecular residual"'
)


def _post(body: dict[str, Any]) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        API,
        data=data,
        headers={
            "User-Agent": UA,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read().decode("utf-8"))


def monthly_award_amounts(
    *,
    search_text: str | None = None,
    fiscal_years: list[int] | None = None,
    page_size: int = 500,
    sleep_s: float = 0.2,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Page NIH RePORTER by fiscal year.

    The v2 API rejects offsets above 14,999, so multi-year queries that
    return ~15k+ hits must be split. Per-FY liquid-biopsy hits stay well
    under that cap.
    """
    text = (search_text or DEFAULT_TEXT).strip()
    today = date.today()
    years = fiscal_years or list(range(2018, today.year + 1))
    by_m: dict[str, float] = defaultdict(float)
    n_rows = 0
    total_reported = 0
    for fy in years:
        offset = 0
        fy_total = None
        while True:
            body = {
                "criteria": {
                    "fiscal_years": [fy],
                    "advanced_text_search": {
                        "operator": "advanced",
                        "search_field": "projecttitle,abstracttext",
                        "search_text": text,
                    },
                },
                "offset": offset,
                "limit": page_size,
                "include_fields": [
                    "ProjectNum",
                    "AwardAmount",
                    "FiscalYear",
                    "AwardNoticeDate",
                ],
            }
            payload = _post(body)
            if fy_total is None:
                fy_total = int((payload.get("meta") or {}).get("total") or 0)
                total_reported += fy_total
            rows = list(payload.get("results") or [])
            if not rows:
                break
            for row in rows:
                n_rows += 1
                amt = row.get("award_amount")
                if amt is None:
                    continue
                notice = str(row.get("award_notice_date") or "")[:10]
                if len(notice) >= 7:
                    key = f"{notice[:7]}-01"
                else:
                    row_fy = row.get("fiscal_year") or fy
                    # FY starts Oct prior calendar year — put on Oct-01 of FY-1.
                    key = f"{int(row_fy) - 1}-10-01"
                by_m[key] += float(amt)
            offset += len(rows)
            if offset >= fy_total or len(rows) < page_size:
                break
            # Hard stop before API max offset (14,999).
            if offset >= 14500:
                break
            time.sleep(sleep_s)
        time.sleep(sleep_s)
    # Scale to millions for charting
    pts = [
        {"date": d, "value": round(by_m[d] / 1e6, 4)}
        for d in sorted(by_m)
        if d <= today.isoformat()[:7] + "-01"
    ]
    meta = {
        "source": "api.reporter.nih.gov/v2",
        "search_text": text,
        "fiscal_years": years,
        "projects": n_rows,
        "total_reported": total_reported,
        "unit": "usd_millions",
    }
    return pts, meta
