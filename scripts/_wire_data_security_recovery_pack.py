#!/usr/bin/env python3
"""One-shot wire: themes / datasets / attachments / boards / scores / schedule."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
THEME = "Cybersecurity '26: Data Security & Recovery"
SLUG = "cybersecurity-26-data-security-recovery"
GROUP = "Cybersecurity"
GROUP_SLUG = "cybersecurity"
PACK = "data_security_recovery"
FORUMS = "data_security_recovery_forums"

REUSE = [
    (
        "cyber_resilience_saas",
        "agent_governance_zt",
        "Reuse agent_gov RBRK+CVLT+VRNS cyber-resilience SaaS — theme hero stack",
    ),
    (
        "rbrk_subscription_revenue",
        "agent_governance_zt",
        "Reuse Rubrik subscription (Security Cloud / Agent Cloud)",
    ),
    (
        "cvlt_subscription_revenue",
        "agent_governance_zt",
        "Reuse Commvault subscription (SaaS / cyber resilience migration)",
    ),
    (
        "vrns_saas_revenue",
        "agent_governance_zt",
        "Reuse Varonis SaaS (DSPM / data security)",
    ),
    (
        "sec_item105_8k_count",
        "cybersecurity_software",
        "Reuse SEC Item 1.05 8-K counts — disclosure → recovery budgets",
    ),
    (
        "software_ipp_investment",
        "ai_resilient_software",
        "Reuse software IPP — enterprise security budget envelope",
    ),
    (
        "panw_subscription_revenue",
        "cybersecurity_software",
        "Reuse PANW subscription — platform absorbing data security (soft)",
    ),
    (
        "zs_revenue",
        "cybersecurity_software",
        "Reuse Zscaler revenue — Zero Trust / AI Protect adjacency (soft)",
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
                "packs": [
                    PACK,
                    FORUMS,
                    "agent_governance_zt",
                    "cybersecurity_software",
                    "ai_resilient_software",
                ],
                "notes": (
                    "v1: MSP/data-security SaaS (AVPT+NABL) + NTAP Public Cloud / "
                    "PSTG Product storage + ransomware.live victims + ransomware "
                    "KEV + cyber-recovery USASpending + recovery/DSPM transcripts "
                    "+ wiki. Reuses cyber_resilience_saas (agent_gov) + Item 1.05 + "
                    "software IPP + PANW/ZS soft. Forums: cybersecurity+sysadmin+msp. "
                    "Differs from vuln (CTEM/KEV-all), identity (IdP/PAM), "
                    "agent_gov (NHI/ZT-cloud primary)."
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
    catalog = json.loads((ROOT / "config" / f"{PACK}_catalog.json").read_text())
    fcatalog = json.loads((ROOT / "config" / f"{FORUMS}_catalog.json").read_text())
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
    for series_key, pack, note in REUSE:
        if add_att(series_key=series_key, pack=pack, note=note):
            n_att += 1

    ticker_map = {
        "AVPT": ["avpt_revenue"],
        "NABL": ["nabl_subscription_revenue"],
        "NTAP": ["ntap_public_cloud_revenue"],
        "PSTG": ["pstg_product_revenue"],
        "DELL": ["dell_revenue"],
        "RBRK": ["rbrk_subscription_revenue"],
        "CVLT": ["cvlt_subscription_revenue"],
        "VRNS": ["vrns_saas_revenue"],
    }
    for ticker, keys in ticker_map.items():
        for k in keys:
            pack = (
                "agent_governance_zt"
                if k
                in (
                    "rbrk_subscription_revenue",
                    "cvlt_subscription_revenue",
                    "vrns_saas_revenue",
                )
                else None
            )
            if add_att(series_key=k, scope="ticker", ticker=ticker, pack=pack):
                n_att += 1
    for ticker, note in (
        ("MSFT", "Theme membership — Purview / M365 data security via transcripts"),
        ("IBM", "Theme membership — Guardium / cyber recovery adjacency"),
        ("PANW", "Theme membership — Prisma / Cortex data security adjacency"),
        ("ZS", "Theme membership — Zero Trust / AI Protect adjacency"),
        ("FTNT", "Theme membership — FortiDLP adjacency"),
    ):
        if add_att(series_key=None, scope="membership", ticker=ticker, note=note):
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
                    "display_name": "Cyber resilience SaaS (RBRK+CVLT+VRNS)",
                    "series_keys": [
                        "rbrk_subscription_revenue",
                        "cvlt_subscription_revenue",
                        "vrns_saas_revenue",
                    ],
                    "sum_series_key": "cyber_resilience_saas",
                    "section": "primary",
                    "why_it_matters": (
                        "Rubrik + Commvault + Varonis are the public cyber-resilience "
                        "/ DSPM SaaS core of the theme (reused from agent_gov)."
                    ),
                },
                {
                    "chart_type": "stacked_bar",
                    "metric": "raw",
                    "display_name": "MSP / data-security SaaS (AVPT+NABL)",
                    "series_keys": ["avpt_revenue", "nabl_subscription_revenue"],
                    "sum_series_key": "msp_data_security_saas",
                    "section": "primary",
                    "why_it_matters": (
                        "AvePoint + N-able Cove extend the resilience story into "
                        "Microsoft 365 / SMB-MSP recovery."
                    ),
                },
                {
                    "chart_type": "stacked_bar",
                    "metric": "raw",
                    "display_name": "Immutable storage / cyber recovery",
                    "series_keys": [
                        "ntap_public_cloud_revenue",
                        "pstg_product_revenue",
                    ],
                    "sum_series_key": "storage_cyber_recovery",
                    "section": "primary",
                    "why_it_matters": (
                        "NetApp Public Cloud + Pure Product track hardware-enforced "
                        "immutability / SafeMode adjacency under CapEx constraints."
                    ),
                },
                {
                    "series_key": "ransomware_live_weekly_victims",
                    "metric": "wma4",
                    "chart_type": "bar",
                    "section": "primary",
                    "why_it_matters": (
                        "Rising ransomware leak-site claims are the free demand "
                        "pulse for recovery / immutable backup spend."
                    ),
                },
                {
                    "series_key": "cisa_kev_ransomware_weekly_adds",
                    "metric": "wma4",
                    "chart_type": "bar",
                    "section": "primary",
                    "why_it_matters": (
                        "CISA KEV adds tagged Known for ransomware campaign use "
                        "signal exploit urgency that drives recovery budgets "
                        "(not all-KEV — that is the vuln theme)."
                    ),
                },
                {
                    "series_key": "sec_item105_8k_count",
                    "metric": "m3_avg",
                    "chart_type": "bar",
                    "section": "primary",
                    "why_it_matters": (
                        "SEC Item 1.05 cyber incident 8-Ks (reused) push boards "
                        "toward resilience and disclosure readiness."
                    ),
                },
                {
                    "series_key": "usaspending_cyber_recovery",
                    "metric": "m3_avg",
                    "chart_type": "bar",
                    "section": "primary",
                    "why_it_matters": (
                        "Federal cyber recovery / data-protection awards are the "
                        "compliance-driven demand pulse."
                    ),
                },
                {
                    "series_key": "dsr_transcript_keywords",
                    "metric": "raw",
                    "chart_type": "bar",
                    "section": "primary",
                    "why_it_matters": (
                        "Earnings-call hits on DSPM / cleanroom / immutable / "
                        "ransomware recovery track narrative intensity."
                    ),
                },
                {
                    "series_key": "software_ipp_investment",
                    "metric": "yoy_pct",
                    "chart_type": "bar",
                    "section": "primary",
                    "why_it_matters": (
                        "US software IPP (reused) is the macro budget envelope "
                        "for cyber resilience platforms."
                    ),
                },
                {
                    "series_key": "panw_subscription_revenue",
                    "metric": "raw",
                    "chart_type": "bar",
                    "section": "primary",
                    "why_it_matters": (
                        "PANW subscription (reused, soft) shows platforms "
                        "absorbing data-security modules."
                    ),
                },
                {
                    "series_key": "zs_revenue",
                    "metric": "raw",
                    "chart_type": "bar",
                    "section": "primary",
                    "why_it_matters": (
                        "Zscaler revenue (reused, soft) tracks Zero Trust / AI "
                        "Protect adjacency to data security."
                    ),
                },
                {
                    "series_key": "dsr_recovery_talk_mentions",
                    "metric": "raw",
                    "chart_type": "bar",
                    "section": "primary",
                    "display_name": "Forum recovery / DSPM talk",
                    "why_it_matters": (
                        "Practitioner recovery / DSPM / cleanroom talk on cyber / "
                        "sysadmin / msp."
                    ),
                },
                {
                    "chart_type": "multi_line",
                    "metric": "raw",
                    "display_name": "Forum recovery vs named-product sentiment",
                    "series_keys": [
                        "dsr_recovery_talk_sentiment",
                        "dsr_named_sentiment",
                    ],
                    "section": "primary",
                    "why_it_matters": (
                        "Sentiment on recovery talk vs named resilience brands "
                        "(same unit)."
                    ),
                },
                {
                    "chart_type": "stacked_bar",
                    "metric": "raw",
                    "display_name": "Forum named resilience mentions",
                    "series_keys": [
                        "dsr_rubrik_mentions",
                        "dsr_commvault_mentions",
                        "dsr_varonis_mentions",
                        "dsr_avepoint_mentions",
                        "dsr_nable_mentions",
                        "dsr_netapp_mentions",
                    ],
                    "section": "primary",
                    "why_it_matters": (
                        "Named Rubrik / Commvault / Varonis / AvePoint / N-able / "
                        "NetApp chatter separates franchise attention."
                    ),
                },
                {
                    "chart_type": "stacked_bar",
                    "metric": "raw",
                    "display_name": "Forum community volume",
                    "series_keys": [
                        "dsr_cybersecurity_sub_posts",
                        "dsr_sysadmin_sub_posts",
                        "dsr_msp_sub_posts",
                    ],
                    "section": "primary",
                    "why_it_matters": (
                        "Community volume across cybersecurity / sysadmin / msp "
                        "is the umbrella practitioner traffic backdrop."
                    ),
                },
                {
                    "series_key": "wiki_ransomware_pageviews",
                    "metric": "m3_avg",
                    "chart_type": "bar",
                    "section": "primary",
                    "why_it_matters": (
                        "Wikipedia Ransomware interest is a soft awareness pulse."
                    ),
                },
            ],
        }
    )
    for ticker, keys in ticker_map.items():
        boards_list.append(
            {
                "scope": "ticker",
                "theme": THEME,
                "theme_slug": SLUG,
                "ticker": ticker,
                "recipes": [
                    {"series_key": k, "metric": "raw", "chart_type": "bar"}
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
        "score_id": "data_security_recovery_backdrop",
        "label": "Data security & recovery backdrop",
        "pack": PACK,
        "status": "active",
        "signal": {
            "yoy_clip_pct": 20.0,
            "exclude_incomplete_month": True,
            "history_smooth_months": 3,
            "note": (
                "Resilience SaaS (reuse) + MSP SaaS + storage cyber recovery + "
                "ransomware.live + ransomware KEV; soft USASpending + IPP + "
                "Item 1.05. Forums/wiki/transcripts chart-only."
            ),
        },
        "components": [
            {
                "series_key": "cyber_resilience_saas",
                "metric": "yoy_pct",
                "weight": 1.5,
                "polarity": 1.0,
                "enabled": True,
                "source_pack": "agent_governance_zt",
                "rationale": (
                    "RBRK+CVLT+VRNS SaaS rising → cyber resilience / DSPM demand."
                ),
            },
            {
                "series_key": "msp_data_security_saas",
                "metric": "yoy_pct",
                "weight": 1.0,
                "polarity": 1.0,
                "enabled": True,
                "rationale": (
                    "AVPT+NABL rising → MSP / M365 data-security recovery path."
                ),
            },
            {
                "series_key": "storage_cyber_recovery",
                "metric": "yoy_pct",
                "weight": 0.8,
                "polarity": 1.0,
                "enabled": True,
                "rationale": (
                    "NTAP Public Cloud + PSTG Product rising → immutable storage."
                ),
            },
            {
                "series_key": "ransomware_live_weekly_victims",
                "metric": "wma4",
                "weight": 0.6,
                "polarity": 1.0,
                "enabled": True,
                "rationale": (
                    "Leak-site victim claims rising → recovery urgency / demand."
                ),
            },
            {
                "series_key": "cisa_kev_ransomware_weekly_adds",
                "metric": "wma4",
                "weight": 0.4,
                "polarity": 1.0,
                "enabled": True,
                "rationale": (
                    "Ransomware-tagged KEV adds rising → exploit-driven recovery."
                ),
            },
            {
                "series_key": "software_ipp_investment",
                "metric": "yoy_pct",
                "weight": 0.35,
                "polarity": 1.0,
                "enabled": True,
                "source_pack": "ai_resilient_software",
                "rationale": "US software IPP rising → enterprise security CapEx.",
            },
            {
                "series_key": "sec_item105_8k_count",
                "metric": "m3_avg",
                "weight": 0.25,
                "polarity": 1.0,
                "enabled": True,
                "source_pack": "cybersecurity_software",
                "rationale": "Item 1.05 filings rising → disclosure / resilience spend.",
            },
            {
                "series_key": "usaspending_cyber_recovery",
                "metric": "m3_avg_yoy_pct",
                "weight": 0.25,
                "polarity": 1.0,
                "enabled": True,
                "rationale": (
                    "Federal cyber recovery awards rising → compliance demand (soft)."
                ),
            },
        ],
        "notes": (
            "Forums + wiki + transcripts chart-only. Differs from vuln all-KEV, "
            "identity IdP/PAM, and agent_gov NHI/ZT-cloud primary scores."
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
            "due": {"weekdays": ["Tue"], "max_age_days": 8},
            "freshness": {
                "r2_key": f"themedata/{PACK}/metrics_latest.json",
                "as_of_field": "as_of",
                "local_relative": f"data/{PACK}/latest.json",
            },
            "description": (
                "Weekly data security & recovery — FMP MSP/storage + "
                "ransomware.live + ransomware KEV + USASpending."
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
            "due": {"weekdays": ["Tue"], "max_age_days": 8},
            "freshness": {
                "r2_key": f"themedata/{FORUMS}/metrics_latest.json",
                "as_of_field": "as_of",
                "local_relative": f"data/{FORUMS}/latest.json",
            },
            "description": (
                "Weekly data-security forums (Arctic Shift cybersecurity+"
                "sysadmin+msp)."
            ),
        }
    )
    sched["packs"] = packs
    sched_path.write_text(json.dumps(sched, indent=2) + "\n")
    print("schedule: upserted")
    print("done")


if __name__ == "__main__":
    main()
