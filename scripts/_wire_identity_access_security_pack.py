#!/usr/bin/env python3
"""One-shot wire: themes / datasets / attachments / boards / scores / schedule."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
THEME = "Cybersecurity '26: Identity & Access Security"
SLUG = "cybersecurity-26-identity-access-security"
GROUP = "Cybersecurity"
GROUP_SLUG = "cybersecurity"
PACK = "identity_access_security"
FORUMS = "identity_access_security_forums"

REUSE = [
    (
        "kalshi_okta_cust_100k_acv",
        "seat_based_pricing",
        "Reuse seat_based Kalshi Okta $100k ACV customers — IdP expansion pulse (chart-only)",
    ),
    (
        "panw_subscription_revenue",
        "cybersecurity_software",
        "Reuse cybersecurity_software PANW subscription — platform identity adjacency",
    ),
    (
        "crwd_subscription_revenue",
        "cybersecurity_software",
        "Reuse cybersecurity_software CRWD subscription — Falcon Identity adjacency",
    ),
    (
        "software_ipp_investment",
        "ai_resilient_software",
        "Reuse ai_resilient software IPP investment — enterprise security budget envelope",
    ),
    (
        "ag_nhi_talk_mentions",
        "agent_governance_zt_forums",
        "Reuse agent_gov NHI/agent identity forum talk — Agentic Era backdrop (chart-only)",
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
                    "v1: OKTA+SAIL IAM/IGA + OneSpan Digipass sub + Yubico + TENB "
                    "sub adjacency + identity/PAM/passkey transcripts + USASpending "
                    "IAM + wiki. Reuses Kalshi Okta (seat_based) + PANW/CRWD sub "
                    "(cyber software) + software IPP (ai_resilient) + NHI forum talk "
                    "(agent_gov). Forums: cybersecurity+identitymanagement+sysadmin. "
                    "Differs from agent_gov (NHI/ZT cloud/MCP) and seat_based (seats)."
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
                # Prefer keeping existing registry row; stamp pack ownership lightly
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
        "OKTA": ["okta_revenue"],
        "SAIL": ["sail_revenue"],
        "OSPN": ["ospn_subscription_revenue"],
        "YUBICO.ST": ["yubico_revenue"],
        "TENB": ["tenb_subscription_revenue"],
    }
    for ticker, keys in ticker_map.items():
        for k in keys:
            if add_att(series_key=k, scope="ticker", ticker=ticker):
                n_att += 1
    for ticker, note in (
        ("MSFT", "Theme membership — Entra / passkeys via transcripts"),
        ("PANW", "Theme membership — CyberArk / identity platform adjacency"),
        ("CRWD", "Theme membership — Falcon Identity adjacency"),
        ("S", "Theme membership — Singularity Identity adjacency"),
        ("HO.PA", "Theme membership — Thales auth adjacency (no clean FMP cut)"),
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
                    "display_name": "IAM/IGA revenue by name",
                    "series_keys": ["okta_revenue", "sail_revenue"],
                    "sum_series_key": "identity_iam_iga_revenue",
                    "section": "primary",
                    "why_it_matters": (
                        "Okta + SailPoint are the public workforce IdP / IGA "
                        "pure-plays at the center of the Agentic Era identity story."
                    ),
                },
                {
                    "series_key": "yubico_revenue",
                    "metric": "raw",
                    "chart_type": "bar",
                    "section": "primary",
                    "why_it_matters": (
                        "Yubico is the cleanest public phishing-resistant FIDO2 / "
                        "passkey hardware franchise."
                    ),
                },
                {
                    "series_key": "ospn_subscription_revenue",
                    "metric": "raw",
                    "chart_type": "bar",
                    "section": "primary",
                    "why_it_matters": (
                        "OneSpan Digipass subscription tracks authentication software "
                        "demand adjacent to hardware keys."
                    ),
                },
                {
                    "series_key": "tenb_subscription_revenue",
                    "metric": "raw",
                    "chart_type": "bar",
                    "section": "primary",
                    "why_it_matters": (
                        "Tenable subscription is a soft Identity Exposure / exposure-"
                        "management adjacency to the IAM core."
                    ),
                },
                {
                    "series_key": "panw_subscription_revenue",
                    "metric": "raw",
                    "chart_type": "bar",
                    "section": "primary",
                    "why_it_matters": (
                        "PANW subscription (reused) captures platformization including "
                        "CyberArk / identity after M&A."
                    ),
                },
                {
                    "series_key": "crwd_subscription_revenue",
                    "metric": "raw",
                    "chart_type": "bar",
                    "section": "primary",
                    "why_it_matters": (
                        "CRWD subscription (reused) includes Falcon Identity "
                        "platform adjacency."
                    ),
                },
                {
                    "series_key": "identity_access_transcripts",
                    "metric": "raw",
                    "chart_type": "bar",
                    "section": "primary",
                    "why_it_matters": (
                        "Earnings-call hits on IAM / PAM / passkeys / NHI / ITDR "
                        "track narrative intensity ahead of lagged ARR."
                    ),
                },
                {
                    "series_key": "usaspending_identity_access",
                    "metric": "m3_avg",
                    "chart_type": "bar",
                    "section": "primary",
                    "why_it_matters": (
                        "Federal IAM / PAM / Zero Trust obligations are the free "
                        "compliance-driven demand pulse."
                    ),
                },
                {
                    "series_key": "software_ipp_investment",
                    "metric": "yoy_pct",
                    "chart_type": "bar",
                    "section": "primary",
                    "why_it_matters": (
                        "US software IPP investment is the macro budget envelope "
                        "for identity platforms (reused)."
                    ),
                },
                {
                    "series_key": "kalshi_okta_cust_100k_acv",
                    "metric": "raw",
                    "chart_type": "line",
                    "section": "primary",
                    "why_it_matters": (
                        "Kalshi Okta $100k ACV customer odds price IdP expansion "
                        "risk (reused, chart-only)."
                    ),
                },
                {
                    "series_key": "ias_iam_talk_mentions",
                    "metric": "raw",
                    "chart_type": "bar",
                    "section": "primary",
                    "display_name": "Forum IAM/passkey/NHI talk",
                    "why_it_matters": (
                        "Practitioner IAM / PAM / passkey / NHI talk on cyber / "
                        "identitymanagement / sysadmin."
                    ),
                },
                {
                    "series_key": "ag_nhi_talk_mentions",
                    "metric": "raw",
                    "chart_type": "bar",
                    "section": "primary",
                    "display_name": "Forum NHI/agent talk (reused)",
                    "why_it_matters": (
                        "NHI / agent-identity talk reused from agent_gov forums — "
                        "Agentic Era backdrop."
                    ),
                },
                {
                    "chart_type": "stacked_bar",
                    "metric": "raw",
                    "display_name": "Forum named identity mentions",
                    "series_keys": [
                        "ias_okta_mentions",
                        "ias_sailpoint_mentions",
                        "ias_yubikey_mentions",
                        "ias_entra_mentions",
                        "ias_cyberark_mentions",
                    ],
                    "section": "primary",
                    "why_it_matters": (
                        "Named Okta / SailPoint / YubiKey / Entra / CyberArk chatter "
                        "separates franchise attention."
                    ),
                },
                {
                    "chart_type": "stacked_bar",
                    "metric": "raw",
                    "display_name": "Forum community volume",
                    "series_keys": [
                        "ias_cybersecurity_sub_posts",
                        "ias_identitymanagement_sub_posts",
                        "ias_sysadmin_sub_posts",
                    ],
                    "section": "primary",
                    "why_it_matters": (
                        "Community volume across cybersecurity / identitymanagement / "
                        "sysadmin is the umbrella practitioner traffic backdrop."
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
        "score_id": "identity_access_security_backdrop",
        "label": "Identity & access security backdrop",
        "pack": PACK,
        "status": "active",
        "signal": {
            "yoy_clip_pct": 20.0,
            "exclude_incomplete_month": True,
            "history_smooth_months": 3,
            "note": (
                "OKTA+SAIL IAM/IGA + Yubico + OneSpan + TENB soft + PANW/CRWD sub "
                "reuse + software IPP; USASpending soft. Kalshi/forums/wiki/"
                "transcripts/GitHub chart-only."
            ),
        },
        "components": [
            {
                "series_key": "okta_revenue",
                "metric": "yoy_pct",
                "weight": 0.22,
                "polarity": 1.0,
                "enabled": True,
                "rationale": "Okta revenue rising → workforce/customer IdP demand.",
            },
            {
                "series_key": "sail_revenue",
                "metric": "yoy_pct",
                "weight": 0.2,
                "polarity": 1.0,
                "enabled": True,
                "rationale": "SailPoint revenue rising → IGA / Agentic Fabric demand.",
            },
            {
                "series_key": "yubico_revenue",
                "metric": "yoy_pct",
                "weight": 0.12,
                "polarity": 1.0,
                "enabled": True,
                "rationale": "Yubico rising → phishing-resistant FIDO2/passkey hardware.",
            },
            {
                "series_key": "ospn_subscription_revenue",
                "metric": "yoy_pct",
                "weight": 0.1,
                "polarity": 1.0,
                "enabled": True,
                "rationale": "OneSpan subscription rising → Digipass / auth software.",
            },
            {
                "series_key": "panw_subscription_revenue",
                "metric": "yoy_pct",
                "weight": 0.1,
                "polarity": 1.0,
                "enabled": True,
                "source_pack": "cybersecurity_software",
                "rationale": "PANW subscription rising → platform + CyberArk identity.",
            },
            {
                "series_key": "crwd_subscription_revenue",
                "metric": "yoy_pct",
                "weight": 0.08,
                "polarity": 1.0,
                "enabled": True,
                "source_pack": "cybersecurity_software",
                "rationale": "CRWD subscription rising → Falcon Identity adjacency.",
            },
            {
                "series_key": "software_ipp_investment",
                "metric": "yoy_pct",
                "weight": 0.08,
                "polarity": 1.0,
                "enabled": True,
                "source_pack": "ai_resilient_software",
                "rationale": "US software IPP rising → enterprise identity CapEx.",
            },
            {
                "series_key": "tenb_subscription_revenue",
                "metric": "yoy_pct",
                "weight": 0.05,
                "polarity": 1.0,
                "enabled": True,
                "rationale": "Tenable subscription rising → Identity Exposure adjacency (soft).",
            },
            {
                "series_key": "usaspending_identity_access",
                "metric": "m3_avg_yoy_pct",
                "weight": 0.05,
                "polarity": 1.0,
                "enabled": True,
                "rationale": "Federal IAM/ZT obligations rising → compliance demand (soft).",
            },
        ],
        "notes": (
            "Kalshi Okta + transcripts + forums + wiki + GitHub chart-only. "
            "MSFT Entra via transcripts only. Differs from agent_gov NHI/ZT-cloud "
            "score and seat_based seat SaaS score."
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
            "due": {"weekdays": ["Sun"], "max_age_days": 8},
            "freshness": {
                "r2_key": f"themedata/{PACK}/metrics_latest.json",
                "as_of_field": "as_of",
                "local_relative": f"data/{PACK}/latest.json",
            },
            "description": (
                "Weekly identity & access — FMP OKTA/SAIL/Yubico/OSPN + USASpending."
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
            "due": {"weekdays": ["Sun"], "max_age_days": 8},
            "freshness": {
                "r2_key": f"themedata/{FORUMS}/metrics_latest.json",
                "as_of_field": "as_of",
                "local_relative": f"data/{FORUMS}/latest.json",
            },
            "description": (
                "Weekly identity forums (Arctic Shift cybersecurity+"
                "identitymanagement+sysadmin)."
            ),
        }
    )
    sched["packs"] = packs
    sched_path.write_text(json.dumps(sched, indent=2) + "\n")
    print("schedule: upserted")
    print("done")


if __name__ == "__main__":
    main()
