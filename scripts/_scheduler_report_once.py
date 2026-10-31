#!/usr/bin/env python3
"""One-off scheduler report; writes JSON to stdout."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lib.env import load_env
from lib.r2 import get_json

MISSING = [
    "beauty_prestige",
    "beauty_prestige_forums",
    "ai_resilient_software",
    "ai_resilient_software_forums",
    "political_spend",
    "political_spend_forums",
    "memory_cxl",
    "memory_cxl_forums",
    "memory_nand",
    "memory_nand_forums",
    "memory_dram",
    "memory_graphics_mobile",
    "memory_graphics_mobile_forums",
    "memory_dram_forums",
    "memory_essd",
    "memory_essd_forums",
    "memory_hbm_testing",
    "memory_hbm_packaging",
    "memory_hbm_testing_forums",
    "crypto_brokerages",
    "crypto_brokerages_forums",
    "regulatory_testing",
    "regulatory_testing_forums",
    "advanced_packaging",
]


def main() -> int:
    load_env()
    status = get_json("themedata/scheduler/last_runs.json") or {}
    schedule = json.loads((_ROOT / "config/update_schedule.json").read_text())
    sched_ids = {
        p["id"]
        for p in schedule.get("packs", [])
        if p.get("enabled", True) and p.get("id")
    }
    packs_status = status.get("packs") or status.get("last_runs") or {}
    dispatch_results = status.get("last_dispatch_results") or []
    dr_by_id = {r.get("id") or r.get("pack_id"): r for r in dispatch_results if r.get("id") or r.get("pack_id")}

    def pack_row(pid: str) -> dict:
        lr = packs_status.get(pid) or {}
        dr = dr_by_id.get(pid)
        age = None
        as_of = lr.get("as_of")
        if as_of and lr.get("last_success_at"):
            pass
        return {
            "in_schedule": pid in sched_ids,
            "dispatch": None
            if dr is None
            else {
                "action": dr.get("action"),
                "exit_code": dr.get("exit_code"),
                "reason": dr.get("reason"),
                "as_of": dr.get("as_of"),
            },
            "last_run": None
            if not lr
            else {
                "ok": lr.get("ok"),
                "last_exit_code": lr.get("last_exit_code"),
                "last_attempt_at": lr.get("last_attempt_at"),
                "last_success_at": lr.get("last_success_at"),
                "as_of": lr.get("as_of"),
                "host": lr.get("host"),
            },
        }

    failed = [
        {
            "id": r.get("id"),
            "exit_code": r.get("exit_code"),
            "reason": (r.get("reason") or "")[:200],
        }
        for r in dispatch_results
        if r.get("action") == "ran" and r.get("exit_code") not in (0, None)
    ]
    ran = sorted(
        r.get("id")
        for r in dispatch_results
        if r.get("action") == "ran" and (r.get("id") or r.get("pack_id"))
    )

    out = {
        "last_dispatch_at": status.get("last_dispatch_at"),
        "last_dispatch_host": status.get("last_dispatch_host"),
        "counts": {
            "schedule_enabled": len(sched_ids),
            "status_packs": len(packs_status),
            "last_dispatch_results": len(dispatch_results),
        },
        "missing_pack_audit": {pid: pack_row(pid) for pid in MISSING},
        "failed_runs": failed,
        "ran_in_last_dispatch": ran,
    }
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
