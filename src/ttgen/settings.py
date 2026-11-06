"""Deployment settings, all read from the environment.

Nothing here is secret on its own; the secrets (password hashes, session
tokens) live in the SQLite database under ``state_dir``.  Read on every call
rather than cached so tests and the CLI can point a running process at a
different directory by setting a variable.

=========================  ==================================================
TTGEN_HOME                 where the database and saved setups live
                           (default: ``data/`` beside the code)
TTGEN_ORG_NAME             the institution's name shown on every page
TTGEN_SESSION_HOURS        how long a sign-in lasts (default 12)
TTGEN_SECURE_COOKIES       ``1`` once the site is served over HTTPS
TTGEN_ADMIN_USERNAME /     create this administrator at start-up if no
TTGEN_ADMIN_PASSWORD       account exists yet (handy for Docker)
=========================  ==================================================
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from . import __version__

PRODUCT = "Timetable"


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def home() -> Path:
    env = os.environ.get("TTGEN_HOME")
    if env:
        return Path(env)
    repo = Path(__file__).resolve().parents[2] / "data"
    return repo if repo.is_dir() else Path.cwd() / "data"


@dataclass(frozen=True)
class Settings:
    org_name: str
    session_hours: float
    secure_cookies: bool
    state_dir: Path
    version: str = __version__
    product: str = PRODUCT


def get() -> Settings:
    try:
        hours = float(os.environ.get("TTGEN_SESSION_HOURS", "12"))
    except ValueError:
        hours = 12.0
    return Settings(
        org_name=os.environ.get("TTGEN_ORG_NAME", "PSG College of Technology").strip(),
        session_hours=max(0.25, hours),
        secure_cookies=_flag("TTGEN_SECURE_COOKIES"),
        state_dir=Path(os.environ.get("TTGEN_STATE_DIR") or (home() / "state")),
    )
