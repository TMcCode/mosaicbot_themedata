#!/usr/bin/env python3
"""Daily dispatcher: run themedata packs that are due on this host.

Reads config/update_schedule.json. Safe to cron daily — packs are skipped
when outside their window and last success is still fresh.

Examples:
  python3 jobs/dispatch_due.py --dry-run
  python3 jobs/dispatch_due.py --force lab_economics
  THEMEDATA_HOST=vps python3 jobs/dispatch_due.py
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lib.env import load_env  # noqa: E402
from lib import r2 as r2lib  # noqa: E402

SCHEDULE_PATH = _ROOT / "config" / "update_schedule.json"
STATUS_LOCAL = _ROOT / "data" / "scheduler" / "last_runs.json"
STATUS_R2_KEY = "themedata/scheduler/last_runs.json"
LOCK_PATH = _ROOT / "data" / "scheduler" / "dispatch.lock"
WEEKDAY_MAP = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}


def _now_tz(tz_name: str) -> datetime:
    return datetime.now(ZoneInfo(tz_name))


def _parse_as_of(value: Any) -> date | None:
    if not value:
        return None
    s = str(value)[:10]
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _load_schedule() -> dict:
    return json.loads(SCHEDULE_PATH.read_text(encoding="utf-8"))


def _load_status() -> dict:
    if STATUS_LOCAL.exists():
        try:
            return json.loads(STATUS_LOCAL.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    try:
        remote = r2lib.get_json(STATUS_R2_KEY)
        if isinstance(remote, dict):
            return remote
    except Exception:
        pass
    return {"schema_version": 0, "packs": {}}


def _save_status(status: dict, *, skip_r2: bool) -> None:
    STATUS_LOCAL.parent.mkdir(parents=True, exist_ok=True)
    STATUS_LOCAL.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
    if not skip_r2:
        try:
            r2lib.put_json(STATUS_R2_KEY, status)
        except Exception as e:
            print(f"WARN: could not write R2 status: {e}", file=sys.stderr)


def _host_matches(pack: dict, host: str) -> bool:
    allowed = pack.get("hosts") or ["any"]
    if host == "any":
        return True
    return host in allowed or "any" in allowed


def _read_freshness_as_of(pack: dict) -> date | None:
    fresh = pack.get("freshness") or {}
    field = fresh.get("as_of_field") or "as_of"
    payload = None
    r2_key = fresh.get("r2_key")
    if r2_key:
        try:
            payload = r2lib.get_json(r2_key)
        except Exception as e:
            print(f"WARN: freshness R2 read failed for {pack['id']}: {e}", file=sys.stderr)
    if payload is None:
        rel = fresh.get("local_relative")
        if rel:
            path = _ROOT / rel
            if path.exists():
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    payload = None
    if not isinstance(payload, dict):
        return None
    return _parse_as_of(payload.get(field))


def _last_success_as_of(status: dict, pack_id: str) -> date | None:
    row = (status.get("packs") or {}).get(pack_id) or {}
    return _parse_as_of(row.get("as_of")) or _parse_as_of(row.get("finished_at"))


def is_due(
    pack: dict,
    *,
    today: date,
    status: dict,
    force: bool,
) -> tuple[bool, str]:
    if force:
        return True, "forced"
    if not pack.get("enabled", True):
        return False, "disabled"

    due = pack.get("due") or {}
    pack_id = pack["id"]
    reasons: list[str] = []

    # Already succeeded today → skip (idempotent daily cron)
    last = (status.get("packs") or {}).get(pack_id) or {}
    last_ok = _parse_as_of(last.get("last_success_at"))
    if last.get("ok") and last_ok == today:
        return False, "already succeeded today"

    in_window = True
    if "weekdays" in due:
        wanted = {WEEKDAY_MAP[d] for d in due["weekdays"] if d in WEEKDAY_MAP}
        if today.weekday() not in wanted:
            in_window = False
            reasons.append(f"weekday={today.strftime('%a')} not in {due['weekdays']}")
        else:
            reasons.append(f"weekday={today.strftime('%a')}")

    d_from = due.get("day_of_month_from")
    d_to = due.get("day_of_month_to")
    if d_from is not None and d_to is not None:
        if not (int(d_from) <= today.day <= int(d_to)):
            in_window = False
            reasons.append(f"day={today.day} outside {d_from}-{d_to}")
        else:
            reasons.append(f"day={today.day} in {d_from}-{d_to}")

    max_age = due.get("max_age_days")
    as_of = _read_freshness_as_of(pack) or _last_success_as_of(status, pack_id)
    stale = False
    if max_age is not None and as_of is not None:
        age = (today - as_of).days
        if age >= int(max_age):
            stale = True
            reasons.append(f"stale as_of={as_of.isoformat()} age={age}d>={max_age}")
        else:
            reasons.append(f"fresh as_of={as_of.isoformat()} age={age}d<{max_age}")
    elif max_age is not None and as_of is None:
        stale = True
        reasons.append("no prior as_of")

    if in_window or stale:
        return True, "; ".join(reasons) or "due"
    return False, "; ".join(reasons) or "not due"


def run_pack(pack: dict, *, python: str) -> tuple[int, str]:
    job = pack["job"]
    script = _ROOT / job
    if not script.exists():
        return 2, f"missing job script: {script}"
    proc = subprocess.run(
        [python, str(script)],
        cwd=str(_ROOT),
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out[-8000:]


def dispatch(
    *,
    host: str,
    dry_run: bool,
    force_ids: set[str] | None,
    only: set[str] | None,
    skip_r2: bool,
    python: str,
) -> int:
    load_env()
    schedule = _load_schedule()
    tz_name = schedule.get("timezone") or "America/New_York"
    now = _now_tz(tz_name)
    today = now.date()
    status = _load_status()
    status.setdefault("schema_version", 0)
    status.setdefault("packs", {})

    results: list[dict[str, Any]] = []
    exit_code = 0

    for pack in schedule.get("packs") or []:
        pid = pack["id"]
        if only and pid not in only:
            continue
        if not _host_matches(pack, host):
            results.append({"id": pid, "action": "skip", "reason": f"host={host} not in {pack.get('hosts')}"})
            continue

        force = bool(force_ids) and (pid in force_ids or "*" in force_ids)
        due, reason = is_due(pack, today=today, status=status, force=force)
        if not due:
            results.append({"id": pid, "action": "skip", "reason": reason})
            continue

        if dry_run:
            results.append({"id": pid, "action": "would_run", "reason": reason})
            continue

        started = datetime.now(timezone.utc).isoformat()
        code, output = run_pack(pack, python=python)
        finished = datetime.now(timezone.utc).isoformat()
        as_of = _read_freshness_as_of(pack)
        row = {
            "last_attempt_at": finished,
            "last_started_at": started,
            "last_exit_code": code,
            "last_reason": reason,
            "host": host,
            "as_of": as_of.isoformat() if as_of else None,
            "ok": code == 0,
            "tail": output[-1500:],
        }
        if code == 0:
            row["last_success_at"] = finished
        else:
            exit_code = 1
        status["packs"][pid] = {**(status["packs"].get(pid) or {}), **row}
        results.append(
            {
                "id": pid,
                "action": "ran",
                "exit_code": code,
                "reason": reason,
                "as_of": row["as_of"],
            }
        )

    status["last_dispatch_at"] = datetime.now(timezone.utc).isoformat()
    status["last_dispatch_host"] = host
    status["last_dispatch_results"] = results
    if not dry_run:
        _save_status(status, skip_r2=skip_r2)

    print(
        json.dumps(
            {
                "host": host,
                "today": today.isoformat(),
                "timezone": tz_name,
                "dry_run": dry_run,
                "results": results,
            },
            indent=2,
        )
    )
    return exit_code


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--host",
        default=os.environ.get("THEMEDATA_HOST", "vps"),
        help="This machine's role: vps | macmini | any (default: THEMEDATA_HOST or vps)",
    )
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--force",
        nargs="*",
        metavar="PACK",
        help="Force run; optional pack ids (default all matching host if flag alone)",
    )
    p.add_argument("--only", nargs="+", metavar="PACK", help="Restrict to these pack ids")
    p.add_argument("--skip-r2", action="store_true", help="Do not read/write scheduler status on R2")
    p.add_argument("--python", default=sys.executable)
    args = p.parse_args(argv)

    force_ids: set[str] | None = None
    if args.force is not None:
        force_ids = set(args.force) if args.force else {"*"}

    # --force PACK implies only those packs; bare --force = all on this host
    only = set(args.only) if args.only else None
    if force_ids and "*" not in force_ids:
        only = force_ids if only is None else (only & force_ids)

    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_fh = open(LOCK_PATH, "a+", encoding="utf-8")
    try:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("FAILED: another dispatch_due is running (lock held)", file=sys.stderr)
        return 3

    try:
        return dispatch(
            host=args.host,
            dry_run=args.dry_run,
            force_ids=force_ids,
            only=only,
            skip_r2=args.skip_r2,
            python=args.python,
        )
    finally:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
        lock_fh.close()


if __name__ == "__main__":
    raise SystemExit(main())
