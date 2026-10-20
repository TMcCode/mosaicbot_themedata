"""BLS Public Data API v2 client with retries for transient busyness.

BLS often returns HTTP 503 HTML ("The BLS website is busy") under load.
Packs should call ``fetch_series`` instead of posting to api.bls.gov ad hoc.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

UA = "mosaicbot_themedata/0.1 (+lib.bls)"
BLS_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

# Transient: site busy / gateway / rate limit. Do not retry 400/401/403.
_RETRY_HTTP = frozenset({429, 500, 502, 503, 504})


def fetch_series(
    series_ids: list[str],
    *,
    start_year: int,
    end_year: int,
    api_key: str,
    timeout: float = 120,
    retries: int = 5,
    user_agent: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """POST BLS v2; return ``{series_id: [{year, period, value, ...}, ...]}``.

    Retries on HTTP 429/5xx and network errors with exponential backoff.
    """
    if not series_ids:
        return {}
    payload = {
        "seriesid": series_ids,
        "startyear": str(start_year),
        "endyear": str(end_year),
        "registrationkey": api_key,
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": user_agent or UA,
    }

    last: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(
            BLS_URL, data=body, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:500]
            last = RuntimeError(f"BLS HTTP {e.code}: {detail}")
            if e.code in _RETRY_HTTP and attempt + 1 < retries:
                time.sleep(2.0 * (2**attempt))  # 2s, 4s, 8s, 16s…
                continue
            raise last from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
            if attempt + 1 < retries:
                time.sleep(2.0 * (2**attempt))
                continue
            raise RuntimeError(f"BLS network error: {e}") from e

        status = raw.get("status")
        if status != "REQUEST_SUCCEEDED":
            msg = raw.get("message")
            # BLS sometimes returns 200 with a failure status under load.
            busy = status in {
                "REQUEST_NOT_PROCESSED",
                "REQUEST_FAILED",
            } or (
                isinstance(msg, list)
                and any("busy" in str(m).lower() for m in msg)
            )
            last = RuntimeError(f"BLS status={status!r} message={msg!r}")
            if busy and attempt + 1 < retries:
                time.sleep(2.0 * (2**attempt))
                continue
            raise last

        out: dict[str, list[dict[str, Any]]] = {}
        for series in raw.get("Results", {}).get("series", []) or []:
            sid = series.get("seriesID")
            if not sid:
                continue
            rows: list[dict[str, Any]] = []
            for row in series.get("data", []) or []:
                rows.append(
                    {
                        "year": row.get("year"),
                        "period": row.get("period"),
                        "periodName": row.get("periodName"),
                        "value": row.get("value"),
                        "footnotes": row.get("footnotes") or [],
                    }
                )
            out[str(sid)] = rows
            msgs = series.get("message") or []
            if msgs and not rows:
                # Keep parity with prior pack helpers: empty series still keyed.
                pass
        return out

    assert last is not None
    raise last
