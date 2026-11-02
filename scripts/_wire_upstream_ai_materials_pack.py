#!/usr/bin/env python3
"""One-shot wire: themes / datasets / attachments / boards / scores / schedule."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
THEME = "AI Bottleneck '26: Upstream AI Materials"
SLUG = "ai-bottleneck-26-upstream-ai-materials"
GROUP = "AI Bottleneck"
GROUP_SLUG = "ai-bottleneck"
PACK = "upstream_ai_materials"
FORUMS = "upstream_ai_materials_forums"

REUSE = [
    (
        "hyperscaler_capex",
        "optical_fiber",
        "Reuse optical_fiber hyperscaler CapEx — AI server materials demand envelope",
    ),
    (
        "taiwan_semiconductor_exports",
        "memory_cxl",
        "Reuse memory_cxl Taiwan semiconductor exports — TW electronics BOM pulse",
    ),
    (
        "korea_semiconductor_exports",
        "memory_cxl",
        "Reuse memory_cxl Korea semiconductor exports — KR electronics cycle",
    ),
    (
        "kalshi_americas_dc_capacity_57_5gw",
        "photonics_materials",
        "Reuse photonics Kalshi Americas DC capacity — AI infra build pulse (chart-only)",
    ),
    (
        "glw_revenue",
        "optical_fiber",
        "Reuse optical_fiber GLW Optical Communications — AI interconnect materials",
    ),
    (
        "semis_abf_mentions",
        "ic_substrates_forums",
        "Reuse ic_substrates_forums ABF mentions — Ajinomoto Build-up Film talk (chart-only)",
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
                    "v1: ROG AES + TW CCL/laminate (Elite/ITEQ/TUC) + Fulltech/"
                    "Co-Tech + Census copper foil/glass fiber/epoxy + materials "
                    "transcripts + USASpending CHIPS + wiki. Reuses hyperscaler "
                    "CapEx (optical_fiber) + TW/KR semi exports (memory_cxl) + "
                    "Kalshi Americas DC (photonics). Forums: semiconductors+"
                    "chemicalengineering. Differs from IC substrates "
                    "(Unimicron/Ibiden), photonics (InP/SOI), optical fiber, HBM."
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
        (ROOT / "config" / "upstream_ai_materials_catalog.json").read_text()
    )
    fcatalog = json.loads(
        (ROOT / "config" / "upstream_ai_materials_forums_catalog.json").read_text()
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
        "ROG": ["rog_aes_revenue", "rog_ems_revenue", "rog_revenue"],
        "2383.TW": ["elite_material_2383_revenue"],
        "6213.TW": ["iteq_6213_revenue"],
        "6274.TWO": ["tuc_6274_revenue"],
        "8039.TW": ["taiflex_8039_revenue"],
        "1815.TWO": ["fulltech_1815_revenue"],
        "1802.TW": ["taiwan_glass_1802_revenue"],
        "8358.TWO": ["cotech_8358_revenue"],
        "3110.T": ["nitto_boseki_3110_revenue"],
        "5384.T": ["fujimi_5384_revenue"],
        "4203.T": ["sumitomo_bakelite_4203_revenue"],
    }
    for ticker, keys in ticker_map.items():
        for k in keys:
            if add_att(series_key=k, scope="ticker", ticker=ticker):
                n_att += 1
    # Membership-only mega-caps / adjacency without clean FMP materials cut
    for ticker, note in (
        ("ENTG", "Theme membership — materials transcripts / CHIPS adjacency"),
        ("GLW", "Theme membership — optical glass adjacency (pack is CCL/foil)"),
        ("MMM", "Theme membership — advanced materials adjacency"),
        ("ESI", "Theme membership — electronics chemistry adjacency"),
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
                    "series_key": "rog_aes_revenue",
                    "metric": "raw",
                    "chart_type": "bar",
                    "section": "primary",
                    "why_it_matters": (
                        "Rogers Advanced Electronics Solutions is the cleanest "
                        "public high-frequency laminate / AI-board materials cut."
                    ),
                },
                {
                    "series_key": "tw_ccl_laminate_revenue",
                    "metric": "raw",
                    "chart_type": "bar",
                    "section": "primary",
                    "why_it_matters": (
                        "Elite + ITEQ + TUC sum is the TW high-end CCL/laminate "
                        "pure-play stack (ex Taiflex FPC)."
                    ),
                },
                {
                    "chart_type": "stacked_bar",
                    "metric": "raw",
                    "display_name": "TW CCL/laminate revenue by name",
                    "series_keys": [
                        "elite_material_2383_revenue",
                        "iteq_6213_revenue",
                        "tuc_6274_revenue",
                    ],
                    "sum_series_key": "tw_ccl_laminate_revenue",
                    "section": "primary",
                    "why_it_matters": (
                        "Named Elite / ITEQ / TUC layers show who is absorbing "
                        "AI server board materials demand."
                    ),
                },
                {
                    "chart_type": "stacked_bar",
                    "metric": "raw",
                    "display_name": "Fiberglass + copper foil pure-plays",
                    "series_keys": [
                        "fulltech_1815_revenue",
                        "cotech_8358_revenue",
                    ],
                    "section": "primary",
                    "why_it_matters": (
                        "Fulltech electronic fiberglass + Co-Tech copper foil "
                        "are the scarce-input pure-plays behind laminates."
                    ),
                },
                {
                    "series_key": "hs7410_copper_foil_imports",
                    "metric": "m3_avg",
                    "chart_type": "bar",
                    "section": "primary",
                    "why_it_matters": (
                        "US copper foil imports (HS 7410xx) are the free trade "
                        "pulse for electronic foil scarcity."
                    ),
                },
                {
                    "series_key": "hs701912_glass_fiber_imports",
                    "metric": "m3_avg",
                    "chart_type": "bar",
                    "section": "primary",
                    "why_it_matters": (
                        "US glass fiber rovings imports proxy low-CTE / "
                        "electronic yarn feedstock into laminates."
                    ),
                },
                {
                    "series_key": "hyperscaler_capex",
                    "metric": "yoy_pct",
                    "chart_type": "bar",
                    "section": "primary",
                    "why_it_matters": (
                        "Hyperscaler CapEx is the AI server demand envelope that "
                        "pulls specialized board materials (reused)."
                    ),
                },
                {
                    "series_key": "taiwan_semiconductor_exports",
                    "metric": "m3_avg_yoy_pct",
                    "chart_type": "bar",
                    "section": "primary",
                    "why_it_matters": (
                        "Taiwan semi exports are the regional electronics BOM "
                        "pulse for CCL/foil demand (reused)."
                    ),
                },
                {
                    "series_key": "upstream_materials_transcripts",
                    "metric": "raw",
                    "chart_type": "bar",
                    "section": "primary",
                    "why_it_matters": (
                        "Earnings-call hits on copper foil / CCL / T-glass / ABF "
                        "track materials narrative intensity at ROG + buyers."
                    ),
                },
                {
                    "series_key": "usaspending_chips_materials",
                    "metric": "m3_avg",
                    "chart_type": "bar",
                    "section": "primary",
                    "why_it_matters": (
                        "Federal CHIPS / materials obligations are the free "
                        "domestic fab-build spillover pulse."
                    ),
                },
                {
                    "series_key": "kalshi_americas_dc_capacity_57_5gw",
                    "metric": "raw",
                    "chart_type": "line",
                    "section": "primary",
                    "why_it_matters": (
                        "Kalshi Americas DC capacity odds price AI infra "
                        "build risk that pulls upstream materials (reused)."
                    ),
                },
                {
                    "series_key": "uam_materials_talk_mentions",
                    "metric": "raw",
                    "chart_type": "bar",
                    "section": "primary",
                    "display_name": "Forum upstream materials talk",
                    "why_it_matters": (
                        "Reddit CCL/foil/T-glass/ABF talk on semis + chemeng "
                        "tracks practitioner attention ahead of quarterlies."
                    ),
                },
                {
                    "chart_type": "stacked_bar",
                    "metric": "raw",
                    "display_name": "Forum community volume",
                    "series_keys": [
                        "uam_semiconductors_sub_posts",
                        "uam_chemicalengineering_sub_posts",
                    ],
                    "section": "primary",
                    "why_it_matters": (
                        "Community volume across semiconductors / chemeng is "
                        "the umbrella practitioner traffic backdrop."
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
        "score_id": "upstream_ai_materials_backdrop",
        "label": "Upstream AI materials backdrop",
        "pack": PACK,
        "status": "active",
        "signal": {
            "yoy_clip_pct": 25.0,
            "exclude_incomplete_month": True,
            "history_smooth_months": 3,
            "note": (
                "ROG AES + TW CCL stack + Fulltech + Co-Tech + copper foil "
                "imports + hyperscaler CapEx + TW/KR exports; glass fiber / "
                "USASpending soft. Kalshi/forums/wiki/transcripts chart-only."
            ),
        },
        "components": [
            {
                "series_key": "rog_aes_revenue",
                "metric": "yoy_pct",
                "weight": 0.2,
                "polarity": 1.0,
                "enabled": True,
                "rationale": "ROG AES rising → high-end laminate / AI board materials demand.",
            },
            {
                "series_key": "tw_ccl_laminate_revenue",
                "metric": "yoy_pct",
                "weight": 0.2,
                "polarity": 1.0,
                "enabled": True,
                "rationale": "Elite+ITEQ+TUC rising → TW CCL/laminate pure-play demand.",
            },
            {
                "series_key": "fulltech_1815_revenue",
                "metric": "yoy_pct",
                "weight": 0.12,
                "polarity": 1.0,
                "enabled": True,
                "rationale": "Fulltech rising → electronic fiberglass / T-glass adjacency.",
            },
            {
                "series_key": "cotech_8358_revenue",
                "metric": "yoy_pct",
                "weight": 0.12,
                "polarity": 1.0,
                "enabled": True,
                "rationale": "Co-Tech rising → electronic copper foil scarcity pulse.",
            },
            {
                "series_key": "hs7410_copper_foil_imports",
                "metric": "m3_avg_yoy_pct",
                "weight": 0.12,
                "polarity": 1.0,
                "enabled": True,
                "rationale": "HS copper foil imports rising → foil trade / scarcity pulse.",
            },
            {
                "series_key": "hyperscaler_capex",
                "metric": "yoy_pct",
                "weight": 0.1,
                "polarity": 1.0,
                "enabled": True,
                "source_pack": "optical_fiber",
                "rationale": "Hyperscaler CapEx rising → AI server materials demand envelope.",
            },
            {
                "series_key": "taiwan_semiconductor_exports",
                "metric": "m3_avg_yoy_pct",
                "weight": 0.06,
                "polarity": 1.0,
                "enabled": True,
                "source_pack": "memory_cxl",
                "rationale": "TW semi exports rising → electronics BOM for CCL/foil.",
            },
            {
                "series_key": "korea_semiconductor_exports",
                "metric": "m3_avg_yoy_pct",
                "weight": 0.04,
                "polarity": 1.0,
                "enabled": True,
                "source_pack": "memory_cxl",
                "rationale": "KR semi exports rising → regional electronics cycle.",
            },
            {
                "series_key": "hs701912_glass_fiber_imports",
                "metric": "m3_avg_yoy_pct",
                "weight": 0.04,
                "polarity": 1.0,
                "enabled": True,
                "rationale": "Glass fiber imports rising → fiberglass yarn feedstock (soft).",
            },
        ],
        "notes": (
            "Kalshi DC + transcripts + forums + wiki + USASpending + epoxy + "
            "JP conglomerates (Nitto/Fujimi/Bakelite) + Taiflex FPC + ROG EMS "
            "chart-only. Differs from IC substrates / photonics / optical fiber."
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
            "due": {"weekdays": ["Sat"], "max_age_days": 8},
            "freshness": {
                "r2_key": f"themedata/{PACK}/metrics_latest.json",
                "as_of_field": "as_of",
                "local_relative": f"data/{PACK}/latest.json",
            },
            "description": (
                "Weekly upstream AI materials — FMP ROG/TW CCL/foil + Census HS "
                "+ USASpending CHIPS."
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
            "due": {"weekdays": ["Sat"], "max_age_days": 8},
            "freshness": {
                "r2_key": f"themedata/{FORUMS}/metrics_latest.json",
                "as_of_field": "as_of",
                "local_relative": f"data/{FORUMS}/latest.json",
            },
            "description": (
                "Weekly upstream AI materials forums (Arctic Shift "
                "semiconductors+chemicalengineering)."
            ),
        }
    )
    sched["packs"] = packs
    sched_path.write_text(json.dumps(sched, indent=2) + "\n")
    print("schedule: upserted")
    print("done")


if __name__ == "__main__":
    main()
