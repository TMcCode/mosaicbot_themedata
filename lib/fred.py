"""FRED observations client — prefer official API + key; CSV fallback.

Packs should call ``fetch_series`` instead of scraping fredgraph.csv ad hoc.
Set ``FRED_API_KEY`` in repo ``.env`` (free at https://fred.stlouisfed.org/docs/api/api_key.html).
"""

from __future__ import annotations

import csv
import io
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from lib.env import load_env

UA = "mosaicbot_themedata/0.1 (+lib.fred)"
_API = "https://api.stlouisfed.org/fred/series/observations"
_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"


def _api_key() -> str | None:
    load_env()
    key = (os.environ.get("FRED_API_KEY") or "").strip()
    return key or None


def _get(url: str, *, timeout: float = 60, retries: int = 4) -> bytes:
    last: Exception | None = None
    for i in range(retries):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
            time.sleep(1.2 * (i + 1))
    assert last is not None
    raise last


def _parse_api(raw: bytes) -> list[tuple[str, float]]:
    data = json.loads(raw.decode("utf-8"))
    if data.get("error_code"):
        raise RuntimeError(
            f"FRED API error {data.get('error_code')}: {data.get('error_message')}"
        )
    rows: list[tuple[str, float]] = []
    for obs in data.get("observations") or []:
        d = obs.get("date")
        v = obs.get("value")
        if not d or v in (None, "", "."):
            continue
        rows.append((str(d)[:10], float(v)))
    return rows


def _parse_csv(raw: bytes, series_id: str) -> list[tuple[str, float]]:
    if raw[:15].lower().startswith(b"<!doctype") or b"<html" in raw[:200].lower():
        raise RuntimeError(f"FRED returned HTML for {series_id} (missing/blocked)")
    rows: list[tuple[str, float]] = []
    for row in csv.DictReader(io.StringIO(raw.decode())):
        d = row.get("observation_date") or row.get("DATE")
        v = row.get(series_id)
        if not d or v in (None, "", "."):
            continue
        rows.append((str(d)[:10], float(v)))
    return rows


def fetch_series(
    series_id: str,
    *,
    observation_start: str | None = None,
    observation_end: str | None = None,
    allow_csv_fallback: bool = False,
) -> list[tuple[str, float]]:
    """Return ``(YYYY-MM-DD, value)`` ascending.

    With ``FRED_API_KEY`` set (normal): **API only** — retries, then raises.
    Without a key: optional keyless ``fredgraph.csv`` only if ``allow_csv_fallback``.
    """
    sid = str(series_id).strip()
    if not sid:
        raise RuntimeError("empty FRED series id")

    key = _api_key()
    if key:
        params: dict[str, Any] = {
            "series_id": sid,
            "api_key": key,
            "file_type": "json",
            "sort_order": "asc",
        }
        if observation_start:
            params["observation_start"] = observation_start
        if observation_end:
            params["observation_end"] = observation_end
        url = f"{_API}?{urllib.parse.urlencode(params)}"
        return _parse_api(_get(url))

    if not allow_csv_fallback:
        raise RuntimeError(
            "FRED_API_KEY missing — add it to .env "
            "(https://fred.stlouisfed.org/docs/api/api_key.html)"
        )

    qs = urllib.parse.urlencode({"id": sid})
    if observation_start:
        qs += f"&cosd={observation_start}"
    if observation_end:
        qs += f"&coed={observation_end}"
    return _parse_csv(_get(f"{_CSV}?{qs}"), sid)
