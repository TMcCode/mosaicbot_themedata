#!/usr/bin/env python3
"""One-shot wire: themes / datasets / attachments / boards / scores / schedule for LDES."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
THEME = "Renewable Energy '26: Long-Duration Energy Storage"
SLUG = "renewable-energy-26-long-duration-energy-storage"
GROUP = "Renewable Energy"
GROUP_SLUG = "renewable-energy"
PACK = "ldes"
FORUMS = "ldes_forums"


def main() -> None:
    themes_path = ROOT / "config" / "themes.json"
    themes = json.loads(themes_path.read_text())
    if not any(t.get("theme_slug") == SLUG for t in themes["themes"]):
        themes["themes"].append(
            {
                "theme": THEME,
                "theme_slug": SLUG,
                "group": GROUP,
                "group_slug": GROUP_SLUG,
                "status": "active",
                "stockthemes": True,
                "packs": [PACK, FORUMS],
                "notes": (
                    "v1: FMP BE Product/Install + FLNC Product/Americas + EOSE/LGO/STEM "
                    "+ LDES transcripts + EIA-860M battery MW/duration shares + FRED "
                    "zinc/battery PPI/transformer PPI/DGS10 + USASpending LDES/OCED + "
                    "Kalshi VA/ratepayer. Forums: r/energy+PowerEngineering+RenewableEnergy."
                ),
            }
        )
        themes_path.write_text(json.dumps(themes, indent=2) + "\n")
        print("themes: added")
    else:
        print("themes: exists")

    ds_path = ROOT / "config" / "datasets.json"
    ds = json.loads(ds_path.read_text())
    ids = {d.get("id") for d in ds.get("datasets") or []}
    catalog = json.loads((ROOT / "config" / "ldes_catalog.json").read_text())
    fcatalog = json.loads((ROOT / "config" / "ldes_forums_catalog.json").read_text())
    new_ds = []
    for cat, pack in ((catalog, PACK), (fcatalog, FORUMS)):
        for s in cat.get("series") or []:
            did = s.get("dataset_id") or s.get("key")
            if did in ids:
                continue
            row = {
                "id": did,
                "label": s.get("display_name") or s.get("short_name") or did,
                "description": s.get("official_name") or s.get("label") or did,
                "status": "active",
                "license": "public",
                "access": s.get("access") or "api",
                "cadence": s.get("cadence") or "weekly",
                "as_of_semantics": s.get("as_of_semantics") or "period_end",
                "pack": pack,
                "series_keys": [s["key"]],
                "source": s.get("source"),
            }
            if s.get("fred_id"):
                row["fred_id"] = s["fred_id"]
                row["provider"] = "FRED"
            new_ds.append(row)
            ids.add(did)
    ds.setdefault("datasets", []).extend(new_ds)
    ds_path.write_text(json.dumps(ds, indent=2) + "\n")
    print("datasets added", len(new_ds))

    att_path = ROOT / "config" / "series_attachments.json"
    att = json.loads(att_path.read_text())
    existing_pairs = {
        (a.get("theme_slug"), a.get("series_key"), a.get("ticker"), a.get("scope"))
        for a in att.get("attachments") or []
    }
    forum_keys = {s["key"] for s in fcatalog["series"]}
    pack_keys = {s["key"] for s in catalog["series"]}

    def add_att(*, series_key: str, scope: str = "theme", ticker=None) -> bool:
        key = (SLUG, series_key, ticker, scope)
        if key in existing_pairs:
            return False
        att.setdefault("attachments", []).append(
            {
                "scope": scope,
                "theme": THEME,
                "theme_slug": SLUG,
                "ticker": ticker,
                "series_key": series_key,
                "pack": FORUMS if series_key in forum_keys else PACK,
            }
        )
        existing_pairs.add(key)
        return True

    n_att = 0
    for k in pack_keys:
        if add_att(series_key=k):
            n_att += 1
    for k in forum_keys:
        if add_att(series_key=k):
            n_att += 1
    for ticker, keys in {
        "BE": ["be_product_revenue", "be_installation_revenue"],
        "FLNC": ["flnc_product_revenue", "flnc_americas_revenue"],
        "EOSE": ["eose_revenue", "fred_zinc_price"],
        "LGO": ["lgo_revenue"],
        "STEM": ["stem_service_revenue"],
    }.items():
        for k in keys:
            if add_att(series_key=k, scope="ticker", ticker=ticker):
                n_att += 1
    att_path.write_text(json.dumps(att, indent=2) + "\n")
    print("attachments added", n_att)

    boards_path = ROOT / "config" / "default_boards.json"
    boards = json.loads(boards_path.read_text())
    boards_list = [
        b for b in (boards.get("boards") or []) if b.get("theme_slug") != SLUG
    ]
    boards_list.append(
        {
            "scope": "theme",
            "theme": THEME,
            "theme_slug": SLUG,
            "recipes": [
                {
                    "series_key": "ldes_oem_product_stack",
                    "metric": "yoy_pct",
                    "chart_type": "bar",
                    "section": "primary",
                },
                {
                    "series_key": "be_product_revenue",
                    "metric": "yoy_pct",
                    "chart_type": "bar",
                    "section": "primary",
                },
                {
                    "series_key": "be_installation_revenue",
                    "metric": "yoy_pct",
                    "chart_type": "bar",
                    "section": "primary",
                },
                {
                    "series_key": "flnc_product_revenue",
                    "metric": "yoy_pct",
                    "chart_type": "bar",
                    "section": "primary",
                },
                {
                    "series_key": "flnc_americas_revenue",
                    "metric": "yoy_pct",
                    "chart_type": "bar",
                    "section": "primary",
                },
                {
                    "series_key": "eose_revenue",
                    "metric": "yoy_pct",
                    "chart_type": "bar",
                    "section": "primary",
                },
                {
                    "series_key": "ldes_transcript_keywords",
                    "metric": "raw",
                    "chart_type": "bar",
                    "section": "primary",
                },
                {
                    "series_key": "eia_battery_operating_mw",
                    "metric": "m3_avg_yoy_pct",
                    "chart_type": "bar",
                    "section": "primary",
                },
                {
                    "series_key": "eia_battery_share_ge_4h_pct",
                    "metric": "raw",
                    "chart_type": "line",
                    "section": "primary",
                },
                {
                    "series_key": "eia_battery_share_ge_6h_pct",
                    "metric": "raw",
                    "chart_type": "line",
                    "section": "primary",
                },
                {
                    "series_key": "fred_zinc_price",
                    "metric": "m3_avg_yoy_pct",
                    "chart_type": "line",
                    "section": "primary",
                },
                {
                    "series_key": "fred_storage_battery_ppi",
                    "metric": "m3_avg_yoy_pct",
                    "chart_type": "line",
                    "section": "primary",
                },
                {
                    "series_key": "fred_transformer_ppi",
                    "metric": "m3_avg_yoy_pct",
                    "chart_type": "line",
                    "section": "primary",
                },
                {
                    "series_key": "usaspending_ldes_oced",
                    "metric": "m3_avg",
                    "chart_type": "bar",
                    "section": "primary",
                },
                {
                    "series_key": "kalshi_va_data_center_clean_energy",
                    "metric": "raw",
                    "chart_type": "line",
                    "section": "primary",
                },
                {
                    "series_key": "kalshi_dc_ratepayer_power_cost",
                    "metric": "raw",
                    "chart_type": "line",
                    "section": "primary",
                },
                {
                    "series_key": "ldes_category_talk_mentions",
                    "metric": "raw",
                    "chart_type": "bar",
                    "section": "primary",
                },
                {
                    "chart_type": "stacked_bar",
                    "metric": "raw",
                    "display_name": "Forum LDES product mentions",
                    "series_keys": [
                        "ldes_smartstack_mentions",
                        "ldes_eose_mentions",
                        "ldes_bloom_mentions",
                        "ldes_fluence_mentions",
                    ],
                    "section": "primary",
                },
                {
                    "chart_type": "stacked_bar",
                    "metric": "raw",
                    "display_name": "Forum community volume",
                    "series_keys": [
                        "ldes_energy_sub_posts",
                        "ldes_powereng_sub_posts",
                        "ldes_renewable_sub_posts",
                    ],
                    "section": "primary",
                },
                {
                    "series_key": "ldes_product_talk_sentiment",
                    "metric": "raw",
                    "chart_type": "line",
                    "section": "primary",
                },
                {
                    "series_key": "fred_dgs10",
                    "metric": "raw",
                    "chart_type": "line",
                    "section": "primary",
                },
                {
                    "series_key": "lgo_revenue",
                    "metric": "yoy_pct",
                    "chart_type": "bar",
                    "section": "primary",
                },
                {
                    "series_key": "stem_service_revenue",
                    "metric": "yoy_pct",
                    "chart_type": "bar",
                    "section": "primary",
                },
            ],
        }
    )
    for ticker, recipes in {
        "BE": [
            {
                "series_key": "be_product_revenue",
                "metric": "yoy_pct",
                "chart_type": "bar",
            },
            {
                "series_key": "be_installation_revenue",
                "metric": "yoy_pct",
                "chart_type": "bar",
            },
        ],
        "FLNC": [
            {
                "series_key": "flnc_product_revenue",
                "metric": "yoy_pct",
                "chart_type": "bar",
            },
            {
                "series_key": "flnc_americas_revenue",
                "metric": "yoy_pct",
                "chart_type": "bar",
            },
        ],
        "EOSE": [
            {"series_key": "eose_revenue", "metric": "yoy_pct", "chart_type": "bar"},
            {
                "series_key": "fred_zinc_price",
                "metric": "m3_avg_yoy_pct",
                "chart_type": "line",
            },
        ],
    }.items():
        boards_list.append(
            {
                "scope": "ticker",
                "theme": THEME,
                "theme_slug": SLUG,
                "ticker": ticker,
                "recipes": recipes,
            }
        )
    boards["boards"] = boards_list
    boards_path.write_text(json.dumps(boards, indent=2) + "\n")
    print("boards ok")

    scores_path = ROOT / "config" / "theme_scores.json"
    scores = json.loads(scores_path.read_text())
    scores["themes"] = [
        t for t in scores.get("themes") or [] if t.get("theme_slug") != SLUG
    ]
    if "scores" in scores:
        scores["scores"] = [
            t for t in scores.get("scores") or [] if t.get("theme_slug") != SLUG
        ]
    scores.setdefault("themes", []).append(
        {
            "theme": THEME,
            "theme_slug": SLUG,
            "score_id": "ldes_backdrop",
            "label": "Long-duration energy storage backdrop",
            "pack": PACK,
            "status": "active",
            "signal": {
                "yoy_clip_pct": 20.0,
                "exclude_incomplete_month": True,
                "history_smooth_months": 3,
                "note": "OEM product + EIA duration + zinc/battery cost scissors + transcripts + federal LDES awards.",
            },
            "components": [
                {
                    "series_key": "ldes_oem_product_stack",
                    "metric": "yoy_pct",
                    "weight": 0.25,
                    "polarity": 1.0,
                    "enabled": True,
                    "rationale": "BE+FLNC+EOSE product/revenue growth → commercial LDES conversion.",
                },
                {
                    "series_key": "be_product_revenue",
                    "metric": "yoy_pct",
                    "weight": 0.12,
                    "polarity": 1.0,
                    "enabled": True,
                    "rationale": "Bloom Energy Server / data-center firming hardware.",
                },
                {
                    "series_key": "flnc_americas_revenue",
                    "metric": "yoy_pct",
                    "weight": 0.12,
                    "polarity": 1.0,
                    "enabled": True,
                    "rationale": "Fluence Americas — US data-center / PJM-ERCOT exposure.",
                },
                {
                    "series_key": "eose_revenue",
                    "metric": "yoy_pct",
                    "weight": 0.1,
                    "polarity": 1.0,
                    "enabled": True,
                    "rationale": "Eos zinc Z3 OEM ramp.",
                },
                {
                    "series_key": "ldes_transcript_keywords",
                    "metric": "yoy_pct",
                    "weight": 0.1,
                    "polarity": 1.0,
                    "enabled": True,
                    "rationale": "Data-center / LDES / Smartstack narrative intensity.",
                },
                {
                    "series_key": "eia_battery_operating_mw",
                    "metric": "m3_avg_yoy_pct",
                    "weight": 0.1,
                    "polarity": 1.0,
                    "enabled": True,
                    "rationale": "US utility-scale battery fleet expansion.",
                },
                {
                    "series_key": "eia_battery_share_ge_4h_pct",
                    "metric": "yoy_pct",
                    "weight": 0.08,
                    "polarity": 1.0,
                    "enabled": True,
                    "rationale": "Operating fleet shifting into multi-hour duration.",
                },
                {
                    "series_key": "fred_zinc_price",
                    "metric": "m3_avg_yoy_pct",
                    "weight": 0.05,
                    "polarity": -1.0,
                    "enabled": True,
                    "rationale": "Zinc BOM up → Eos margin pressure.",
                },
                {
                    "series_key": "fred_storage_battery_ppi",
                    "metric": "m3_avg_yoy_pct",
                    "weight": 0.05,
                    "polarity": 1.0,
                    "enabled": True,
                    "rationale": "Rising battery PPI slows LFP duration-creep into LDES window.",
                },
                {
                    "series_key": "usaspending_ldes_oced",
                    "metric": "m3_avg_yoy_pct",
                    "weight": 0.03,
                    "polarity": 1.0,
                    "enabled": True,
                    "rationale": "Federal LDES/OCED award pulse (soft).",
                },
            ],
            "notes": "Forums + Kalshi + DGS10 + transformer PPI chart-only.",
        }
    )
    scores_path.write_text(json.dumps(scores, indent=2) + "\n")
    print("scores ok")

    sched_path = ROOT / "config" / "update_schedule.json"
    sched = json.loads(sched_path.read_text())
    packs = [
        p for p in (sched.get("packs") or []) if p.get("id") not in (PACK, FORUMS)
    ]
    packs.append(
        {
            "id": PACK,
            "job": f"jobs/{PACK}.py",
            "enabled": True,
            "hosts": ["vps", "any"],
            "cadence": "weekly",
            "due": {"weekdays": ["Tue"], "max_age_days": 10},
            "freshness": {
                "r2_key": f"themedata/{PACK}/metrics_latest.json",
                "as_of_field": "as_of",
                "local_relative": f"data/{PACK}/latest.json",
            },
            "description": "LDES — FMP OEM segments + EIA-860M duration + FRED zinc/PPI + USASpending + Kalshi.",
        }
    )
    packs.append(
        {
            "id": FORUMS,
            "job": f"jobs/{FORUMS}.py",
            "enabled": True,
            "hosts": ["vps", "any"],
            "cadence": "weekly",
            "due": {"weekdays": ["Wed"], "max_age_days": 8},
            "freshness": {
                "r2_key": f"themedata/{FORUMS}/metrics_latest.json",
                "as_of_field": "as_of",
                "local_relative": f"data/{FORUMS}/latest.json",
            },
            "description": "Weekly Arctic Shift LDES forums (r/energy, PowerEngineering, RenewableEnergy).",
        }
    )
    sched["packs"] = packs
    sched_path.write_text(json.dumps(sched, indent=2) + "\n")
    print("schedule ok")


if __name__ == "__main__":
    main()
