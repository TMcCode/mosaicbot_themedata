#!/usr/bin/env python3
"""One-shot wire: themes / datasets / attachments / boards / scores / schedule."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
THEME = "Enterprise Software '24: Data Analytics & BI Software"
SLUG = "enterprise-software-24-data-analytics-bi-software"
GROUP = "Enterprise Software"
GROUP_SLUG = "enterprise-software"
PACK = "data_analytics_bi"
FORUMS = "data_analytics_bi_forums"

REUSE = [
    (
        "crm_integration_analytics_revenue",
        "seat_based_pricing",
        "Reuse seat_based CRM Integration & Analytics — Tableau/analytics franchise",
    ),
    (
        "software_ipp_investment",
        "ai_resilient_software",
        "Reuse ai_resilient software IPP investment",
    ),
    (
        "ramp_ai_adoption",
        "ai_resilient_software",
        "Reuse ai_resilient Ramp AI adoption",
    ),
]


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
                    "v1: PLTR Commercial/Government + SAP Cloud + AMPL/SMWB/DOMO "
                    "pure-plays + JP BI adjacency + analytics/agentic transcripts + "
                    "SO Tableau/Power BI/Amplitude + USASpending Palantir/analytics + "
                    "Kalshi KXAIBILL + wiki. Reuses CRM Integration & Analytics "
                    "(seat_based) + software IPP / Ramp AI (ai_resilient). Forums: "
                    "dataengineering+businessintelligence+PowerBI. Differs from "
                    "seat-based seats and devops OTel/K8s."
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
    catalog = json.loads((ROOT / "config" / "data_analytics_bi_catalog.json").read_text())
    fcatalog = json.loads(
        (ROOT / "config" / "data_analytics_bi_forums_catalog.json").read_text()
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
        series_key: str | None,
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
            or (
                FORUMS
                if series_key in forum_keys
                else PACK
            ),
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
    for series_key, pack, note in REUSE:
        if add_att(series_key=series_key, pack=pack, note=note):
            n_att += 1

    ticker_map = {
        "PLTR": [
            "pltr_commercial_revenue",
            "pltr_government_revenue",
            "pltr_revenue",
            "pltr_commercial_share",
        ],
        "SAP": ["sap_cloud_revenue"],
        "AMPL": ["ampl_revenue"],
        "SMWB": ["smwb_revenue"],
        "DOMO": ["domo_subscription_revenue", "domo_revenue"],
        "HUCK": ["domo_subscription_revenue", "domo_revenue"],
        "3655.T": ["brainpad_3655_revenue"],
        "4422.T": ["valuenex_4422_revenue"],
        "4432.T": ["wingarc_4432_revenue"],
        "CRM": ["crm_integration_analytics_revenue"],
    }
    for ticker, keys in ticker_map.items():
        for k in keys:
            pack = (
                "seat_based_pricing"
                if k == "crm_integration_analytics_revenue"
                else None
            )
            if add_att(series_key=k, scope="ticker", ticker=ticker, pack=pack):
                n_att += 1
    for ticker in ("MSFT",):
        if add_att(
            series_key=None,
            scope="membership",
            ticker=ticker,
            note="Theme membership — Power BI/Fabric via transcripts / theme charts",
        ):
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
                    "display_name": "Palantir Commercial + Government",
                    "series_keys": [
                        "pltr_commercial_revenue",
                        "pltr_government_revenue",
                    ],
                    "sum_series_key": "pltr_segment_stack",
                    "section": "primary",
                    "why_it_matters": (
                        "Commercial vs Government splits the sovereignty / Foundry "
                        "enterprise expansion story from USG Gotham concentration."
                    ),
                },
                {
                    "series_key": "crm_integration_analytics_revenue",
                    "metric": "raw",
                    "chart_type": "bar",
                    "section": "primary",
                    "why_it_matters": (
                        "Salesforce Integration & Analytics is the cleanest public "
                        "Tableau / MuleSoft-adjacent franchise print (reused from "
                        "seat-based)."
                    ),
                },
                {
                    "series_key": "sap_cloud_revenue",
                    "metric": "raw",
                    "chart_type": "bar",
                    "section": "primary",
                    "why_it_matters": (
                        "SAP Cloud is the Joule / Business Data Cloud monetization "
                        "proxy where FMP has no BI-only cut."
                    ),
                },
                {
                    "chart_type": "stacked_bar",
                    "metric": "raw",
                    "display_name": "Analytics pure-play revenue",
                    "series_keys": [
                        "ampl_revenue",
                        "smwb_revenue",
                        "domo_revenue",
                    ],
                    "sum_series_key": "bi_pureplay_revenue",
                    "section": "primary",
                    "why_it_matters": (
                        "AMPL + SMWB + DOMO are the public product-analytics / "
                        "digital-intel / BI pure-plays (ex CRM/MSFT/SAP mega-blobs)."
                    ),
                },
                {
                    "series_key": "pltr_commercial_share",
                    "metric": "raw",
                    "chart_type": "line",
                    "section": "primary",
                    "why_it_matters": (
                        "Rising Commercial share of Palantir revenue confirms "
                        "enterprise expansion beyond government concentration."
                    ),
                },
                {
                    "series_key": "analytics_bi_transcripts",
                    "metric": "raw",
                    "chart_type": "bar",
                    "section": "primary",
                    "why_it_matters": (
                        "Earnings-call hits on Tableau / Power BI / Fabric / "
                        "Foundry / Agentforce / Joule proxy MSFT and CRM analytics "
                        "narrative where FMP segments fail."
                    ),
                },
                {
                    "chart_type": "stacked_bar",
                    "metric": "raw",
                    "display_name": "Stack Overflow BI/analytics questions",
                    "series_keys": [
                        "so_tableau_questions",
                        "so_powerbi_questions",
                        "so_amplitude_questions",
                    ],
                    "sum_series_key": "so_bi_tag_stack",
                    "section": "primary",
                    "why_it_matters": (
                        "Practitioner question volume on Tableau / Power BI / "
                        "Amplitude is the free developer-adoption pulse (not OTel)."
                    ),
                },
                {
                    "series_key": "software_ipp_investment",
                    "metric": "yoy_pct",
                    "chart_type": "bar",
                    "section": "primary",
                    "why_it_matters": (
                        "US software IPP investment is the macro budget envelope "
                        "for analytics platforms (reused from ai_resilient)."
                    ),
                },
                {
                    "series_key": "ramp_ai_adoption",
                    "metric": "raw",
                    "chart_type": "line",
                    "section": "primary",
                    "why_it_matters": (
                        "Ramp AI adoption is a high-frequency read on agentic "
                        "tooling demand that lifts analytics / outcome pricing."
                    ),
                },
                {
                    "series_key": "usaspending_palantir_analytics",
                    "metric": "m3_avg",
                    "chart_type": "bar",
                    "section": "primary",
                    "why_it_matters": (
                        "Federal Palantir / Foundry / analytics obligations are "
                        "the free gov demand pulse for the sovereignty thesis."
                    ),
                },
                {
                    "series_key": "kalshi_federal_ai_legislation",
                    "metric": "raw",
                    "chart_type": "line",
                    "section": "primary",
                    "why_it_matters": (
                        "Kalshi odds of federal AI legislation price compliance / "
                        "sovereignty policy risk for analytics platforms."
                    ),
                },
                {
                    "series_key": "bi_talk_mentions",
                    "metric": "raw",
                    "chart_type": "bar",
                    "section": "primary",
                    "display_name": "Forum BI/analytics talk",
                    "why_it_matters": (
                        "Reddit BI/analytics talk on dataeng / BI / PowerBI tracks "
                        "practitioner attention ahead of lagged quarterlies."
                    ),
                },
                {
                    "chart_type": "stacked_bar",
                    "metric": "raw",
                    "display_name": "Forum named product mentions",
                    "series_keys": [
                        "bi_tableau_mentions",
                        "bi_powerbi_mentions",
                        "bi_amplitude_mentions",
                        "bi_palantir_mentions",
                    ],
                    "section": "primary",
                    "why_it_matters": (
                        "Named Tableau / Power BI / Amplitude / Palantir chatter "
                        "separates franchise attention in practitioner forums."
                    ),
                },
                {
                    "chart_type": "stacked_bar",
                    "metric": "raw",
                    "display_name": "Forum community volume",
                    "series_keys": [
                        "bi_dataengineering_sub_posts",
                        "bi_businessintelligence_sub_posts",
                        "bi_powerbi_sub_posts",
                    ],
                    "section": "primary",
                    "why_it_matters": (
                        "Community volume across dataeng / BI / PowerBI is the "
                        "umbrella practitioner traffic backdrop."
                    ),
                },
            ],
        }
    )
    for ticker, keys in (
        ("PLTR", ["pltr_commercial_revenue", "pltr_government_revenue", "pltr_revenue"]),
        ("SAP", ["sap_cloud_revenue"]),
        ("AMPL", ["ampl_revenue"]),
        ("SMWB", ["smwb_revenue"]),
        ("DOMO", ["domo_subscription_revenue", "domo_revenue"]),
        ("HUCK", ["domo_subscription_revenue", "domo_revenue"]),
        ("CRM", ["crm_integration_analytics_revenue"]),
        ("3655.T", ["brainpad_3655_revenue"]),
        ("4422.T", ["valuenex_4422_revenue"]),
        ("4432.T", ["wingarc_4432_revenue"]),
    ):
        boards_list.append(
            {
                "scope": "ticker",
                "theme": THEME,
                "theme_slug": SLUG,
                "ticker": ticker,
                "recipes": [
                    {
                        "series_key": k,
                        "metric": "raw",
                        "chart_type": "bar",
                    }
                    for k in keys
                ],
            }
        )
    boards["boards"] = boards_list
    boards_path.write_text(json.dumps(boards, indent=2) + "\n")
    print("boards: rewritten for theme")

    score_recipe = {
        "theme": THEME,
        "theme_slug": SLUG,
        "score_id": "data_analytics_bi_backdrop",
        "label": "Data analytics & BI backdrop",
        "pack": PACK,
        "status": "active",
        "signal": {
            "yoy_clip_pct": 20.0,
            "exclude_incomplete_month": True,
            "history_smooth_months": 3,
            "note": (
                "PLTR Commercial + CRM Integration & Analytics + SAP Cloud + "
                "pure-play sum + software IPP + Ramp AI; USASpending soft. "
                "Kalshi/forums/wiki/transcripts/SO chart-only."
            ),
        },
        "components": [
            {
                "series_key": "pltr_commercial_revenue",
                "metric": "yoy_pct",
                "weight": 0.2,
                "polarity": 1.0,
                "enabled": True,
                "rationale": "PLTR Commercial rising → enterprise Foundry/AIP demand.",
            },
            {
                "series_key": "crm_integration_analytics_revenue",
                "metric": "yoy_pct",
                "weight": 0.18,
                "polarity": 1.0,
                "enabled": True,
                "source_pack": "seat_based_pricing",
                "rationale": "CRM Integration & Analytics rising → Tableau franchise.",
            },
            {
                "series_key": "sap_cloud_revenue",
                "metric": "yoy_pct",
                "weight": 0.14,
                "polarity": 1.0,
                "enabled": True,
                "rationale": "SAP Cloud rising → Joule / Business Data Cloud pulse.",
            },
            {
                "series_key": "bi_pureplay_revenue",
                "metric": "yoy_pct",
                "weight": 0.18,
                "polarity": 1.0,
                "enabled": True,
                "rationale": "AMPL+SMWB+DOMO revenue rising → pure-play analytics demand.",
            },
            {
                "series_key": "software_ipp_investment",
                "metric": "yoy_pct",
                "weight": 0.12,
                "polarity": 1.0,
                "enabled": True,
                "source_pack": "ai_resilient_software",
                "rationale": "US software IPP rising → enterprise analytics CapEx.",
            },
            {
                "series_key": "ramp_ai_adoption",
                "metric": "m3_avg_yoy_pct",
                "weight": 0.1,
                "polarity": 1.0,
                "enabled": True,
                "source_pack": "ai_resilient_software",
                "rationale": "Ramp AI adoption rising → agentic analytics demand.",
            },
            {
                "series_key": "usaspending_palantir_analytics",
                "metric": "yoy_pct",
                "weight": 0.08,
                "polarity": 1.0,
                "enabled": True,
                "rationale": "Federal Palantir/analytics obligations rising → gov demand.",
            },
        ],
        "notes": (
            "Kalshi AI bill + transcripts + SO tags + forums + wiki + JP adjacency "
            "chart-only. MSFT Power BI via transcripts only (no FMP BI segment). "
            "CRM Sales/Service stay on seat-based."
        ),
    }

    scores_path = ROOT / "config" / "theme_scores.json"
    scores = json.loads(scores_path.read_text())
    for arr_key in ("themes", "scores"):
        if not isinstance(scores.get(arr_key), list):
            continue
        arr = [t for t in scores[arr_key] if t.get("theme_slug") != SLUG]
        arr.append(score_recipe)
        scores[arr_key] = arr
        print(f"theme_scores.{arr_key}: upserted")
    scores_path.write_text(json.dumps(scores, indent=2) + "\n")

    sched_path = ROOT / "config" / "update_schedule.json"
    sched = json.loads(sched_path.read_text())
    packs = [p for p in sched.get("packs") or [] if p.get("id") not in (PACK, FORUMS)]
    packs.append(
        {
            "id": PACK,
            "job": f"jobs/{PACK}.py",
            "enabled": True,
            "hosts": ["vps", "any"],
            "cadence": "weekly",
            "due": {"weekdays": ["Fri"], "max_age_days": 8},
            "freshness": {
                "r2_key": f"themedata/{PACK}/metrics_latest.json",
                "as_of_field": "as_of",
                "local_relative": f"data/{PACK}/latest.json",
            },
            "description": (
                "Weekly data analytics/BI — FMP PLTR/SAP/pure-plays + SO + "
                "USASpending + Kalshi."
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
                "Weekly data analytics/BI forums (Arctic Shift "
                "dataeng+BI+PowerBI)."
            ),
        }
    )
    sched["packs"] = packs
    sched_path.write_text(json.dumps(sched, indent=2) + "\n")
    print("schedule: upserted")
    print("done")


if __name__ == "__main__":
    main()
