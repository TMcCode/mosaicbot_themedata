#!/usr/bin/env python3
"""One-shot wire: themes / datasets / attachments / boards / scores / schedule."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
THEME = "Biotech '24: Vaccine Makers"
SLUG = "biotech-24-vaccine-makers"
GROUP = "Biotech"
GROUP_SLUG = "biotech"
PACK = "vaccine_makers"
FORUMS = "vaccine_makers_forums"


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
                    "v1: MRNA COVID/RSV + NVAX Nuvaxovid + EBS Product/Contracts + "
                    "VALN travel (IXIARO/DUKORAL) + pure-play revenue sum + "
                    "franchise transcripts (MRK/PFE/GSK/SNY/MRNA/BNTX) + CDC NIS "
                    "flu/COVID/RSV coverage + flu refusal + ClinicalTrials ID "
                    "vaccines + USASpending biodefense + Census HS 300220 + "
                    "Kalshi ACIP-rec/mpox + wiki. No MRK/PFE mega-segment scoring. "
                    "Forums: pharmacy+biotech."
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
    catalog = json.loads((ROOT / "config" / "vaccine_makers_catalog.json").read_text())
    fcatalog = json.loads(
        (ROOT / "config" / "vaccine_makers_forums_catalog.json").read_text()
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
            if s.get("hs6"):
                row["hs6"] = s["hs6"]
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

    ticker_map = {
        "MRNA": [
            "mrna_covid_revenue",
            "mrna_rsv_revenue",
            "mrna_revenue",
            "mrna_respiratory_franchise",
        ],
        "BNTX": ["bntx_revenue"],
        "NVAX": ["nvax_nuvaxovid_revenue", "nvax_revenue"],
        "EBS": ["ebs_product_revenue", "ebs_contracts_grants", "ebs_revenue"],
        "VALN": [
            "valn_ixiaro_revenue",
            "valn_dukoral_revenue",
            "valn_travel_vaccine_revenue",
        ],
        "VALN.PA": [
            "valn_ixiaro_revenue",
            "valn_dukoral_revenue",
            "valn_travel_vaccine_revenue",
        ],
    }
    for ticker, keys in ticker_map.items():
        for k in keys:
            if add_att(series_key=k, scope="ticker", ticker=ticker):
                n_att += 1
    # Membership-only for big pharma (transcripts are theme-scoped).
    for ticker in ("MRK", "PFE", "GSK", "SNY", "PCVX", "BAVA.CO", "CSL.AU"):
        key = (SLUG, None, ticker, "membership")
        if key not in existing_pairs:
            att.setdefault("attachments", []).append(
                {
                    "scope": "membership",
                    "theme": THEME,
                    "theme_slug": SLUG,
                    "ticker": ticker,
                    "series_key": None,
                    "pack": PACK,
                    "note": "Theme membership — vaccine franchise via transcripts / theme charts",
                }
            )
            existing_pairs.add(key)
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
                    "series_key": "cdc_nis_flu_coverage",
                    "metric": "raw",
                    "chart_type": "line",
                    "section": "primary",
                    "why_it_matters": (
                        "CDC adult flu coverage is the free weekly uptake pulse for "
                        "seasonal respiratory franchises."
                    ),
                },
                {
                    "series_key": "cdc_nis_rsv_coverage",
                    "metric": "raw",
                    "chart_type": "line",
                    "section": "primary",
                    "why_it_matters": (
                        "Adult RSV coverage (50+) tracks Arexvy / Abrysvo / mRESVIA "
                        "eligible-population absorption."
                    ),
                },
                {
                    "series_key": "cdc_nis_covid_coverage",
                    "metric": "raw",
                    "chart_type": "line",
                    "section": "primary",
                    "why_it_matters": (
                        "COVID booster coverage is the demand backdrop for Spikevax / "
                        "Comirnaty / Nuvaxovid."
                    ),
                },
                {
                    "series_key": "cdc_nis_flu_refusal",
                    "metric": "raw",
                    "chart_type": "line",
                    "section": "primary",
                    "why_it_matters": (
                        "Flu refusal intent is the free hesitancy / confidence bear "
                        "proxy (Bear1)."
                    ),
                },
                {
                    "chart_type": "stacked_bar",
                    "metric": "raw",
                    "display_name": "Vaccine pure-play revenue",
                    "series_keys": [
                        "mrna_revenue",
                        "bntx_revenue",
                        "nvax_revenue",
                        "ebs_revenue",
                    ],
                    "sum_series_key": "vaccine_pureplay_revenue",
                    "section": "primary",
                    "why_it_matters": (
                        "Pure-play vaccine revenue (ex-MRK/PFE mega-blobs) is the "
                        "commercial proof of theme trajectory."
                    ),
                },
                {
                    "chart_type": "stacked_bar",
                    "metric": "raw",
                    "display_name": "Moderna COVID + RSV",
                    "series_keys": ["mrna_covid_revenue", "mrna_rsv_revenue"],
                    "sum_series_key": "mrna_respiratory_franchise",
                    "section": "primary",
                    "why_it_matters": (
                        "MRNA annual COVID vs RSV splits the post-pandemic franchise "
                        "mix (Bull1 adult RSV)."
                    ),
                },
                {
                    "series_key": "nvax_nuvaxovid_revenue",
                    "metric": "raw",
                    "chart_type": "bar",
                    "section": "primary",
                    "why_it_matters": (
                        "Nuvaxovid is the protein COVID commercial print — "
                        "differentiates from mRNA peers."
                    ),
                },
                {
                    "chart_type": "stacked_bar",
                    "metric": "raw",
                    "display_name": "Emergent product + contracts",
                    "series_keys": ["ebs_product_revenue", "ebs_contracts_grants"],
                    "section": "primary",
                    "why_it_matters": (
                        "EBS Product + Contracts/Grants is the biodefense / BARDA "
                        "stockpile franchise (Bull3)."
                    ),
                },
                {
                    "chart_type": "stacked_bar",
                    "metric": "raw",
                    "display_name": "Valneva travel vaccines",
                    "series_keys": ["valn_ixiaro_revenue", "valn_dukoral_revenue"],
                    "sum_series_key": "valn_travel_vaccine_revenue",
                    "section": "primary",
                    "why_it_matters": (
                        "IXIARO + DUKORAL are rare public travel / vector-borne "
                        "vaccine prints."
                    ),
                },
                {
                    "series_key": "vaccine_franchise_transcripts",
                    "metric": "raw",
                    "chart_type": "bar",
                    "section": "primary",
                    "why_it_matters": (
                        "Earnings-call hits on Gardasil / Shingrix / Arexvy / Prevnar "
                        "/ Spikevax / ACIP proxy big-pharma vaccine narrative where "
                        "FMP segments fail."
                    ),
                },
                {
                    "series_key": "clinicaltrials_infectious_vaccines",
                    "metric": "m3_avg",
                    "chart_type": "bar",
                    "section": "primary",
                    "why_it_matters": (
                        "New infectious-disease vaccine trial posts track pipeline "
                        "breadth (ex-oncology cancer vaccines)."
                    ),
                },
                {
                    "series_key": "usaspending_vaccine_biodefense",
                    "metric": "m3_avg",
                    "chart_type": "bar",
                    "section": "primary",
                    "why_it_matters": (
                        "Federal vaccine/biodefense obligations are the free "
                        "procurement pulse for EBS / stockpile names."
                    ),
                },
                {
                    "series_key": "census_vaccine_imports_hs300220",
                    "metric": "m3_avg",
                    "chart_type": "line",
                    "section": "primary",
                    "why_it_matters": (
                        "HS 300220 human vaccine imports are a soft supply / trade "
                        "backdrop."
                    ),
                },
                {
                    "series_key": "kalshi_cdc_flu_rec_ended",
                    "metric": "raw",
                    "chart_type": "line",
                    "section": "primary",
                    "why_it_matters": (
                        "Kalshi odds that CDC ends the flu recommendation price "
                        "ACIP / policy risk (Bear1)."
                    ),
                },
                {
                    "series_key": "kalshi_mpox_cases_2026",
                    "metric": "raw",
                    "chart_type": "line",
                    "section": "primary",
                    "why_it_matters": (
                        "Elevated mpox case odds are optional demand for JYNNEOS / "
                        "BAVA-class biodefense."
                    ),
                },
                {
                    "series_key": "vm_vaccine_talk_mentions",
                    "metric": "raw",
                    "chart_type": "bar",
                    "section": "primary",
                    "display_name": "Forum adult vaccine talk",
                    "why_it_matters": (
                        "Pharmacy/biotech forum talk on adult immunization tracks "
                        "field sentiment ahead of lagged quarterlies."
                    ),
                },
                {
                    "chart_type": "stacked_bar",
                    "metric": "raw",
                    "display_name": "Forum named franchise mentions",
                    "series_keys": [
                        "vm_shingrix_arexvy_mentions",
                        "vm_pneumococcal_mentions",
                        "vm_mrna_covid_mentions",
                    ],
                    "section": "primary",
                    "why_it_matters": (
                        "Named franchise chatter separates RSV/shingles vs pneumo "
                        "vs mRNA COVID attention."
                    ),
                },
            ],
        }
    )
    for ticker, keys in (
        ("MRNA", ["mrna_revenue", "mrna_covid_revenue", "mrna_rsv_revenue"]),
        ("BNTX", ["bntx_revenue"]),
        ("NVAX", ["nvax_nuvaxovid_revenue", "nvax_revenue"]),
        ("EBS", ["ebs_product_revenue", "ebs_contracts_grants", "ebs_revenue"]),
        ("VALN", ["valn_ixiaro_revenue", "valn_dukoral_revenue"]),
        ("VALN.PA", ["valn_ixiaro_revenue", "valn_dukoral_revenue"]),
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
                        "chart_type": "bar" if "revenue" in k else "line",
                    }
                    for k in keys
                ],
            }
        )
    boards["boards"] = boards_list
    boards_path.write_text(json.dumps(boards, indent=2) + "\n")
    print("boards: rewritten for theme")

    scores_path = ROOT / "config" / "theme_scores.json"
    scores = json.loads(scores_path.read_text())
    # theme_scores.json uses top-level "themes" in newer layout — detect
    arr_key = "themes" if isinstance(scores.get("themes"), list) else None
    if arr_key is None:
        for k, v in scores.items():
            if (
                isinstance(v, list)
                and v
                and isinstance(v[0], dict)
                and "theme_slug" in v[0]
                and "components" in v[0]
            ):
                arr_key = k
                break
    if arr_key is None:
        scores.setdefault("themes", [])
        arr_key = "themes"
    arr = scores[arr_key]
    arr = [t for t in arr if t.get("theme_slug") != SLUG]
    arr.append(
        {
            "theme": THEME,
            "theme_slug": SLUG,
            "score_id": "vaccine_makers_backdrop",
            "label": "Vaccine makers backdrop",
            "pack": PACK,
            "status": "active",
            "signal": {
                "yoy_clip_pct": 25.0,
                "exclude_incomplete_month": True,
                "history_smooth_months": 3,
                "note": (
                    "NIS flu/RSV coverage + pure-play revenue + EBS contracts + "
                    "Nuvaxovid + transcripts − flu refusal − Kalshi flu-rec risk. "
                    "No MRK/PFE mega-segment legs."
                ),
            },
            "components": [
                {
                    "series_key": "cdc_nis_flu_coverage",
                    "metric": "yoy_pct",
                    "weight": 0.18,
                    "polarity": 1.0,
                    "enabled": True,
                    "rationale": "Adult flu coverage — seasonal franchise uptake.",
                },
                {
                    "series_key": "cdc_nis_rsv_coverage",
                    "metric": "yoy_pct",
                    "weight": 0.14,
                    "polarity": 1.0,
                    "enabled": True,
                    "rationale": "Adult RSV coverage — Arexvy/Abrysvo/mRESVIA pulse.",
                },
                {
                    "series_key": "cdc_nis_flu_refusal",
                    "metric": "yoy_pct",
                    "weight": 0.12,
                    "polarity": -1.0,
                    "enabled": True,
                    "rationale": "Rising refusal intent → hesitancy / Bear1.",
                },
                {
                    "series_key": "vaccine_pureplay_revenue",
                    "metric": "yoy_pct",
                    "weight": 0.16,
                    "polarity": 1.0,
                    "enabled": True,
                    "rationale": "MRNA+BNTX+NVAX+EBS commercial trajectory.",
                },
                {
                    "series_key": "nvax_nuvaxovid_revenue",
                    "metric": "yoy_pct",
                    "weight": 0.08,
                    "polarity": 1.0,
                    "enabled": True,
                    "rationale": "Protein COVID franchise print.",
                },
                {
                    "series_key": "ebs_contracts_grants",
                    "metric": "yoy_pct",
                    "weight": 0.10,
                    "polarity": 1.0,
                    "enabled": True,
                    "rationale": "Government biodefense / BARDA contracts.",
                },
                {
                    "series_key": "vaccine_franchise_transcripts",
                    "metric": "yoy_pct",
                    "weight": 0.10,
                    "polarity": 1.0,
                    "enabled": True,
                    "rationale": "Big-pharma adult franchise narrative intensity.",
                },
                {
                    "series_key": "usaspending_vaccine_biodefense",
                    "metric": "yoy_pct",
                    "weight": 0.06,
                    "polarity": 1.0,
                    "enabled": True,
                    "rationale": "Federal vaccine/biodefense obligations.",
                },
                {
                    "series_key": "kalshi_cdc_flu_rec_ended",
                    "metric": "yoy_pct",
                    "weight": 0.06,
                    "polarity": -1.0,
                    "enabled": False,
                    "rationale": (
                        "DISABLED until Kalshi has ~1y path for YoY — keep "
                        "chart-only (raw odds are not YoY-clipped signals)."
                    ),
                },
            ],
            "notes": (
                "Coverage YoY may be thin until ~52 weeks of NIS history — "
                "omit-missing handles gaps. Cap MRNA annual COVID/RSV as chart-only "
                "until multi-year prints exist. Kalshi flu-rec chart-only for now."
            ),
        }
    )
    scores[arr_key] = arr
    scores_path.write_text(json.dumps(scores, indent=2) + "\n")
    print("theme_scores: upserted")

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
            "due": {"weekdays": ["Thu"], "max_age_days": 8},
            "freshness": {
                "r2_key": f"themedata/{PACK}/metrics_latest.json",
                "as_of_field": "as_of",
                "local_relative": f"data/{PACK}/latest.json",
            },
            "description": (
                "Weekly vaccine makers — FMP pure-plays + CDC NIS + CT.gov + "
                "USASpending + Census HS + Kalshi."
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
            "due": {"weekdays": ["Thu"], "max_age_days": 8},
            "freshness": {
                "r2_key": f"themedata/{FORUMS}/metrics_latest.json",
                "as_of_field": "as_of",
                "local_relative": f"data/{FORUMS}/latest.json",
            },
            "description": "Weekly vaccine makers forums (Arctic Shift pharmacy+biotech).",
        }
    )
    sched["packs"] = packs
    sched_path.write_text(json.dumps(sched, indent=2) + "\n")
    print("schedule: upserted")
    print("done")


if __name__ == "__main__":
    main()
