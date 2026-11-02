"""CDC National Immunization Survey — Fall Respiratory Virus Module (weekly).

Primary dataset: ``5c6r-xi2t`` on data.cdc.gov (NIS-ACM / RespVaxView weekly
adult coverage for FLU / COVID / RSV). Short season history by design —
append on each pack run.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

UA = "mosaicbot_themedata/0.1 (+lib.cdc_nis_frvm; CDC open data research use)"
# Weekly adult coverage + intent (2025–26 season onward on this id).
DEFAULT_DATASET = "5c6r-xi2t"
API = "https://data.cdc.gov/resource/{dataset}.json"


def _get(
    dataset: str,
    params: dict[str, Any],
    *,
    retries: int = 4,
) -> list[dict[str, Any]]:
    url = API.format(dataset=dataset) + "?" + urllib.parse.urlencode(params)
    last: Exception | None = None
    for i in range(retries):
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            if isinstance(body, list):
                return body
            raise RuntimeError(f"CDC unexpected payload type {type(body)}")
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            last = e
            time.sleep(1.2 * (i + 1))
    assert last is not None
    raise last


def weekly_coverage(
    *,
    vaccine: str,
    demographic_name: str = "18+ years",
    indicator_label: str = "Up-to-date",
    indicator_category_label: str = "Yes",
    geographic_level: str = "National",
    dataset: str = DEFAULT_DATASET,
    limit: int = 5000,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """National weekly coverage % (``nd_weekly_estimate``).

    RSV uses ``demographic_name='50+ years'`` (ACIP adult RSV window).
    """
    vax = str(vaccine or "").strip().upper()
    where = (
        f"geographic_level='{geographic_level}' AND "
        f"demographic_name='{demographic_name}' AND "
        f"vaccine='{vax}' AND "
        f"indicator_label='{indicator_label}' AND "
        f"indicator_category_label='{indicator_category_label}' AND "
        f"suppression_flag='0'"
    )
    rows = _get(
        dataset,
        {
            "$where": where,
            "$order": "week_ending ASC",
            "$limit": str(int(limit)),
        },
    )
    pts: list[dict[str, Any]] = []
    for r in rows:
        day = str(r.get("week_ending") or "")[:10]
        raw = r.get("nd_weekly_estimate")
        if not day or raw in (None, ""):
            continue
        try:
            val = float(raw)
        except (TypeError, ValueError):
            continue
        pts.append({"date": day, "value": round(val, 4)})
    meta = {
        "source": f"data.cdc.gov/{dataset}",
        "vaccine": vax,
        "demographic_name": demographic_name,
        "indicator_label": indicator_label,
        "indicator_category_label": indicator_category_label,
        "n_points": len(pts),
    }
    return pts, meta


def weekly_flu_refusal(
    *,
    dataset: str = DEFAULT_DATASET,
    demographic_name: str = "18+ years",
    limit: int = 5000,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Share of adults who probably/definitely will not get a flu vaccine."""
    return weekly_coverage(
        vaccine="FLU",
        demographic_name=demographic_name,
        indicator_label="4-level vaccination and intent",
        indicator_category_label="Probably or definitely will not get a vaccine",
        dataset=dataset,
        limit=limit,
    )
