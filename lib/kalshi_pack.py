"""Pack-job helpers for Kalshi: auto-roll, chart reset, drop if no successor.

Catalog fields:
  - ``kalshi_series`` (required)
  - ``kalshi_ticker`` preferred pin (updated on roll)
  - ``kalshi_strike`` optional ``T5.8``-style (normalized into match)
  - ``kalshi_match`` optional ticker substring that must survive year rollover
  - ``kalshi_auto_roll`` default True

If no open successor matches, raises so the pack omits the series (page drop).
On roll: archives prior contract offline and resets live history to the new ticker.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from lib import kalshi as kalshilib
from lib import r2 as r2lib

_DATE_PART = re.compile(r"^\d{2}[A-Z]{3}\d{0,2}$")  # 26NOV / 26NOV02 / 27FEB28
_YEAR = re.compile(r"^20\d{2}$")
_YY = re.compile(r"^\d{2}$")
_DATE_PREFIX = re.compile(r"^\d{2}[A-Z]{3}")  # 26NOV glued onto ECVOL / CUST…


def infer_roll_match(series_ticker: str, market_ticker: str) -> str | None:
    """Derive a year-stable ticker fragment for matching the next contract."""
    series = str(series_ticker or "").strip().upper()
    ticker = str(market_ticker or "").strip().upper()
    if not ticker:
        return None
    rest = ticker
    if series and ticker.startswith(series):
        rest = ticker[len(series) :].lstrip("-")
    kept: list[str] = []
    for part in rest.split("-"):
        if not part:
            continue
        if _DATE_PART.fullmatch(part) or _YEAR.fullmatch(part) or _YY.fullmatch(part):
            continue
        stripped = _DATE_PREFIX.sub("", part)
        if stripped:
            kept.append(stripped)
    return "-".join(kept) if kept else None


def resolve_match(meta: dict[str, Any]) -> str | None:
    """Explicit strike/match wins; else infer from preferred ticker."""
    strike = meta.get("kalshi_strike")
    if strike not in (None, ""):
        return kalshilib._norm_strike(strike)
    explicit = str(meta.get("kalshi_match") or "").strip().upper()
    if explicit:
        return explicit
    series = str(meta.get("kalshi_series") or "").strip()
    ticker = str(meta.get("kalshi_ticker") or "").strip()
    return infer_roll_match(series, ticker)


def _history_path(out_dir: Path, series_key: str) -> Path:
    return out_dir / f"{series_key}_kalshi_history.jsonl"


def _meta_path(out_dir: Path, series_key: str) -> Path:
    return out_dir / f"{series_key}_kalshi_meta.json"


def load_history(out_dir: Path, series_key: str, r2_prefix: str) -> list[dict[str, Any]]:
    path = _history_path(out_dir, series_key)
    rows: list[dict[str, Any]] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
        return rows
    try:
        raw = r2lib.get_bytes(f"{r2_prefix}/{series_key}_kalshi_history.jsonl")
        if raw:
            for line in raw.decode("utf-8").splitlines():
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    except Exception:
        pass
    return rows


def write_history(
    out_dir: Path,
    series_key: str,
    r2_prefix: str,
    points: list[dict[str, Any]],
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = _history_path(out_dir, series_key)
    path.write_text(
        "".join(
            json.dumps({"date": p["date"], "value": p["value"]}, separators=(",", ":"))
            + "\n"
            for p in points
        ),
        encoding="utf-8",
    )
    try:
        r2lib.put_bytes(
            f"{r2_prefix}/{series_key}_kalshi_history.jsonl",
            path.read_bytes(),
            content_type="application/x-ndjson",
        )
    except Exception:
        pass


def load_meta(out_dir: Path, series_key: str, r2_prefix: str) -> dict[str, Any]:
    path = _meta_path(out_dir, series_key)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    try:
        remote = r2lib.get_json(f"{r2_prefix}/{series_key}_kalshi_meta.json")
        if isinstance(remote, dict):
            return remote
    except Exception:
        pass
    return {}


def write_meta(
    out_dir: Path,
    series_key: str,
    r2_prefix: str,
    meta: dict[str, Any],
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    slim = {
        k: meta.get(k)
        for k in (
            "resolved_ticker",
            "prior_ticker",
            "strike",
            "match",
            "rolled",
            "title",
            "close_time",
            "status",
            "fetched_at",
        )
        if meta.get(k) is not None
    }
    path = _meta_path(out_dir, series_key)
    path.write_text(json.dumps(slim, indent=2) + "\n", encoding="utf-8")
    try:
        r2lib.put_json(f"{r2_prefix}/{series_key}_kalshi_meta.json", slim)
    except Exception:
        pass


def archive_contract(
    out_dir: Path,
    series_key: str,
    r2_prefix: str,
    *,
    ticker: str,
    points: list[dict[str, Any]],
) -> None:
    if not ticker or not points:
        return
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", ticker)
    path = out_dir / f"{series_key}_kalshi_archive_{safe}.jsonl"
    path.write_text(
        "".join(
            json.dumps({"date": p["date"], "value": p["value"]}, separators=(",", ":"))
            + "\n"
            for p in points
        ),
        encoding="utf-8",
    )
    try:
        r2lib.put_bytes(
            f"{r2_prefix}/{series_key}_kalshi_archive_{safe}.jsonl",
            path.read_bytes(),
            content_type="application/x-ndjson",
        )
    except Exception:
        pass


def persist_catalog_ticker(
    catalog_path: Path | None, series_key: str, ticker: str
) -> None:
    if not catalog_path or not catalog_path.exists() or not ticker:
        return
    cat = json.loads(catalog_path.read_text(encoding="utf-8"))
    changed = False
    for row in cat.get("series") or []:
        if row.get("key") != series_key:
            continue
        if str(row.get("kalshi_ticker") or "") != ticker:
            row["kalshi_ticker"] = ticker
            changed = True
        break
    if changed:
        catalog_path.write_text(json.dumps(cat, indent=2) + "\n", encoding="utf-8")


def stamp_contract_close(meta: dict[str, Any], mm: dict[str, Any]) -> None:
    """Copy Kalshi close onto catalog meta for ``series_shell`` / chart caption."""
    ct = mm.get("close_time") or mm.get("expiration_time")
    if not ct:
        return
    day = str(ct)[:10]
    if len(day) == 10 and day[4] == "-" and day[7] == "-":
        meta["contract_close"] = day


def fetch_from_meta(
    meta: dict[str, Any],
    *,
    out_dir: Path,
    r2_prefix: str,
    catalog_path: Path | None = None,
    fetched_at: str | None = None,
    start: str | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Fetch one catalog Kalshi series. Raises if no open successor (omit series)."""
    key = str(meta.get("key") or "")
    series_t = str(meta.get("kalshi_series") or "").strip()
    ticker = str(meta.get("kalshi_ticker") or "").strip()
    auto_roll = meta.get("kalshi_auto_roll")
    if auto_roll is None:
        auto_roll = True
    auto_roll = bool(auto_roll)

    if not series_t:
        raise RuntimeError("kalshi_series required")

    if not auto_roll:
        if not ticker:
            raise RuntimeError("kalshi_ticker required when kalshi_auto_roll is false")
        pts, mm = kalshilib.fetch_daily_probability(
            series_ticker=series_t, market_ticker=ticker
        )
        stamp_contract_close(meta, mm)
        if start:
            pts = [p for p in pts if p["date"] >= start]
        note = (
            f"{key}: Kalshi {ticker} last={mm.get('last_price')} "
            f"oi={mm.get('open_interest')}"
        )
        return pts, note

    match = resolve_match(meta)
    kmeta = load_meta(out_dir, key, r2_prefix)
    prior_ticker = str(kmeta.get("resolved_ticker") or ticker or "").strip() or None
    prior_pts = load_history(out_dir, key, r2_prefix)

    pts, mm = kalshilib.fetch_daily_probability_auto(
        series_ticker=series_t,
        strike=meta.get("kalshi_strike"),
        match=match,
        preferred_ticker=ticker or None,
        prior_ticker=prior_ticker,
        prior_points=prior_pts,
    )
    resolved = str(mm.get("resolved_ticker") or "")
    notes: list[str] = []
    if mm.get("rolled"):
        archive_contract(
            out_dir,
            key,
            r2_prefix,
            ticker=str(prior_ticker or ""),
            points=list(mm.get("archived_points") or prior_pts),
        )
        notes.append(
            f"{key}: Kalshi rolled {prior_ticker} → {resolved}; chart reset (prior archived)"
        )
    write_history(out_dir, key, r2_prefix, pts)
    if fetched_at:
        mm["fetched_at"] = fetched_at
    mm["match"] = match
    write_meta(out_dir, key, r2_prefix, mm)
    stamp_contract_close(meta, mm)
    if resolved:
        meta["kalshi_ticker"] = resolved
        if mm.get("rolled"):
            persist_catalog_ticker(catalog_path, key, resolved)
    if start:
        pts = [p for p in pts if p["date"] >= start]
    notes.append(
        f"{key}: Kalshi auto {resolved} match={match or '*'} "
        f"rolled={mm.get('rolled')} last={mm.get('last_price')} "
        f"oi={mm.get('open_interest')}"
    )
    return pts, "; ".join(notes)
