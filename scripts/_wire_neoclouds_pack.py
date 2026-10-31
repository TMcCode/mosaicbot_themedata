#!/usr/bin/env python3
"""One-shot wire: themes / datasets / attachments / boards / scores / schedule for Neoclouds."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
THEME = "AI '26: Neoclouds"
SLUG = "ai-26-neoclouds"
GROUP = "AI"
GROUP_SLUG = "ai"
PACK = "neoclouds"
FORUMS = "neoclouds_forums"


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
                "packs": [PACK, FORUMS, "optical_fiber"],
                "notes": (
                    "v1: CRWV/NBIS/APLD (+ DOCN) revenue + HUT HPC/colo + IREN AI Cloud "
                    "share + CBRS P/S + CapEx/debt + transcripts + Kalshi CRWV power / "
                    "ratepayer / ERCOT + DGS10 + wiki. Reuses hyperscaler CapEx "
                    "(optical_fiber). Forums: r/datacenter+nvidia+LocalLLaMA. Differs "
                    "from optical/HBM/photonics (rents GPUs / owns MW)."
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
    catalog = json.loads((ROOT / "config" / "neoclouds_catalog.json").read_text())
    fcatalog = json.loads(
        (ROOT / "config" / "neoclouds_forums_catalog.json").read_text()
    )
    new_ds = []
    for cat, pack in ((catalog, PACK), (fcatalog, FORUMS)):
        for s in cat.get("series") or []:
            did = s.get("dataset_id") or s.get("key")
            if did in ids:
                # Reuse existing id (e.g. treasury_10y) — still stamp pack if missing.
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
        series_key="hyperscaler_capex",
        pack="optical_fiber",
        note="Reuse optical_fiber — hyperscaler CapEx backdrop",
    ):
        n_att += 1

    ticker_map = {
        "CRWV": [
            "crwv_revenue",
            "crwv_capex",
            "crwv_total_debt",
            "kalshi_crwv_contracted_power",
        ],
        "NBIS": ["nbis_revenue", "nbis_capex", "nbis_total_debt"],
        "APLD": ["apld_revenue", "apld_capex", "apld_total_debt"],
        "DOCN": ["docn_revenue"],
        "HUT": ["hut_hpc_cloud_revenue", "hut_capex"],
        "IREN": ["iren_ai_cloud_revenue", "iren_ai_cloud_share", "iren_capex"],
        "WULF": ["wulf_revenue", "wulf_capex"],
        "CBRS": ["cbrs_product_revenue", "cbrs_service_revenue"],
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
                    "display_name": "Neocloud pure-play revenue",
                    "series_keys": [
                        "crwv_revenue",
                        "nbis_revenue",
                        "apld_revenue",
                    ],
                    "sum_series_key": "neocloud_pureplay_revenue",
                    "section": "primary",
                    "why_it_matters": (
                        "CRWV+NBIS+APLD revenue is the commercial proof that GPU-cloud "
                        "pure-plays are scaling — the anchor versus optical/HBM picks."
                    ),
                },
                {
                    "series_key": "hut_hpc_cloud_revenue",
                    "metric": "raw",
                    "chart_type": "bar",
                    "section": "primary",
                    "why_it_matters": (
                        "Hut 8 HPC/colo/cloud is the cleanest public miner→AI hosting "
                        "breakout in FMP segments."
                    ),
                },
                {
                    "series_key": "iren_ai_cloud_share",
                    "metric": "raw",
                    "chart_type": "line",
                    "section": "primary",
                    "why_it_matters": (
                        "IREN AI Cloud share of revenue tracks the mining→AI pivot — "
                        "rising share means AI hosting drives the equity story."
                    ),
                },
                {
                    "chart_type": "stacked_bar",
                    "metric": "raw",
                    "display_name": "Cerebras Product vs Service",
                    "series_keys": [
                        "cbrs_product_revenue",
                        "cbrs_service_revenue",
                    ],
                    "section": "primary",
                    "why_it_matters": (
                        "CBRS Product vs Service mix shows wafer-scale chip sales versus "
                        "inference/cloud services — thin history but thesis-relevant."
                    ),
                },
                {
                    "chart_type": "stacked_bar",
                    "metric": "raw",
                    "display_name": "Neocloud CapEx",
                    "series_keys": [
                        "crwv_capex",
                        "nbis_capex",
                        "apld_capex",
                        "iren_capex",
                        "wulf_capex",
                        "hut_capex",
                    ],
                    "sum_series_key": "neocloud_capex",
                    "section": "primary",
                    "why_it_matters": (
                        "CapEx intensity is the fleet-build thesis — GPU purchases and "
                        "data-center energization spend ahead of contracted MW."
                    ),
                },
                {
                    "series_key": "hyperscaler_capex",
                    "metric": "raw",
                    "chart_type": "bar",
                    "section": "primary",
                    "why_it_matters": (
                        "Hyperscaler CapEx (reused) is the demand backdrop neoclouds "
                        "overflow into when cloud GPU capacity is scarce."
                    ),
                },
                {
                    "chart_type": "stacked_bar",
                    "metric": "raw",
                    "display_name": "Neocloud total debt",
                    "series_keys": [
                        "crwv_total_debt",
                        "nbis_total_debt",
                        "apld_total_debt",
                    ],
                    "sum_series_key": "neocloud_total_debt",
                    "section": "primary",
                    "why_it_matters": (
                        "Debt-funded GPU fleets — refinancing / rate risk pulse beside CapEx."
                    ),
                },
                {
                    "series_key": "neocloud_transcript_keywords",
                    "metric": "raw",
                    "chart_type": "bar",
                    "section": "primary",
                    "why_it_matters": (
                        "Earnings-call hits on take-or-pay, energized MW, and Blackwell "
                        "track management narrative intensity."
                    ),
                },
                {
                    "series_key": "kalshi_crwv_contracted_power",
                    "metric": "raw",
                    "chart_type": "line",
                    "section": "primary",
                    "why_it_matters": (
                        "Kalshi odds that CoreWeave hits >4.6 GW contracted power are a "
                        "capacity-delivery pulse for the neocloud energization story."
                    ),
                },
                {
                    "series_key": "kalshi_dc_ratepayer_jul2027",
                    "metric": "raw",
                    "chart_type": "line",
                    "section": "primary",
                    "why_it_matters": (
                        "Rising odds of DC ratepayer power-cost standards raise operating "
                        "cost risk for power-hungry GPU fleets."
                    ),
                },
                {
                    "series_key": "kalshi_ercot_peak_demand",
                    "metric": "raw",
                    "chart_type": "line",
                    "section": "primary",
                    "why_it_matters": (
                        "ERCOT peak-load odds proxy Texas grid stress relevant to IREN "
                        "and other Texas AI hosting sites."
                    ),
                },
                {
                    "series_key": "treasury_10y",
                    "metric": "wma4",
                    "chart_type": "line",
                    "section": "primary",
                    "why_it_matters": (
                        "10Y Treasury sets the cost of capital for debt-funded GPU fleets."
                    ),
                },
                {
                    "series_key": "nc_gpu_cloud_talk_mentions",
                    "metric": "raw",
                    "chart_type": "bar",
                    "section": "primary",
                    "display_name": "Forum GPU cloud / neocloud talk",
                    "why_it_matters": (
                        "Forum talk on GPU cloud / CoreWeave / take-or-pay tracks retail "
                        "and developer attention ahead of lagged quarterly prints."
                    ),
                },
                {
                    "chart_type": "stacked_bar",
                    "metric": "raw",
                    "display_name": "Forum named provider mentions",
                    "series_keys": [
                        "nc_coreweave_mentions",
                        "nc_nebius_mentions",
                        "nc_power_interconnect_mentions",
                    ],
                    "section": "primary",
                    "why_it_matters": (
                        "Named provider vs power/interconnect talk separates brand "
                        "attention from grid-bottleneck discourse."
                    ),
                },
                {
                    "chart_type": "stacked_bar",
                    "metric": "raw",
                    "display_name": "Forum community volume",
                    "series_keys": [
                        "nc_datacenter_posts",
                        "nc_nvidia_posts",
                        "nc_localllama_posts",
                    ],
                    "section": "primary",
                    "why_it_matters": (
                        "Community volume by sub shows whether discourse concentrates in "
                        "ops (datacenter), GPU (nvidia), or developer rental (LocalLLaMA)."
                    ),
                },
                {
                    "series_key": "nc_gpu_cloud_sentiment",
                    "metric": "raw",
                    "chart_type": "line",
                    "section": "primary",
                    "display_name": "Forum GPU cloud sentiment",
                    "why_it_matters": (
                        "Sentiment on GPU-cloud posts gauges whether participants see "
                        "capacity/pricing as improving or deteriorating."
                    ),
                },
                {
                    "series_key": "docn_revenue",
                    "metric": "raw",
                    "chart_type": "bar",
                    "section": "primary",
                    "why_it_matters": (
                        "DigitalOcean is adjacent developer cloud — soft comparison, not "
                        "GPU-pure."
                    ),
                },
                {
                    "series_key": "wulf_revenue",
                    "metric": "raw",
                    "chart_type": "bar",
                    "section": "primary",
                    "why_it_matters": (
                        "TeraWulf total revenue is a miner-pivot adjacency read beside "
                        "HUT/IREN segment breakouts."
                    ),
                },
            ],
        }
    )
    for ticker, recipes in {
        "CRWV": [
            {"series_key": "crwv_revenue", "metric": "raw", "chart_type": "bar"},
            {"series_key": "crwv_capex", "metric": "raw", "chart_type": "bar"},
            {"series_key": "crwv_total_debt", "metric": "raw", "chart_type": "bar"},
            {
                "series_key": "kalshi_crwv_contracted_power",
                "metric": "raw",
                "chart_type": "line",
            },
        ],
        "NBIS": [
            {"series_key": "nbis_revenue", "metric": "raw", "chart_type": "bar"},
            {"series_key": "nbis_capex", "metric": "raw", "chart_type": "bar"},
            {"series_key": "nbis_total_debt", "metric": "raw", "chart_type": "bar"},
        ],
        "APLD": [
            {"series_key": "apld_revenue", "metric": "raw", "chart_type": "bar"},
            {"series_key": "apld_capex", "metric": "raw", "chart_type": "bar"},
            {"series_key": "apld_total_debt", "metric": "raw", "chart_type": "bar"},
        ],
        "HUT": [
            {
                "series_key": "hut_hpc_cloud_revenue",
                "metric": "raw",
                "chart_type": "bar",
            },
            {"series_key": "hut_capex", "metric": "raw", "chart_type": "bar"},
        ],
        "IREN": [
            {
                "series_key": "iren_ai_cloud_revenue",
                "metric": "raw",
                "chart_type": "bar",
            },
            {
                "series_key": "iren_ai_cloud_share",
                "metric": "raw",
                "chart_type": "line",
            },
            {"series_key": "iren_capex", "metric": "raw", "chart_type": "bar"},
        ],
        "WULF": [
            {"series_key": "wulf_revenue", "metric": "raw", "chart_type": "bar"},
            {"series_key": "wulf_capex", "metric": "raw", "chart_type": "bar"},
        ],
        "CBRS": [
            {
                "series_key": "cbrs_product_revenue",
                "metric": "raw",
                "chart_type": "bar",
            },
            {
                "series_key": "cbrs_service_revenue",
                "metric": "raw",
                "chart_type": "bar",
            },
        ],
        "DOCN": [
            {"series_key": "docn_revenue", "metric": "raw", "chart_type": "bar"},
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
            "score_id": "neoclouds_backdrop",
            "label": "Neoclouds backdrop",
            "pack": PACK,
            "status": "active",
            "signal": {
                "yoy_clip_pct": 25.0,
                "exclude_incomplete_month": True,
                "history_smooth_months": 3,
                "note": (
                    "Pure-play revenue + HUT HPC + CapEx + transcripts + hyperscaler "
                    "CapEx reuse. Kalshi/debt/DGS10/forums/wiki chart-only."
                ),
            },
            "components": [
                {
                    "series_key": "neocloud_pureplay_revenue",
                    "metric": "yoy_pct",
                    "weight": 0.28,
                    "polarity": 1.0,
                    "enabled": True,
                    "rationale": (
                        "CRWV+NBIS+APLD revenue growth → commercial GPU-cloud proof."
                    ),
                },
                {
                    "series_key": "hut_hpc_cloud_revenue",
                    "metric": "yoy_pct",
                    "weight": 0.15,
                    "polarity": 1.0,
                    "enabled": True,
                    "rationale": "Hut 8 HPC/colo/cloud → miner→AI hosting conversion.",
                },
                {
                    "series_key": "iren_ai_cloud_revenue",
                    "metric": "yoy_pct",
                    "weight": 0.1,
                    "polarity": 1.0,
                    "enabled": True,
                    "rationale": "IREN AI Cloud annual revenue → AI hosting ramp.",
                },
                {
                    "series_key": "neocloud_capex",
                    "metric": "yoy_pct",
                    "weight": 0.15,
                    "polarity": 1.0,
                    "enabled": True,
                    "rationale": "Rising CapEx → fleet/DC build intensity (growth).",
                },
                {
                    "series_key": "neocloud_transcript_keywords",
                    "metric": "yoy_pct",
                    "weight": 0.1,
                    "polarity": 1.0,
                    "enabled": True,
                    "rationale": (
                        "Take-or-pay / energized MW / Blackwell narrative intensity."
                    ),
                },
                {
                    "series_key": "hyperscaler_capex",
                    "metric": "yoy_pct",
                    "weight": 0.12,
                    "polarity": 1.0,
                    "enabled": True,
                    "source_pack": "optical_fiber",
                    "rationale": (
                        "Hyperscaler CapEx (reused) → overflow demand for neocloud GPUs."
                    ),
                },
                {
                    "series_key": "cbrs_service_revenue",
                    "metric": "yoy_pct",
                    "weight": 0.05,
                    "polarity": 1.0,
                    "enabled": True,
                    "rationale": "Cerebras Service → wafer-scale inference/cloud adjacency.",
                },
                {
                    "series_key": "wiki_coreweave_pageviews",
                    "metric": "yoy_pct",
                    "weight": 0.05,
                    "polarity": 1.0,
                    "enabled": True,
                    "rationale": "CoreWeave wiki attention → soft retail narrative amp.",
                },
            ],
            "notes": (
                "Kalshi/debt/DGS10/forums chart-only. CapEx rising = bullish fleet build "
                "(not cost drag). Debt ambiguous — chart primary."
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
            "due": {"weekdays": ["Tue"], "max_age_days": 8},
            "freshness": {
                "r2_key": f"themedata/{PACK}/metrics_latest.json",
                "as_of_field": "as_of",
                "local_relative": f"data/{PACK}/latest.json",
            },
            "description": (
                "Weekly neoclouds — CRWV/NBIS/APLD/HUT/IREN/CBRS + CapEx/debt + "
                "Kalshi power/ratepayer/ERCOT + DGS10."
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
            "due": {"weekdays": ["Wed"], "max_age_days": 8},
            "freshness": {
                "r2_key": f"themedata/{FORUMS}/metrics_latest.json",
                "as_of_field": "as_of",
                "local_relative": f"data/{FORUMS}/latest.json",
            },
            "description": (
                "Weekly Arctic Shift neoclouds forums "
                "(r/datacenter + nvidia + LocalLLaMA)."
            ),
        }
    )
    sched["packs"] = packs
    sched_path.write_text(json.dumps(sched, indent=2) + "\n")
    print("schedule ok")


if __name__ == "__main__":
    main()
