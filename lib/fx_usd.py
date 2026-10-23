"""Broad USD conversion via *latest* spot FX (constant rate over history).

For stacking multi-currency FMP revenue/CapEx (TWD/JPY/KRW/…) without a full
point-in-time FX panel. Applying one recent cross to every historical print:

- Makes **levels** roughly comparable in USD (good enough for stacked boards).
- Leaves **YoY % unchanged** vs local currency (constant factor cancels).

Not true historical USD — label units ``usd_billions_fx_latest`` (or notes)
when publishing. Prefer FRED DEX daily series; optional FMP quote fallback.

Usage::

    from lib import fx_usd
    rates = fx_usd.latest_rates(["TWD", "JPY", "KRW", "EUR"])
    usd = fx_usd.to_usd(42.89e9, "TWD", rates=rates)  # Unimicron TWD → USD
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Iterable

from lib import fred as fredlib

# FRED: most Asian crosses are "foreign currency units per 1 USD".
# EUR uses DEXUSEU = USD per 1 EUR (inverted convention).
_FRED_MAP: dict[str, tuple[str, str]] = {
    "JPY": ("DEXJPUS", "foreign_per_usd"),
    "CNY": ("DEXCHUS", "foreign_per_usd"),
    "KRW": ("DEXKOUS", "foreign_per_usd"),
    "TWD": ("DEXTAUS", "foreign_per_usd"),
    "THB": ("DEXTHUS", "foreign_per_usd"),
    "CHF": ("DEXSZUS", "foreign_per_usd"),
    "EUR": ("DEXUSEU", "usd_per_foreign"),
    "GBP": ("DEXUSUK", "usd_per_foreign"),
}

_cache: dict[str, "FxRate"] | None = None
_cache_at: float = 0.0
_CACHE_TTL_S = 6 * 3600


@dataclass(frozen=True)
class FxRate:
    currency: str
    """Units of foreign currency per 1 USD, always (normalize on load)."""
    foreign_per_usd: float
    as_of: str
    source: str
    fred_id: str | None = None

    @property
    def usd_per_foreign(self) -> float:
        return 1.0 / self.foreign_per_usd if self.foreign_per_usd else 0.0


def _fred_latest(series_id: str) -> tuple[str, float]:
    pts = fredlib.fetch_series(series_id)
    if not pts:
        raise RuntimeError(f"FRED {series_id}: no observations")
    d, v = pts[-1]
    return str(d)[:10], float(v)


def _load_one(ccy: str) -> FxRate:
    c = ccy.strip().upper()
    if c == "USD":
        return FxRate("USD", 1.0, "spot", "identity")
    if c not in _FRED_MAP:
        raise RuntimeError(
            f"no FRED FX map for {c!r}; known={sorted(_FRED_MAP)}"
        )
    sid, mode = _FRED_MAP[c]
    as_of, raw = _fred_latest(sid)
    if mode == "foreign_per_usd":
        fpu = raw
    elif mode == "usd_per_foreign":
        if raw <= 0:
            raise RuntimeError(f"FRED {sid} non-positive: {raw}")
        fpu = 1.0 / raw
    else:
        raise RuntimeError(f"unknown mode {mode}")
    if fpu <= 0:
        raise RuntimeError(f"FX {c} non-positive foreign_per_usd={fpu}")
    return FxRate(c, fpu, as_of, "fred_dex_latest", sid)


def latest_rates(
    currencies: Iterable[str] | None = None,
    *,
    force: bool = False,
) -> dict[str, FxRate]:
    """Return ``{CCY: FxRate}`` using the most recent FRED print per cross.

    Cached ~6h. Pass ``currencies`` to fetch only what you need (plus USD).
    """
    global _cache, _cache_at
    want = {str(c).strip().upper() for c in (currencies or _FRED_MAP)}
    want.add("USD")
    now = time.time()
    if (
        not force
        and _cache is not None
        and (now - _cache_at) < _CACHE_TTL_S
        and want.issubset(_cache.keys())
    ):
        return {k: _cache[k] for k in want}

    out: dict[str, FxRate] = {"USD": FxRate("USD", 1.0, "spot", "identity")}
    for c in sorted(want):
        if c == "USD":
            continue
        out[c] = _load_one(c)
        time.sleep(0.05)
    if _cache is None:
        _cache = {}
    _cache.update(out)
    _cache_at = now
    return {k: _cache[k] for k in want}


def to_usd(
    amount: float,
    currency: str,
    *,
    rates: dict[str, FxRate] | None = None,
) -> float:
    """Convert ``amount`` in ``currency`` → USD using latest (or provided) rates."""
    c = (currency or "USD").strip().upper()
    if c == "USD":
        return float(amount)
    table = rates if rates is not None else latest_rates([c])
    fx = table.get(c)
    if fx is None:
        fx = _load_one(c)
    return float(amount) / fx.foreign_per_usd


def convert_points(
    points: list[dict[str, Any]],
    currency: str,
    *,
    rates: dict[str, FxRate] | None = None,
    scale: float = 1.0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Convert point values to USD (optional ``scale``, e.g. 1e9 → billions).

    Preserves ``date`` / ``symbol``; adds ``currency_source`` on meta only.
    """
    c = (currency or "USD").strip().upper()
    table = rates if rates is not None else latest_rates([c])
    fx = table.get(c) or _load_one(c)
    out: list[dict[str, Any]] = []
    for p in points:
        usd = to_usd(float(p["value"]), c, rates={c: fx, "USD": table["USD"]})
        row = {
            "date": p["date"],
            "value": round(usd / scale, 4) if scale else round(usd, 4),
        }
        if p.get("symbol"):
            row["symbol"] = p["symbol"]
        out.append(row)
    meta = {
        "currency_source": c,
        "fx_foreign_per_usd": fx.foreign_per_usd,
        "fx_as_of": fx.as_of,
        "fx_source": fx.source,
        "fx_fred_id": fx.fred_id,
        "method": "latest_spot_constant_history",
        "note": (
            "All history converted at one latest FX print — levels ≈ USD; "
            "YoY % matches local-currency YoY."
        ),
    }
    return out, meta
