"""Trading Economics REST client — budget-aware (500 req/month plans).

Design rules for a tight monthly allowance:

1. **Local + R2 history is source of truth.** Never re-pull full history on a
   schedule; backfill once, then append.
2. **Batch symbols** in one ``markets/symbol`` / ``markets/historical`` call.
3. **Disk-cache** every response under ``data/te/cache/``; reuse within TTL.
4. **Ledger** every HTTP hit in ``data/te/usage.json`` (calendar month).
5. **Throttle** ≥0.55s between calls (TE hard-caps ~2 req/s).
6. Prefer **snapshot** for weekly refresh (1 call for N symbols) over historical.
7. Keep HTML scrape as a **zero-API fallback** for public commodity pages.

Env: ``TE_API_KEY`` (``key:secret``), optional ``TE_MONTHLY_LIMIT`` (default 500),
optional ``TE_MONTHLY_RESERVE`` (default 40 — refuse routine pulls below this).
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from lib.env import load_env, require_env

API = "https://api.tradingeconomics.com"
UA = "mosaicbot_themedata/0.1 (+tradingeconomics)"
_ROOT = Path(__file__).resolve().parents[1]
_TE_DIR = _ROOT / "data" / "te"
_USAGE_PATH = _TE_DIR / "usage.json"
_CACHE_DIR = _TE_DIR / "cache"
_SYMBOLOGY_PATH = _ROOT / "config" / "te_symbols.json"

_MIN_INTERVAL_S = 0.55
_last_call_mono = 0.0


class TeBudgetError(RuntimeError):
    """Raised when the monthly request budget would be exceeded."""


def api_key() -> str:
    load_env()
    return require_env("TE_API_KEY")["TE_API_KEY"].strip()


def monthly_limit() -> int:
    load_env()
    import os

    raw = (os.environ.get("TE_MONTHLY_LIMIT") or "500").strip()
    return max(1, int(raw))


def monthly_reserve() -> int:
    load_env()
    import os

    raw = (os.environ.get("TE_MONTHLY_RESERVE") or "40").strip()
    return max(0, int(raw))


def _month_key(d: date | None = None) -> str:
    d = d or date.today()
    return f"{d.year:04d}-{d.month:02d}"


def load_usage() -> dict[str, Any]:
    if not _USAGE_PATH.exists():
        return {"schema_version": 0, "months": {}}
    return json.loads(_USAGE_PATH.read_text(encoding="utf-8"))


def save_usage(doc: dict[str, Any]) -> None:
    _USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _USAGE_PATH.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


def usage_stats(*, month: str | None = None) -> dict[str, Any]:
    mk = month or _month_key()
    doc = load_usage()
    bucket = doc.get("months") or {}
    row = bucket.get(mk) or {"count": 0, "calls": []}
    used = int(row.get("count") or 0)
    limit = monthly_limit()
    reserve = monthly_reserve()
    remaining = max(0, limit - used)
    return {
        "month": mk,
        "used": used,
        "limit": limit,
        "reserve": reserve,
        "remaining": remaining,
        "routine_remaining": max(0, remaining - reserve),
    }


def _record_call(path: str, *, ok: bool, status: int | None, bytes_n: int) -> None:
    doc = load_usage()
    months = doc.setdefault("months", {})
    mk = _month_key()
    row = months.setdefault(mk, {"count": 0, "calls": []})
    row["count"] = int(row.get("count") or 0) + 1
    calls = row.setdefault("calls", [])
    calls.append(
        {
            "at": datetime.now(timezone.utc).isoformat(),
            "path": path[:200],
            "ok": ok,
            "status": status,
            "bytes": bytes_n,
        }
    )
    # Keep ledger bounded
    if len(calls) > 600:
        row["calls"] = calls[-600:]
    save_usage(doc)


def _throttle() -> None:
    global _last_call_mono
    now = time.monotonic()
    wait = _MIN_INTERVAL_S - (now - _last_call_mono)
    if wait > 0:
        time.sleep(wait)
    _last_call_mono = time.monotonic()


def _cache_path(cache_key: str) -> Path:
    h = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()[:40]
    return _CACHE_DIR / f"{h}.json"


def _read_cache(cache_key: str, *, max_age_hours: float | None) -> Any | None:
    path = _cache_path(cache_key)
    if not path.exists():
        return None
    try:
        wrap = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if max_age_hours is not None:
        fetched = wrap.get("fetched_at") or ""
        try:
            ts = datetime.fromisoformat(fetched.replace("Z", "+00:00"))
        except Exception:
            return None
        age_h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0
        if age_h > max_age_hours:
            return None
    return wrap.get("data")


def _write_cache(cache_key: str, data: Any) -> None:
    path = _cache_path(cache_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    wrap = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "cache_key": cache_key,
        "data": data,
    }
    path.write_text(json.dumps(wrap), encoding="utf-8")


def get_json(
    path: str,
    *,
    params: dict[str, str] | None = None,
    cache_key: str | None = None,
    max_age_hours: float | None = None,
    spend: bool = True,
    allow_reserve: bool = False,
    force: bool = False,
) -> Any:
    """GET ``/path`` under api.tradingeconomics.com.

    ``path`` e.g. ``markets/symbol/GAC:COM,GECNYBGQ:COM`` (no leading slash).
    Counts against the monthly budget unless ``spend=False`` (cache-only path
    should not call this).
    """
    path = path.lstrip("/")
    if cache_key and not force:
        hit = _read_cache(cache_key, max_age_hours=max_age_hours)
        if hit is not None:
            return hit

    stats = usage_stats()
    if spend and not force:
        floor = 0 if allow_reserve else stats["reserve"]
        if stats["remaining"] <= floor:
            raise TeBudgetError(
                f"TE budget exhausted or in reserve: used={stats['used']}/"
                f"{stats['limit']} remaining={stats['remaining']} "
                f"(reserve={stats['reserve']}). Use cache/scrape or --force."
            )

    key = api_key()
    qs = dict(params or {})
    qs["c"] = key
    qs.setdefault("f", "json")
    query = "&".join(f"{quote(str(k))}={quote(str(v))}" for k, v in qs.items())
    url = f"{API}/{path}?{query}"

    _throttle()
    req = Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    status: int | None = None
    body = b""
    try:
        with urlopen(req, timeout=90) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            body = resp.read()
        if spend:
            _record_call(path, ok=True, status=status, bytes_n=len(body))
        data = json.loads(body.decode("utf-8"))
    except HTTPError as e:
        err_body = e.read() if hasattr(e, "read") else b""
        if spend:
            _record_call(path, ok=False, status=e.code, bytes_n=len(err_body))
        raise RuntimeError(
            f"TE HTTP {e.code} for {path}: {err_body.decode('utf-8', errors='replace')[:300]}"
        ) from e

    if cache_key:
        _write_cache(cache_key, data)
    return data


def _norm_symbols(symbols: str | list[str]) -> str:
    if isinstance(symbols, str):
        parts = [s.strip() for s in symbols.split(",") if s.strip()]
    else:
        parts = [str(s).strip() for s in symbols if str(s).strip()]
    # TE paths are case-insensitive for commodities; keep stable order, dedupe
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        k = p.upper()
        if k in seen:
            continue
        seen.add(k)
        out.append(p)
    return ",".join(out)


def markets_snapshot(
    symbols: str | list[str],
    *,
    max_age_hours: float | None = 12.0,
    force: bool = False,
    allow_reserve: bool = False,
) -> list[dict[str, Any]]:
    """Latest quote(s). One request for many symbols — prefer this for refresh."""
    sym = _norm_symbols(symbols)
    if not sym:
        return []
    cache_key = f"markets/symbol|{sym}"
    data = get_json(
        f"markets/symbol/{sym}",
        cache_key=cache_key,
        max_age_hours=max_age_hours,
        force=force,
        allow_reserve=allow_reserve,
    )
    if not isinstance(data, list):
        raise RuntimeError(f"unexpected snapshot payload: {type(data)}")
    return data


def markets_historical(
    symbols: str | list[str],
    *,
    d1: str | None = None,
    d2: str | None = None,
    max_age_hours: float | None = 24.0 * 30,
    force: bool = False,
    allow_reserve: bool = True,
) -> list[dict[str, Any]]:
    """OHLC history. Expensive — call rarely; cache aggressively; batch symbols."""
    sym = _norm_symbols(symbols)
    if not sym:
        return []
    params: dict[str, str] = {}
    if d1:
        params["d1"] = d1
    if d2:
        params["d2"] = d2
    cache_key = f"markets/historical|{sym}|{d1 or ''}|{d2 or ''}"
    data = get_json(
        f"markets/historical/{sym}",
        params=params or None,
        cache_key=cache_key,
        max_age_hours=max_age_hours,
        force=force,
        allow_reserve=allow_reserve,
    )
    if not isinstance(data, list):
        raise RuntimeError(f"unexpected historical payload: {type(data)}")
    return data


def parse_te_date(raw: str) -> str:
    """TE dates are often ``DD/MM/YYYY`` or ISO — return ``YYYY-MM-DD``."""
    s = (raw or "").strip()
    if not s:
        raise ValueError("empty TE date")
    if "T" in s:
        s = s.split("T", 1)[0]
    if "-" in s and len(s) >= 10 and s[4] == "-":
        return s[:10]
    # DD/MM/YYYY
    parts = s.replace("-", "/").split("/")
    if len(parts) == 3:
        a, b, c = parts
        if len(c) == 4:
            day, month, year = int(a), int(b), int(c)
            return f"{year:04d}-{month:02d}-{day:02d}"
        if len(a) == 4:
            year, month, day = int(a), int(b), int(c)
            return f"{year:04d}-{month:02d}-{day:02d}"
    raise ValueError(f"unparseable TE date: {raw!r}")


def load_symbology() -> dict[str, Any]:
    if not _SYMBOLOGY_PATH.exists():
        return {"schema_version": 0, "symbols": {}}
    return json.loads(_SYMBOLOGY_PATH.read_text(encoding="utf-8"))


def symbol_for_series(series_key: str) -> str | None:
    doc = load_symbology()
    row = (doc.get("symbols") or {}).get(series_key)
    if isinstance(row, dict):
        return row.get("symbol")
    if isinstance(row, str):
        return row
    return None


def historical_to_points(
    rows: list[dict[str, Any]],
    *,
    symbol: str | None = None,
) -> list[dict[str, Any]]:
    """Map TE historical OHLC rows → ``[{date, value}]`` using Close."""
    want = (symbol or "").upper()
    pts: list[dict[str, Any]] = []
    for r in rows:
        if want and str(r.get("Symbol") or "").upper() != want:
            continue
        close = r.get("Close")
        if close is None:
            continue
        try:
            d = parse_te_date(str(r.get("Date") or ""))
            pts.append({"date": d, "value": float(close)})
        except Exception:
            continue
    pts.sort(key=lambda p: p["date"])
    # Dedupe by date (keep last)
    by: dict[str, float] = {}
    for p in pts:
        by[p["date"]] = p["value"]
    return [{"date": d, "value": by[d]} for d in sorted(by)]


def snapshot_to_spot(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize a markets/symbol row into our germanium-style spot snapshot."""
    last = row.get("Last")
    if last is None:
        last = row.get("Close")
    raw_date = row.get("CloseDate") or row.get("Date") or row.get("LastUpdate")
    as_of = parse_te_date(str(raw_date)) if raw_date else date.today().isoformat()
    return {
        "schema_version": 0,
        "source": f"te_api:{row.get('Symbol')}",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "as_of": as_of,
        "price_cny_kg": float(last) if last is not None else None,
        "unit": str(row.get("unit") or row.get("Unit") or "cny_per_kg").lower().replace("/", "_per_"),
        "symbol": str(row.get("Ticker") or row.get("Symbol") or ""),
        "te_symbol": str(row.get("Symbol") or ""),
        "full_name": str(row.get("Name") or ""),
        "notes": "Trading Economics API CFD/proxy quote; not Argus/SMM physical.",
        "raw": {
            "Last": row.get("Last"),
            "Close": row.get("Close"),
            "WeeklyChange": row.get("WeeklyChange"),
            "YearlyChange": row.get("YearlyChange"),
        },
    }
