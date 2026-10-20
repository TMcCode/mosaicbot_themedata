#!/usr/bin/env python3
"""Wire FMP revenue (+ optional transcript keywords) across recommended themes.

Idempotent: skips series_keys / attachments / board recipes / dataset ids / score
components that already exist.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"


def load(name: str):
    return json.loads((CONFIG / name).read_text(encoding="utf-8"))


def save(name: str, obj) -> None:
    (CONFIG / name).write_text(
        json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def rev_series(
    key: str,
    *,
    symbols: list[str] | None = None,
    symbol: str | None = None,
    display: str,
    short: str,
    official: str,
    dataset_id: str,
    usefulness: int = 5,
    scope_default: str = "theme",
    notes: str | None = None,
) -> dict:
    row = {
        "key": key,
        "source": "fmp_revenue_ticker" if symbol else "fmp_revenue",
        "fmp_limit": 40,
        "label": display,
        "official_name": official,
        "short_name": short,
        "display_name": display,
        "frequency": "quarterly",
        "unit": "usd_billions",
        "derived": ["yoy_pct", "m3_avg_yoy_pct"],
        "scope_default": scope_default,
        "usefulness": usefulness,
        "dataset_id": dataset_id,
        "access": "api",
        "cadence": "quarterly",
        "as_of_semantics": "period_end",
    }
    if symbol:
        row["fmp_symbol"] = symbol
    else:
        row["fmp_symbols"] = list(symbols or [])
        row["require_all"] = False
    if notes:
        row["notes"] = notes
    return row


def tx_series(
    key: str,
    *,
    symbols: list[str],
    keywords: list[str],
    display: str,
    short: str,
    official: str,
    dataset_id: str,
    notes: str | None = None,
) -> dict:
    row = {
        "key": key,
        "source": "fmp_transcript_keywords",
        "fmp_symbols": list(symbols),
        "fmp_limit": 16,
        "require_all": False,
        "keywords": list(keywords),
        "label": display,
        "official_name": official,
        "short_name": short,
        "display_name": display,
        "frequency": "quarterly",
        "unit": "keyword_hits",
        "derived": ["yoy_pct"],
        "scope_default": "theme",
        "usefulness": 4,
        "dataset_id": dataset_id,
        "access": "api",
        "cadence": "quarterly",
        "as_of_semantics": "period_end",
        "notes": notes
        or "Chart-only narrative intensity from FMP earnings-call transcripts.",
    }
    return row


def dataset_row(
    id_: str,
    *,
    name: str,
    description: str,
    pack: str,
    series_keys: list[str],
    symbols: list[str] | None = None,
    data_type: str = "financials",
) -> dict:
    row = {
        "id": id_,
        "license": "free",
        "status": "pull",
        "access": "api",
        "cadence": "quarterly",
        "sector": "themedata",
        "data_type": data_type,
        "region": "US",
        "description": description,
        "source": "fmp",
        "pack": pack,
        "series_keys": series_keys,
        "as_of_semantics": "period_end",
        "name": name,
    }
    if symbols:
        row["fmp_symbols"] = symbols
    return row


def ensure_series(catalog: dict, rows: list[dict]) -> int:
    existing = {s.get("key") for s in catalog.get("series") or []}
    added = 0
    for row in rows:
        if row["key"] in existing:
            continue
        catalog.setdefault("series", []).append(row)
        existing.add(row["key"])
        added += 1
    return added


def ensure_attach(
    attachments: list[dict],
    *,
    series_key: str,
    theme: str,
    theme_slug: str,
    scope: str = "theme",
    ticker: str | None = None,
) -> bool:
    for a in attachments:
        if (
            a.get("series_key") == series_key
            and a.get("theme_slug") == theme_slug
            and a.get("scope") == scope
            and (a.get("ticker") or None) == ticker
        ):
            return False
    attachments.append(
        {
            "series_key": series_key,
            "scope": scope,
            "theme": theme,
            "theme_slug": theme_slug,
            "ticker": ticker,
        }
    )
    return True


def ensure_board_recipe(boards: dict, *, theme_slug: str, scope: str, ticker, recipe) -> bool:
    for b in boards.get("boards") or []:
        if b.get("theme_slug") != theme_slug or b.get("scope") != scope:
            continue
        if scope == "ticker" and b.get("ticker") != ticker:
            continue
        if scope == "theme" and b.get("ticker") not in (None, ""):
            continue
        recipes = b.setdefault("recipes", [])
        # match by series_key or stacked series_keys set
        for r in recipes:
            if r.get("series_key") and r.get("series_key") == recipe.get("series_key"):
                return False
            if r.get("chart_type") == "stacked_bar" and recipe.get("chart_type") == "stacked_bar":
                if set(r.get("series_keys") or []) == set(recipe.get("series_keys") or []):
                    return False
        # insert revenue stacks near top (after first 2 recipes if present)
        insert_at = min(2, len(recipes))
        if recipe.get("chart_type") == "stacked_bar" or (
            recipe.get("series_key") or ""
        ).endswith("_revenue") or "transcript" in (recipe.get("series_key") or ""):
            recipes.insert(insert_at, recipe)
        else:
            recipes.append(recipe)
        return True
    return False


def ensure_dataset(datasets: dict, row: dict) -> bool:
    ids = {d.get("id") for d in datasets.get("datasets") or []}
    if row["id"] in ids:
        return False
    datasets.setdefault("datasets", []).append(row)
    return True


def ensure_score_component(scores: dict, theme_slug: str, component: dict) -> bool:
    for t in scores.get("themes") or []:
        if t.get("theme_slug") != theme_slug:
            continue
        comps = t.setdefault("components", [])
        for c in comps:
            if c.get("series_key") == component["series_key"]:
                return False
        # insert after first CapEx-like component if any, else end
        comps.append(component)
        return True
    return False


# ---------------------------------------------------------------------------
# Theme specs
# ---------------------------------------------------------------------------

OPTICAL_SYMS = ["CIEN", "COHR", "GLW", "INFN", "LITE"]
NAND_SYMS = ["MU", "SIMO", "SNDK", "WDC"]
ESSD_SYMS = ["STX", "WDC", "SNDK", "SIMO"]  # MU ticker-only elsewhere
HOME_SYMS = ["HD", "LOW", "FND", "TSCO"]  # skip LESL (pool specialty)
CHICKEN_SYMS = ["YUM", "QSR", "WING", "LOCO"]
STEEL_SYMS = ["X", "CLF", "MT"]
AI_SYMS = ["BSY", "CDNS", "INTU", "SHOP", "WDAY"]  # ORCL ticker-only (conglomerate)
DEW_SYMS = ["IPGP", "LASR", "COHR", "MKSI", "AVAV"]
LAUNCH_SYMS = ["RKLB", "LUNR", "FLY"]
SURV_SYMS = ["BKSY", "PL", "SATL"]

SPECS = [
    {
        "catalog": "optical_fiber_catalog.json",
        "pack": "optical_fiber",
        "theme": "Data Centers '25: Optical & Fiber Systems",
        "theme_slug": "data-centers-25-optical-fiber-systems",
        "agg_key": "optical_vendor_revenue",
        "agg_display": "Optical vendor revenue (CIEN+COHR+GLW+INFN+LITE)",
        "symbols": OPTICAL_SYMS,
        "tickers": {s: f"{s.lower()}_revenue" for s in OPTICAL_SYMS},
        "tx_key": "optical_transcript_keywords",
        "tx_display": "Optical keyword hits in vendor earnings calls",
        "tx_keywords": [
            "800G",
            "1.6T",
            "CPO",
            "coherent",
            "transceiver",
            "DSP",
            "optical",
            "datacenter interconnect",
        ],
        "score": True,
        "score_weight": 0.15,
        "score_rationale": "Theme-member optical vendor revenue growth \u2192 realized networking demand.",
    },
    {
        "catalog": "memory_nand_catalog.json",
        "pack": "memory_nand",
        "theme": "Memory '26: NAND / Flash",
        "theme_slug": "memory-26-nand-flash",
        "agg_key": "nand_member_revenue",
        "agg_display": "NAND member revenue (MU+SIMO+SNDK+WDC)",
        "symbols": NAND_SYMS,
        "tickers": {s: f"{s.lower()}_revenue" for s in NAND_SYMS},
        "tx_key": "nand_transcript_keywords",
        "tx_display": "NAND keyword hits in member earnings calls",
        "tx_keywords": [
            "NAND",
            "TLC",
            "QLC",
            "flash",
            "SSD",
            "bit growth",
            "wafer",
        ],
        "score": True,
        "score_weight": 0.15,
        "score_rationale": "Theme-member NAND revenue growth \u2192 realized flash demand.",
    },
    {
        "catalog": "memory_essd_catalog.json",
        "pack": "memory_essd",
        "theme": "Memory '26: Enterprise SSD & Managed Storage",
        "theme_slug": "memory-26-enterprise-ssd-managed-storage",
        "agg_key": "essd_member_revenue",
        "agg_display": "Enterprise storage member revenue (STX+WDC+SNDK+SIMO)",
        "symbols": ESSD_SYMS,
        "tickers": {s: f"{s.lower()}_essd_revenue" for s in ESSD_SYMS},
        "tx_key": "essd_transcript_keywords",
        "tx_display": "Enterprise SSD keyword hits in member earnings calls",
        "tx_keywords": [
            "SSD",
            "enterprise SSD",
            "nearline",
            "HAMR",
            "HDD",
            "exabyte",
            "cloud storage",
        ],
        "score": True,
        "score_weight": 0.15,
        "score_rationale": "Theme-member storage revenue growth \u2192 enterprise SSD/HDD demand.",
    },
    {
        "catalog": "memory_dram_catalog.json",
        "pack": "memory_dram",
        "theme": "Memory '26: DRAM",
        "theme_slug": "memory-26-dram",
        "agg_key": None,  # MU only — avoid double-count vs NAND aggregate
        "symbols": [],
        "tickers": {"MU": "mu_dram_revenue"},
        "tx_key": "dram_transcript_keywords",
        "tx_display": "DRAM/HBM keyword hits in MU earnings calls",
        "tx_symbols": ["MU"],
        "tx_keywords": [
            "DRAM",
            "DDR5",
            "HBM",
            "HBM3",
            "HBM3E",
            "high bandwidth memory",
        ],
        "score": False,
    },
    {
        "catalog": "home_improvement_catalog.json",
        "pack": "home_improvement",
        "theme": "Buying a House '26: Home Improvement Retailers",
        "theme_slug": "buying-a-house-26-home-improvement-retailers",
        "agg_key": "home_improvement_revenue",
        "agg_display": "Home improvement retailer revenue (HD+LOW+FND+TSCO)",
        "symbols": HOME_SYMS,
        "tickers": {s: f"{s.lower()}_revenue" for s in HOME_SYMS},
        "tx_key": None,
        "score": True,
        "score_weight": 0.2,
        "score_rationale": "Theme-member retailer revenue growth \u2192 home-improvement demand.",
    },
    {
        "catalog": "restaurants_chicken_catalog.json",
        "pack": "restaurants_chicken",
        "theme": "Restaurants '26: Chicken Buyers",
        "theme_slug": "restaurants-26-chicken-buyers",
        "agg_key": "chicken_buyer_revenue",
        "agg_display": "Chicken buyer revenue (YUM+QSR+WING+LOCO)",
        "symbols": CHICKEN_SYMS,
        "tickers": {s: f"{s.lower()}_revenue" for s in CHICKEN_SYMS},
        "tx_key": "chicken_transcript_keywords",
        "tx_display": "Chicken/wing keyword hits in buyer earnings calls",
        "tx_keywords": [
            "chicken",
            "wing",
            "wings",
            "food cost",
            "commodity",
            "bone-in",
        ],
        "score": True,
        "score_weight": 0.15,
        "score_rationale": "Theme-member restaurant revenue growth \u2192 chicken-channel demand.",
    },
    {
        "catalog": "aisi_weekly_catalog.json",
        "pack": "aisi_weekly",
        "theme": "Steel '26: Integrated / Blast Furnace Producers",
        "theme_slug": "steel-26-integrated-blast-furnace-producers",
        "agg_key": "steel_member_revenue",
        "agg_display": "BF steel member revenue (X+CLF+MT)",
        "symbols": STEEL_SYMS,
        "tickers": {s: f"{s.lower()}_revenue" for s in STEEL_SYMS},
        "tx_key": None,
        "score": True,
        "score_weight": 0.2,
        "score_rationale": "Theme-member steel revenue growth \u2192 realized mill demand.",
    },
    {
        "catalog": "ai_resilient_software_catalog.json",
        "pack": "ai_resilient_software",
        "theme": "AI '26: Mizuho Most AI-Resilient Software",
        "theme_slug": "ai-26-mizuho-most-ai-resilient-software",
        "agg_key": "ai_resilient_software_revenue",
        "agg_display": "AI-resilient software revenue (BSY+CDNS+INTU+SHOP+WDAY)",
        "symbols": AI_SYMS,
        "tickers": {s: f"{s.lower()}_revenue" for s in AI_SYMS},
        "tx_key": None,  # AI keyword noise
        "score": True,
        "score_weight": 0.2,
        "score_rationale": "Theme-member software revenue growth \u2192 resilient demand.",
    },
    {
        "catalog": "dew_defense_catalog.json",
        "pack": "dew_defense",
        "theme": "Modern Warfare '26: Lasers & Directed Energy Weapons",
        "theme_slug": "modern-warfare-26-lasers-directed-energy-weapons",
        "agg_key": "dew_pureplay_revenue",
        "agg_display": "DEW pure-play revenue (IPGP+LASR+COHR+MKSI+AVAV)",
        "symbols": DEW_SYMS,
        "tickers": {s: f"{s.lower()}_dew_revenue" for s in DEW_SYMS},
        "tx_key": "dew_transcript_keywords",
        "tx_display": "DEW/laser keyword hits in pure-play earnings calls",
        "tx_keywords": [
            "directed energy",
            "high energy laser",
            "HELWS",
            "laser weapon",
            "counter-UAS",
            "fiber laser",
        ],
        "score": True,
        "score_weight": 0.1,
        "score_rationale": "DEW pure-play revenue growth \u2192 laser/sensor demand (ex-primes).",
    },
    {
        "catalog": "space_launch_catalog.json",
        "pack": "space_launch",
        "theme": "Space Supply Chain '26: Space Launch & Orbital Logistics",
        "theme_slug": "space-supply-chain-26-space-launch-orbital-logistics",
        "agg_key": "space_launch_pureplay_revenue",
        "agg_display": "Space launch pure-play revenue (RKLB+LUNR+FLY)",
        "symbols": LAUNCH_SYMS,
        "tickers": {s: f"{s.lower()}_revenue" for s in LAUNCH_SYMS},
        "tx_key": None,
        "score": True,
        "score_weight": 0.1,
        "score_rationale": "Launch pure-play revenue growth \u2192 commercial launch demand.",
    },
    {
        "catalog": "space_surveillance_catalog.json",
        "pack": "space_surveillance",
        "theme": "Space Supply Chain '26: Surveillance Satellites",
        "theme_slug": "space-supply-chain-26-surveillance-satellites",
        "agg_key": "surveillance_sat_revenue",
        "agg_display": "Surveillance sat revenue (BKSY+PL+SATL)",
        "symbols": SURV_SYMS,
        "tickers": {s: f"{s.lower()}_revenue" for s in SURV_SYMS},
        "tx_key": None,
        "score": True,
        "score_weight": 0.1,
        "score_rationale": "EO/SAR vendor revenue growth \u2192 surveillance demand.",
    },
]


def ticker_display(sym: str, key: str) -> str:
    names = {
        "CIEN": "Ciena",
        "COHR": "Coherent",
        "GLW": "Corning",
        "INFN": "Infinera",
        "LITE": "Lumentum",
        "MU": "Micron",
        "SIMO": "Silicon Motion",
        "SNDK": "Sandisk",
        "WDC": "Western Digital",
        "STX": "Seagate",
        "HD": "Home Depot",
        "LOW": "Lowe's",
        "FND": "Floor & Decor",
        "TSCO": "Tractor Supply",
        "YUM": "Yum! Brands",
        "QSR": "Restaurant Brands",
        "WING": "Wingstop",
        "LOCO": "El Pollo Loco",
        "X": "U.S. Steel",
        "CLF": "Cleveland-Cliffs",
        "MT": "ArcelorMittal",
        "BSY": "Bentley Systems",
        "CDNS": "Cadence",
        "INTU": "Intuit",
        "SHOP": "Shopify",
        "WDAY": "Workday",
        "IPGP": "IPG Photonics",
        "LASR": "nLIGHT",
        "MKSI": "MKS Instruments",
        "AVAV": "AeroVironment",
        "RKLB": "Rocket Lab",
        "LUNR": "Intuitive Machines",
        "FLY": "Firefly Aerospace",
        "BKSY": "BlackSky",
        "PL": "Planet Labs",
        "SATL": "Satellogic",
    }
    return f"{names.get(sym, sym)} revenue"


def main() -> None:
    attachments_doc = load("series_attachments.json")
    attachments = attachments_doc.setdefault("attachments", [])
    boards = load("default_boards.json")
    datasets = load("datasets.json")
    scores = load("theme_scores.json")

    summary = []

    for spec in SPECS:
        cat_name = spec["catalog"]
        catalog = load(cat_name)
        rows: list[dict] = []
        symbols = list(spec.get("symbols") or [])
        tickers: dict[str, str] = dict(spec.get("tickers") or {})

        if spec.get("agg_key"):
            rows.append(
                rev_series(
                    spec["agg_key"],
                    symbols=symbols,
                    display=spec["agg_display"],
                    short="Member revenue",
                    official=f"FMP income-statement revenue sum — {','.join(symbols)}",
                    dataset_id=spec["agg_key"],
                    notes="Theme-member top-line sum (FMP).",
                )
            )

        for sym, key in tickers.items():
            rows.append(
                rev_series(
                    key,
                    symbol=sym,
                    display=ticker_display(sym, key),
                    short=f"{sym} revenue",
                    official=f"FMP income-statement revenue — {sym}",
                    dataset_id=key,
                    scope_default="ticker",
                )
            )

        if spec.get("tx_key"):
            tx_syms = list(spec.get("tx_symbols") or symbols)
            rows.append(
                tx_series(
                    spec["tx_key"],
                    symbols=tx_syms,
                    keywords=spec["tx_keywords"],
                    display=spec["tx_display"],
                    short="Transcript keywords",
                    official=(
                        f"FMP earning-call-transcript keyword counts — "
                        f"{','.join(tx_syms)}"
                    ),
                    dataset_id=spec["tx_key"],
                )
            )

        added = ensure_series(catalog, rows)
        save(cat_name, catalog)

        # attachments + datasets
        theme, slug, pack = spec["theme"], spec["theme_slug"], spec["pack"]
        for row in rows:
            ensure_attach(
                attachments,
                series_key=row["key"],
                theme=theme,
                theme_slug=slug,
                scope="theme" if row.get("scope_default") != "ticker" else "theme",
            )
            # ticker-scoped for per-name revenue
            if row["source"] == "fmp_revenue_ticker":
                ensure_attach(
                    attachments,
                    series_key=row["key"],
                    theme=theme,
                    theme_slug=slug,
                    scope="ticker",
                    ticker=row["fmp_symbol"],
                )
            ensure_dataset(
                datasets,
                dataset_row(
                    row["dataset_id"],
                    name=row["display_name"],
                    description=row["official_name"],
                    pack=pack,
                    series_keys=[row["key"]],
                    symbols=row.get("fmp_symbols")
                    or ([row["fmp_symbol"]] if row.get("fmp_symbol") else None),
                    data_type=(
                        "transcript_keywords"
                        if "transcript" in row["key"]
                        else "financials"
                    ),
                ),
            )

        # boards
        if spec.get("agg_key") and tickers:
            ensure_board_recipe(
                boards,
                theme_slug=slug,
                scope="theme",
                ticker=None,
                recipe={
                    "chart_type": "stacked_bar",
                    "metric": "raw",
                    "display_name": spec["agg_display"].replace(" revenue", " revenue by name"),
                    "series_keys": list(tickers.values()),
                    "sum_series_key": spec["agg_key"],
                },
            )
        elif spec.get("agg_key"):
            ensure_board_recipe(
                boards,
                theme_slug=slug,
                scope="theme",
                ticker=None,
                recipe={
                    "series_key": spec["agg_key"],
                    "metric": "raw",
                    "chart_type": "bar",
                },
            )
        for sym, key in tickers.items():
            ensure_board_recipe(
                boards,
                theme_slug=slug,
                scope="ticker",
                ticker=sym,
                recipe={"series_key": key, "metric": "raw", "chart_type": "bar"},
            )
            # if no ticker board exists yet, create a minimal one
            has_ticker_board = any(
                b.get("theme_slug") == slug
                and b.get("scope") == "ticker"
                and b.get("ticker") == sym
                for b in boards.get("boards") or []
            )
            if not has_ticker_board:
                boards.setdefault("boards", []).append(
                    {
                        "scope": "ticker",
                        "theme": theme,
                        "theme_slug": slug,
                        "ticker": sym,
                        "recipes": [
                            {"series_key": key, "metric": "raw", "chart_type": "bar"}
                        ],
                    }
                )

        if spec.get("tx_key"):
            ensure_board_recipe(
                boards,
                theme_slug=slug,
                scope="theme",
                ticker=None,
                recipe={
                    "series_key": spec["tx_key"],
                    "metric": "raw",
                    "chart_type": "bar",
                },
            )
        # DRAM MU-only: theme board bar for mu_dram_revenue + transcripts
        if slug == "memory-26-dram":
            ensure_board_recipe(
                boards,
                theme_slug=slug,
                scope="theme",
                ticker=None,
                recipe={
                    "series_key": "mu_dram_revenue",
                    "metric": "raw",
                    "chart_type": "bar",
                },
            )

        if spec.get("score") and spec.get("agg_key"):
            ensure_score_component(
                scores,
                slug,
                {
                    "series_key": spec["agg_key"],
                    "metric": "yoy_pct",
                    "weight": float(spec.get("score_weight") or 0.15),
                    "polarity": 1.0,
                    "enabled": True,
                    "rationale": spec.get("score_rationale") or "Member revenue growth.",
                },
            )

        summary.append(f"{pack}: +{added} series")

    save("series_attachments.json", attachments_doc)
    save("default_boards.json", boards)
    save("datasets.json", datasets)
    save("theme_scores.json", scores)
    print("\n".join(summary))
    print("done")


if __name__ == "__main__":
    main()
