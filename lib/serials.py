"""Stable chart/series serials: ``{source}-{family}-{dataset:04d}-{series:04d}``.

Matching contract
-----------------
* ``series_key`` / ``dataset_id`` remain the primary join keys for packs,
  boards, and attachments.
* ``serial`` is a frozen crosswalk stamped on datasets + catalog series +
  published pack series. Never renumber after assign; new rows take the next
  free ``dataset_n`` under ``(source, family)``.
* Family ``0000`` means flat (no mid-node). Deep vendors use a slug family
  (e.g. FMP revenue adapters → ``fmp-revenue-####-####``).
"""

from __future__ import annotations

import re
from typing import Any

FAMILY_FLAT = "0000"

# Adapter ``source`` on datasets/catalog → canonical (source_slug, family_slug|None).
# Prefer collapsing FMP API variants under source=fmp + family=revenue|capex|…
# Bare ``fmp`` is handled in split_source_family via id/description heuristics.
SOURCE_FAMILY_RULES: dict[str, tuple[str, str | None]] = {
    "fmp_revenue": ("fmp", "revenue"),
    "fmp_revenue_ticker": ("fmp", "revenue"),
    "fmp_revenue_segment": ("fmp", "revenue"),
    "fmp_capex": ("fmp", "capex"),
    "fmp_capex_ticker": ("fmp", "capex"),
    "fmp_employee_count": ("fmp", "employees"),
    "fmp_income_field": ("fmp", "income"),
    "fmp_income_field_ticker": ("fmp", "income"),
    "fmp_revenue_per_employee": ("fmp", "revenue_per_employee"),
    "fmp_transcript_keywords": ("fmp", "transcript"),
    "derived_sum": ("derived", "sum"),
    "derived_ratio": ("derived", "ratio"),
    # TE vendor spellings / adapters → one serial source.
    "trading_economics": ("trading_economics", None),
    "tradingeconomics": ("trading_economics", None),
    "te_indicator": ("trading_economics", "indicator"),
}

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_SERIAL_RE = re.compile(
    r"^([a-z0-9_]+)-([a-z0-9_]+)-(\d{4})-(\d{4})$"
)


def slug_token(value: str | None, *, fallback: str = "unknown") -> str:
    s = _SLUG_RE.sub("_", (value or "").strip().lower()).strip("_")
    return s or fallback


def infer_fmp_family(
    *,
    dataset_id: str | None = None,
    description: str | None = None,
    name: str | None = None,
) -> str:
    """Family for bare ``source=fmp`` rows from id / description shape."""
    sid = (dataset_id or "").strip().lower()
    blob = " ".join(
        p
        for p in (sid, (description or "").lower(), (name or "").lower())
        if p
    )
    # Order matters: transcript/capex before generic revenue.
    if "transcript" in blob:
        return "transcript"
    if "capex" in blob:
        return "capex"
    if "employee" in blob:
        return "employees"
    if (
        "revenue" in blob
        or sid.endswith("_revenue")
        or "_revenue_" in sid
    ):
        return "revenue"
    if "income" in blob or sid.endswith("_rd") or "_rd_" in sid:
        return "income"
    return FAMILY_FLAT


def split_source_family(
    raw_source: str | None,
    *,
    provider: str | None = None,
    dataset_id: str | None = None,
    description: str | None = None,
    name: str | None = None,
) -> tuple[str, str]:
    """Return ``(source_slug, family_slug)`` with family ``0000`` when flat."""
    raw = (raw_source or "").strip().lower()
    if not raw:
        raw = slug_token(provider, fallback="unknown")

    if raw == "fmp":
        fam = infer_fmp_family(
            dataset_id=dataset_id,
            description=description,
            name=name,
        )
        return "fmp", fam if fam != FAMILY_FLAT else FAMILY_FLAT

    if raw in SOURCE_FAMILY_RULES:
        src, fam = SOURCE_FAMILY_RULES[raw]
        return slug_token(src), slug_token(fam) if fam else FAMILY_FLAT

    if raw.startswith("fmp_"):
        rest = raw[4:] or None
        return "fmp", slug_token(rest) if rest else FAMILY_FLAT

    if raw.startswith("derived_"):
        rest = raw[8:] or None
        return "derived", slug_token(rest) if rest else FAMILY_FLAT

    return slug_token(raw), FAMILY_FLAT


def format_serial(
    source: str,
    family: str,
    dataset_n: int,
    series_n: int = 1,
) -> str:
    return (
        f"{slug_token(source)}-{slug_token(family, fallback=FAMILY_FLAT)}-"
        f"{int(dataset_n):04d}-{int(series_n):04d}"
    )


def parse_serial(serial: str | None) -> dict[str, Any] | None:
    if not serial or not isinstance(serial, str):
        return None
    m = _SERIAL_RE.match(serial.strip())
    if not m:
        return None
    return {
        "source": m.group(1),
        "family": m.group(2),
        "dataset_n": int(m.group(3)),
        "series_n": int(m.group(4)),
        "serial": serial.strip(),
    }


def counter_key(source: str, family: str) -> str:
    return f"{slug_token(source)}|{slug_token(family, fallback=FAMILY_FLAT)}"


def serial_parts_dict(
    *,
    source: str,
    family: str,
    dataset_n: int,
    series_n: int,
) -> dict[str, Any]:
    serial = format_serial(source, family, dataset_n, series_n)
    return {
        "serial": serial,
        "serial_source": slug_token(source),
        "serial_family": slug_token(family, fallback=FAMILY_FLAT),
        "serial_dataset_n": int(dataset_n),
        "serial_series_n": int(series_n),
    }
