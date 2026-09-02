"""Load repo-root .env into os.environ (no third-party dotenv)."""

from __future__ import annotations

import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_LOADED = False


def load_env(*, env_path: Path | None = None, override: bool = False) -> Path:
    """Parse KEY=VALUE lines from .env into os.environ.

    Existing env vars win unless override=True.
    """
    global _LOADED
    path = env_path or (_ROOT / ".env")
    if not path.exists():
        _LOADED = True
        return path
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        if not key:
            continue
        if override or key not in os.environ:
            os.environ[key] = val
    _LOADED = True
    return path


def require_env(*keys: str) -> dict[str, str]:
    load_env()
    missing = [k for k in keys if not os.environ.get(k, "").strip()]
    if missing:
        raise RuntimeError(f"missing env: {', '.join(missing)} (see {_ROOT / '.env'})")
    return {k: os.environ[k].strip() for k in keys}
