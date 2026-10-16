"""Trading Economics commodity spot series — shared history + API refresh.

One weekly ``markets/symbol`` call can refresh every pullable symbol in
``config/te_symbols.json``. Historical backfill is per-symbol and rare.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from lib import tradingeconomics as teapi


@dataclass(frozen=True)
class SpotSpec:
    series_key: str
    te_symbol: str
    ticker: str
    name: str
    unit: str = "cny_per_kg"
    history_name: str = ""
    latest_name: str = ""
    r2_history_key: str = ""
    pack: str = "dew_defense"

    def __post_init__(self) -> None:
        # dataclasses frozen: use object.__setattr__
        stem = self.series_key
        if not self.history_name:
            object.__setattr__(self, "history_name", f"{stem}_history.jsonl")
        if not self.latest_name:
            object.__setattr__(self, "latest_name", f"{stem}_latest.json")
        if not self.r2_history_key:
            object.__setattr__(
                self,
                "r2_history_key",
                f"themedata/{self.pack}/{self.history_name}",
            )


def specs_from_symbology(*, pull_only: bool = True) -> list[SpotSpec]:
    doc = teapi.load_symbology()
    out: list[SpotSpec] = []
    for key, row in (doc.get("symbols") or {}).items():
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "pull")
        if pull_only and status not in ("pull", ""):
            continue
        sym = str(row.get("symbol") or "")
        if not sym:
            continue
        hist = str(row.get("history_name") or "")
        latest = str(row.get("latest_name") or "")
        out.append(
            SpotSpec(
                series_key=key,
                te_symbol=sym,
                ticker=str(row.get("ticker") or sym.split(":")[0]),
                name=str(row.get("name") or key),
                unit=str(row.get("unit") or "CNY/Kg").lower().replace("/", "_per_"),
                pack=str(row.get("pack") or "dew_defense"),
                history_name=hist,
                latest_name=latest,
            )
        )
    return out


def get_spec(series_key: str) -> SpotSpec:
    for s in specs_from_symbology(pull_only=False):
        if s.series_key == series_key:
            return s
    raise KeyError(f"unknown TE spot series {series_key!r}")


def history_path(out_dir: Path, spec: SpotSpec) -> Path:
    return out_dir / spec.history_name


def latest_path(out_dir: Path, spec: SpotSpec) -> Path:
    return out_dir / spec.latest_name


def load_history(out_dir: Path, spec: SpotSpec) -> list[dict[str, Any]]:
    path = history_path(out_dir, spec)
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def history_to_body(rows: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows)


def rows_from_body(body: str | bytes) -> list[dict[str, Any]]:
    if isinstance(body, bytes):
        text = body.decode("utf-8", errors="replace")
    else:
        text = body
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def write_history(out_dir: Path, spec: SpotSpec, rows: list[dict[str, Any]]) -> None:
    rows = sorted(rows, key=lambda r: r.get("as_of") or "")
    path = history_path(out_dir, spec)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(history_to_body(rows), encoding="utf-8")
    if rows:
        latest_path(out_dir, spec).write_text(
            json.dumps(rows[-1], indent=2) + "\n", encoding="utf-8"
        )


def upsert_many(out_dir: Path, spec: SpotSpec, new_rows: list[dict[str, Any]]) -> int:
    by: dict[str, dict[str, Any]] = {
        str(r.get("as_of")): r for r in load_history(out_dir, spec) if r.get("as_of")
    }
    inserted = 0
    for row in new_rows:
        key = str(row.get("as_of") or "")
        if not key:
            continue
        if key not in by:
            inserted += 1
        by[key] = row
    write_history(out_dir, spec, list(by.values()))
    return inserted


def upsert_one(out_dir: Path, spec: SpotSpec, row: dict[str, Any]) -> bool:
    return upsert_many(out_dir, spec, [row]) > 0


def points_from_history(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pts: list[dict[str, Any]] = []
    for r in sorted(rows, key=lambda x: x.get("as_of") or ""):
        d = r.get("as_of")
        v = r.get("price_cny_kg")
        if d is None or v is None:
            continue
        pts.append({"date": str(d), "value": float(v)})
    return pts


def _snap_row(
    spec: SpotSpec,
    *,
    as_of: str,
    price: float,
    source: str,
    notes: str,
) -> dict[str, Any]:
    return {
        "schema_version": 0,
        "source": source,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "as_of": as_of,
        "price_cny_kg": float(price),
        "unit": "cny_per_kg",
        "symbol": spec.ticker,
        "te_symbol": spec.te_symbol,
        "full_name": spec.name,
        "notes": notes,
    }


def spot_from_api_row(spec: SpotSpec, row: dict[str, Any]) -> dict[str, Any]:
    base = teapi.snapshot_to_spot(row)
    if base.get("price_cny_kg") is None:
        raise ValueError(f"TE snapshot missing Last/Close for {spec.te_symbol}")
    return _snap_row(
        spec,
        as_of=str(base["as_of"]),
        price=float(base["price_cny_kg"]),
        source=f"te_api:{spec.te_symbol}",
        notes="CFD proxy (Trading Economics API); not Argus/SMM physical.",
    )


def api_backfill(
    out_dir: Path,
    spec: SpotSpec,
    *,
    d1: str | None = None,
    d2: str | None = None,
    force: bool = False,
) -> int:
    if d1 is None:
        existing = load_history(out_dir, spec)
        if existing and not force:
            last = max(str(r.get("as_of") or "") for r in existing)
            if last:
                d1 = last
        if d1 is None:
            d1 = "2020-01-01"
    if d2 is None:
        d2 = date.today().isoformat()
    hist = teapi.markets_historical(
        spec.te_symbol,
        d1=d1,
        d2=d2,
        max_age_hours=24.0 * 14,
        force=force,
        allow_reserve=True,
    )
    pts = teapi.historical_to_points(hist, symbol=spec.te_symbol)
    rows = [
        _snap_row(
            spec,
            as_of=p["date"],
            price=p["value"],
            source=f"te_api_historical:{spec.te_symbol}",
            notes="CFD proxy (Trading Economics API historical Close).",
        )
        for p in pts
    ]
    upsert_many(out_dir, spec, rows)
    return len(pts)


def ingest_historical_file(out_dir: Path, spec: SpotSpec, path: Path) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"expected list in {path}")
    pts = teapi.historical_to_points(data, symbol=spec.te_symbol)
    rows = [
        _snap_row(
            spec,
            as_of=p["date"],
            price=p["value"],
            source=f"te_api_historical_file:{path.name}",
            notes="CFD proxy (Trading Economics API historical Close).",
        )
        for p in pts
    ]
    upsert_many(out_dir, spec, rows)
    return len(pts)


def refresh_batch_api(
    out_dir: Path,
    specs: list[SpotSpec],
    *,
    force: bool = False,
    max_age_hours: float | None = 12.0,
) -> dict[str, tuple[dict[str, Any], bool]]:
    """One markets/symbol call for all specs → upsert each history."""
    if not specs:
        return {}
    symbols = [s.te_symbol for s in specs]
    rows = teapi.markets_snapshot(
        symbols, max_age_hours=max_age_hours, force=force
    )
    by_sym = {str(r.get("Symbol") or "").upper(): r for r in rows}
    out: dict[str, tuple[dict[str, Any], bool]] = {}
    for spec in specs:
        row = by_sym.get(spec.te_symbol.upper())
        if not row:
            raise RuntimeError(f"TE snapshot missing {spec.te_symbol}")
        snap = spot_from_api_row(spec, row)
        inserted = upsert_one(out_dir, spec, snap)
        out[spec.series_key] = (snap, inserted)
    return out
