#!/usr/bin/env python3
"""Publish boards/attachments to R2 and summarize LDES pack state."""

from __future__ import annotations

import json
from pathlib import Path

from lib.env import load_env
from lib import r2 as r2lib

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    load_env()
    boards = json.loads((ROOT / "config" / "default_boards.json").read_text())
    att = json.loads((ROOT / "config" / "series_attachments.json").read_text())
    print(r2lib.put_json("themedata/boards.json", boards))
    print(r2lib.put_json("themedata/series_attachments.json", att))

    d = json.loads((ROOT / "data" / "ldes" / "latest.json").read_text())
    print(
        "ldes",
        "as_of=",
        d.get("as_of"),
        "n=",
        len(d.get("series") or {}),
        "errors=",
        d.get("errors"),
    )
    print("series", sorted(d.get("series") or {}))
    print("theme_scores", d.get("theme_scores"))

    f = ROOT / "data" / "ldes_forums" / "latest.json"
    if f.exists():
        fd = json.loads(f.read_text())
        print(
            "forums",
            "as_of=",
            fd.get("as_of"),
            "n=",
            len(fd.get("series") or {}),
            "errors=",
            len(fd.get("errors") or []),
        )


if __name__ == "__main__":
    main()
