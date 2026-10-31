#!/usr/bin/env python3
"""One-shot wire: themes / datasets / attachments / boards / scores / schedule for LCD."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
THEME = "Energy Services '26: Land Contract Drillers"
SLUG = "energy-services-26-land-contract-drillers"
GROUP = "Energy Services"
GROUP_SLUG = "energy-services"
PACK = "land_contract_drillers"
FORUMS = "land_contract_drillers_forums"
WTI_PACK = "energy_bottleneck_us_refiners"


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
                "packs": [PACK, FORUMS, WTI_PACK],
                "notes": (
                    "v1: HP NA/Intl Solutions + Saudi/Argentina geo + NBR DS/Intl/"
                    "Rig Tech + PTEN Drilling (ex-Completion) + PDS/ESI.TO/TOT.TO/"
                    "2381.SR + CapEx + drilling PPI/IP + Henry Hub + transcripts + "
                    "Kalshi TX oil + USASpending. Reuses WTI (refiners). BH land "
                    "scrape deferred (403). Forums: oilandgasworkers+PetroleumEngineering. "
                    "Differs from refiners/frac."
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
    catalog = json.loads(
        (ROOT / "config" / "land_contract_drillers_catalog.json").read_text()
    )
    fcatalog = json.loads(
        (ROOT / "config" / "land_contract_drillers_forums_catalog.json").read_text()
    )
    new_ds = []
    for cat, pack in ((catalog, PACK), (fcatalog, FORUMS)):
        for s in cat.get("series") or []:
            did = s.get("dataset_id") or s.get("key")
            if did in ids:
                continue
            row = {
                "id": did,
                "label": s.get("display_name") or s.get("short_name") or did,
                "name": s.get("display_name") or s.get("short_name") or did,
                "description": s.get("official_name") or s.get("label") or did,
                "status": "active",
                "license": "public",
                "provider": s.get("source"),
                "access": s.get("access") or "api",
                "cadence": s.get("cadence") or "weekly",
                "as_of_semantics": s.get("as_of_semantics") or "period_end",
                "pack": pack,
                "series_keys": [s["key"]],
                "source": s.get("source"),
            }
            if s.get("fred_id"):
                row["fred_id"] = s["fred_id"]
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

    def add_att(
        *,
        series_key: str,
        scope: str = "theme",
        ticker=None,
        pack: str | None = None,
        note: str | None = None,
    ) -> bool:
        key = (SLUG, series_key, ticker, scope)
        if key in existing_pairs:
            return False
        row = {
            "scope": scope,
            "theme": THEME,
            "theme_slug": SLUG,
            "ticker": ticker,
            "series_key": series_key,
            "pack": pack
            or (FORUMS if series_key in forum_keys else PACK),
        }
        if note:
            row["note"] = note
        att.setdefault("attachments", []).append(row)
        existing_pairs.add(key)
        return True

    n_att = 0
    for k in pack_keys:
        if add_att(series_key=k):
            n_att += 1
    for k in forum_keys:
        if add_att(series_key=k):
            n_att += 1
    if add_att(
        series_key="wti_crude_ur",
        pack=WTI_PACK,
        note="Reuse energy_bottleneck_us_refiners — WTI E&P budget backdrop",
    ):
        n_att += 1

    ticker_map = {
        "HP": [
            "hp_na_solutions_revenue",
            "hp_international_solutions_revenue",
            "hp_intl_share",
            "hp_saudi_revenue",
            "hp_argentina_revenue",
            "hp_capex",
        ],
        "NBR": [
            "nbr_drilling_solutions_revenue",
            "nbr_international_drilling_revenue",
            "nbr_rig_technologies_revenue",
            "nbr_intl_share",
            "nbr_capex",
        ],
        "PTEN": ["pten_drilling_services_revenue", "pten_capex"],
        "PDS": ["pds_revenue", "pds_capex"],
        "ESI.TO": ["esi_to_revenue"],
        "TOT.TO": ["tot_to_revenue"],
        "2381.SR": ["adro_sr_revenue"],
    }
    for ticker, keys in ticker_map.items():
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
            "ticker": None,
            "recipes": [
                {
                    "chart_type": "stacked_bar",
                    "metric": "raw",
                    "display_name": "Land driller core revenue",
                    "series_keys": [
                        "hp_na_solutions_revenue",
                        "hp_international_solutions_revenue",
                        "nbr_drilling_solutions_revenue",
                        "nbr_international_drilling_revenue",
                        "pten_drilling_services_revenue",
                        "pds_revenue",
                    ],
                    "sum_series_key": "land_driller_core_revenue",
                    "section": "primary",
                    "why_it_matters": (
                        "Core land-drilling revenue (ex-PTEN Completion) is the "
                        "commercial proof of dayrate / fleet absorption."
                    ),
                },
                {
                    "chart_type": "stacked_bar",
                    "metric": "raw",
                    "display_name": "H&P NA vs International Solutions",
                    "series_keys": [
                        "hp_na_solutions_revenue",
                        "hp_international_solutions_revenue",
                    ],
                    "sum_series_key": "hp_solutions_revenue",
                    "section": "primary",
                    "why_it_matters": (
                        "H&P NA Solutions tracks Lower-48 super-spec pricing; "
                        "International tracks NOC / Vaca Muerta backlog."
                    ),
                },
                {
                    "series_key": "hp_intl_share",
                    "metric": "raw",
                    "chart_type": "line",
                    "section": "primary",
                    "why_it_matters": (
                        "Rising H&P International share means NOC backlog is "
                        "carrying the equity story vs cyclical Lower 48."
                    ),
                },
                {
                    "chart_type": "stacked_bar",
                    "metric": "raw",
                    "display_name": "Nabors drilling ops + Rig Tech",
                    "series_keys": [
                        "nbr_drilling_solutions_revenue",
                        "nbr_international_drilling_revenue",
                        "nbr_rig_technologies_revenue",
                    ],
                    "section": "primary",
                    "why_it_matters": (
                        "NBR International is the SANAD / Eastern Hemisphere hero; "
                        "Rig Technologies is the automation monetization leg."
                    ),
                },
                {
                    "series_key": "nbr_intl_share",
                    "metric": "raw",
                    "chart_type": "line",
                    "section": "primary",
                    "why_it_matters": (
                        "Nabors International share of drilling ops tracks "
                        "insulation from Lower-48 capital discipline."
                    ),
                },
                {
                    "series_key": "nbr_rig_technologies_revenue",
                    "metric": "raw",
                    "chart_type": "bar",
                    "section": "primary",
                    "why_it_matters": (
                        "Rig Technologies (Canrig / SmartSLIDE stack) is capital-light "
                        "software/automation revenue — thesis differentiator."
                    ),
                },
                {
                    "chart_type": "stacked_bar",
                    "metric": "raw",
                    "display_name": "H&P Saudi + Argentina",
                    "series_keys": ["hp_saudi_revenue", "hp_argentina_revenue"],
                    "sum_series_key": "hp_intl_noc_geo",
                    "section": "primary",
                    "why_it_matters": (
                        "Saudi + Argentina geo prints are the free proxy for Aramco "
                        "and Vaca Muerta land-drilling demand."
                    ),
                },
                {
                    "series_key": "drilling_services_ppi",
                    "metric": "m3_avg",
                    "chart_type": "line",
                    "section": "primary",
                    "why_it_matters": (
                        "Drilling oil & gas wells PPI is the best free dayrate / "
                        "pricing-power proxy while BH land scrape is deferred."
                    ),
                },
                {
                    "series_key": "drilling_ip",
                    "metric": "m3_avg",
                    "chart_type": "line",
                    "section": "primary",
                    "why_it_matters": (
                        "Industrial production of drilling oil & gas wells tracks "
                        "onshore activity when Baker Hughes weekly is unavailable."
                    ),
                },
                {
                    "series_key": "wti_crude_ur",
                    "metric": "raw",
                    "chart_type": "line",
                    "section": "primary",
                    "why_it_matters": (
                        "WTI (reused) sets E&P cash generation and Lower-48 drilling "
                        "budgets for land contractors."
                    ),
                },
                {
                    "series_key": "henry_hub_ng",
                    "metric": "wma4",
                    "chart_type": "line",
                    "section": "primary",
                    "why_it_matters": (
                        "Henry Hub pulses gas-basin (Haynesville / Montney) land-rig "
                        "demand — the theme's second commodity driver."
                    ),
                },
                {
                    "chart_type": "stacked_bar",
                    "metric": "raw",
                    "display_name": "Land driller CapEx",
                    "series_keys": [
                        "hp_capex",
                        "nbr_capex",
                        "pten_capex",
                        "pds_capex",
                    ],
                    "sum_series_key": "land_driller_capex",
                    "section": "primary",
                    "why_it_matters": (
                        "CapEx tracks super-spec upgrades and international newbuilds "
                        "— the FCF reinvestment bear case."
                    ),
                },
                {
                    "series_key": "land_driller_transcript_keywords",
                    "metric": "raw",
                    "chart_type": "bar",
                    "section": "primary",
                    "why_it_matters": (
                        "Earnings-call hits on FlexRig / PACE-X / SANAD / dayrate "
                        "track management narrative intensity."
                    ),
                },
                {
                    "series_key": "kalshi_tx_oil_prod_2026",
                    "metric": "raw",
                    "chart_type": "line",
                    "section": "primary",
                    "why_it_matters": (
                        "Kalshi odds that Texas 2026 oil averages >5.8 mmbpd are a "
                        "Permian land-drilling demand pulse."
                    ),
                },
                {
                    "series_key": "lcd_land_drilling_talk_mentions",
                    "metric": "raw",
                    "chart_type": "bar",
                    "section": "primary",
                    "display_name": "Forum land drilling talk",
                    "why_it_matters": (
                        "Forum talk on land drilling / dayrates / FlexRig tracks "
                        "field-level sentiment ahead of lagged quarterlies."
                    ),
                },
                {
                    "chart_type": "stacked_bar",
                    "metric": "raw",
                    "display_name": "Forum named / dayrate mentions",
                    "series_keys": [
                        "lcd_hp_flexrig_mentions",
                        "lcd_nabors_pacex_mentions",
                        "lcd_dayrate_util_mentions",
                    ],
                    "section": "primary",
                    "why_it_matters": (
                        "Named contractor vs dayrate/utilization talk separates brand "
                        "attention from pricing/stack chatter."
                    ),
                },
                {
                    "chart_type": "stacked_bar",
                    "metric": "raw",
                    "display_name": "Forum community volume",
                    "series_keys": [
                        "lcd_oilandgasworkers_posts",
                        "lcd_petroleumengineering_posts",
                    ],
                    "section": "primary",
                    "why_it_matters": (
                        "Community volume by sub shows field-hand vs engineering "
                        "discourse concentration."
                    ),
                },
                {
                    "series_key": "lcd_land_drilling_sentiment",
                    "metric": "raw",
                    "chart_type": "line",
                    "section": "primary",
                    "display_name": "Forum land drilling sentiment",
                    "why_it_matters": (
                        "Sentiment on land-drilling posts gauges whether crews see "
                        "dayrates/staffing as improving or deteriorating."
                    ),
                },
                {
                    "series_key": "adro_sr_revenue",
                    "metric": "raw",
                    "chart_type": "bar",
                    "section": "primary",
                    "why_it_matters": (
                        "Arabian Drilling is the listed Saudi land contractor — "
                        "Aramco ecosystem adjacency."
                    ),
                },
                {
                    "series_key": "esi_to_revenue",
                    "metric": "raw",
                    "chart_type": "bar",
                    "section": "primary",
                    "why_it_matters": (
                        "Ensign is the Canadian/international land-drilling adjacency."
                    ),
                },
            ],
        }
    )
    for ticker, recipes in {
        "HP": [
            {
                "series_key": "hp_na_solutions_revenue",
                "metric": "raw",
                "chart_type": "bar",
            },
            {
                "series_key": "hp_international_solutions_revenue",
                "metric": "raw",
                "chart_type": "bar",
            },
            {"series_key": "hp_intl_share", "metric": "raw", "chart_type": "line"},
            {"series_key": "hp_saudi_revenue", "metric": "raw", "chart_type": "bar"},
            {
                "series_key": "hp_argentina_revenue",
                "metric": "raw",
                "chart_type": "bar",
            },
            {"series_key": "hp_capex", "metric": "raw", "chart_type": "bar"},
        ],
        "NBR": [
            {
                "series_key": "nbr_international_drilling_revenue",
                "metric": "raw",
                "chart_type": "bar",
            },
            {
                "series_key": "nbr_drilling_solutions_revenue",
                "metric": "raw",
                "chart_type": "bar",
            },
            {
                "series_key": "nbr_rig_technologies_revenue",
                "metric": "raw",
                "chart_type": "bar",
            },
            {"series_key": "nbr_intl_share", "metric": "raw", "chart_type": "line"},
            {"series_key": "nbr_capex", "metric": "raw", "chart_type": "bar"},
        ],
        "PTEN": [
            {
                "series_key": "pten_drilling_services_revenue",
                "metric": "raw",
                "chart_type": "bar",
            },
            {"series_key": "pten_capex", "metric": "raw", "chart_type": "bar"},
        ],
        "PDS": [
            {"series_key": "pds_revenue", "metric": "raw", "chart_type": "bar"},
            {"series_key": "pds_capex", "metric": "raw", "chart_type": "bar"},
        ],
        "ESI.TO": [
            {"series_key": "esi_to_revenue", "metric": "raw", "chart_type": "bar"},
        ],
        "TOT.TO": [
            {"series_key": "tot_to_revenue", "metric": "raw", "chart_type": "bar"},
        ],
        "2381.SR": [
            {"series_key": "adro_sr_revenue", "metric": "raw", "chart_type": "bar"},
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
            "score_id": "land_contract_drillers_backdrop",
            "label": "Land contract drillers backdrop",
            "pack": PACK,
            "status": "active",
            "signal": {
                "yoy_clip_pct": 25.0,
                "exclude_incomplete_month": True,
                "history_smooth_months": 3,
                "note": (
                    "HP NA/Intl + NBR Intl/Rig Tech + drilling PPI/IP (bull) vs CapEx "
                    "(bear light). WTI via source_pack. Kalshi/forums/wiki chart-only."
                ),
            },
            "components": [
                {
                    "series_key": "hp_na_solutions_revenue",
                    "metric": "yoy_pct",
                    "weight": 0.18,
                    "polarity": 1.0,
                    "enabled": True,
                    "rationale": "H&P NA Solutions → Lower-48 super-spec dayrate absorption.",
                },
                {
                    "series_key": "hp_international_solutions_revenue",
                    "metric": "yoy_pct",
                    "weight": 0.12,
                    "polarity": 1.0,
                    "enabled": True,
                    "rationale": "H&P International → NOC / Vaca Muerta backlog growth.",
                },
                {
                    "series_key": "nbr_international_drilling_revenue",
                    "metric": "yoy_pct",
                    "weight": 0.15,
                    "polarity": 1.0,
                    "enabled": True,
                    "rationale": "Nabors International → SANAD / Eastern Hemisphere backlog.",
                },
                {
                    "series_key": "nbr_rig_technologies_revenue",
                    "metric": "yoy_pct",
                    "weight": 0.1,
                    "polarity": 1.0,
                    "enabled": True,
                    "rationale": "Rig Technologies → automation / software monetization.",
                },
                {
                    "series_key": "pten_drilling_services_revenue",
                    "metric": "yoy_pct",
                    "weight": 0.08,
                    "polarity": 1.0,
                    "enabled": True,
                    "rationale": "PTEN Drilling Services (ex-Completion) → US land fleet.",
                },
                {
                    "series_key": "drilling_services_ppi",
                    "metric": "m3_avg_yoy_pct",
                    "weight": 0.14,
                    "polarity": 1.0,
                    "enabled": True,
                    "rationale": "Drilling PPI rising → dayrate / pricing power.",
                },
                {
                    "series_key": "drilling_ip",
                    "metric": "m3_avg_yoy_pct",
                    "weight": 0.12,
                    "polarity": 1.0,
                    "enabled": True,
                    "rationale": "Drilling IP rising → onshore activity / fleet absorption.",
                },
                {
                    "series_key": "wti_crude_ur",
                    "metric": "yoy_pct",
                    "weight": 0.08,
                    "polarity": 1.0,
                    "enabled": True,
                    "source_pack": WTI_PACK,
                    "rationale": "WTI (reused) → E&P budget / Lower-48 drilling demand.",
                },
                {
                    "series_key": "land_driller_capex",
                    "metric": "yoy_pct",
                    "weight": 0.08,
                    "polarity": -1.0,
                    "enabled": True,
                    "rationale": (
                        "Rising CapEx → fleet reinvestment FCF pressure (bear thesis leg)."
                    ),
                },
                {
                    "series_key": "land_driller_transcript_keywords",
                    "metric": "yoy_pct",
                    "weight": 0.05,
                    "polarity": 1.0,
                    "enabled": True,
                    "rationale": "FlexRig / SANAD / dayrate narrative intensity.",
                },
            ],
            "notes": (
                "Kalshi/forums/wiki/HH/machinery PPI chart-only. CapEx negative = "
                "reinvestment pressure (unlike neoclouds fleet-build bull). "
                "Excludes PTEN Completion."
            ),
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
            "due": {"weekdays": ["Thu"], "max_age_days": 8},
            "freshness": {
                "r2_key": f"themedata/{PACK}/metrics_latest.json",
                "as_of_field": "as_of",
                "local_relative": f"data/{PACK}/latest.json",
            },
            "description": (
                "Weekly land contract drillers — HP/NBR/PTEN segments + drilling "
                "PPI/IP + CapEx + Kalshi TX oil."
            ),
        }
    )
    packs.append(
        {
            "id": FORUMS,
            "job": f"jobs/{FORUMS}.py",
            "enabled": True,
            "hosts": ["vps", "any"],
            "cadence": "weekly",
            "due": {"weekdays": ["Fri"], "max_age_days": 8},
            "freshness": {
                "r2_key": f"themedata/{FORUMS}/metrics_latest.json",
                "as_of_field": "as_of",
                "local_relative": f"data/{FORUMS}/latest.json",
            },
            "description": (
                "Weekly Arctic Shift land-driller forums "
                "(r/oilandgasworkers + PetroleumEngineering)."
            ),
        }
    )
    sched["packs"] = packs
    sched_path.write_text(json.dumps(sched, indent=2) + "\n")
    print("schedule ok")


if __name__ == "__main__":
    main()
