"""Environment and runtime configuration.

The single place in the package that touches ``os.environ``. Every other module
imports its settings from here so that model choice, seeding, and filesystem
layout have exactly one definition.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

# Read .env if one exists; a missing file is not an error (CI and tests run without one).
# override=True so .env is the authoritative source in local dev: a stale OPENAI_API_KEY
# left over in the shell/OS environment from unrelated work must not silently beat the
# key the developer just put in .env.
load_dotenv(override=True)

MODEL_ID: str = os.getenv("CLOUD_ASSISTANT_MODEL", "openai:gpt-4o-mini")
"""Model identifier passed to ``init_chat_model``, e.g. ``openai:gpt-4o-mini``."""

MOCK_SEED: int = int(os.getenv("CLOUD_ASSISTANT_SEED", "1337"))
"""Base seed mixed with the account id to make every fixture deterministic."""


def _resolve_reference_date(raw: str) -> date:
    """Parse the reference date, accepting an ISO date or the literal 'today'."""
    value = raw.strip().lower()
    if value == "today":
        return date.today()
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise RuntimeError(
            f"CLOUD_ASSISTANT_REFERENCE_DATE must be an ISO date (YYYY-MM-DD) or 'today', got {raw!r}"
        ) from exc


REFERENCE_DATE: date = _resolve_reference_date(os.getenv("CLOUD_ASSISTANT_REFERENCE_DATE", "2026-08-10"))
"""The date the mock cloud believes it is. Pinned by default so transcripts are byte-stable
across runs and months; set ``CLOUD_ASSISTANT_REFERENCE_DATE=today`` for a live-looking window."""

FAULT_ACCOUNT_ID: str = "999999999999"
"""Reserved account whose tool calls always return malformed payloads."""

CLEAN_ACCOUNT_ID: str = "111111111111"
"""Reserved account with no idle resources and no security findings; drives both skip branches."""

CLEAN_ACCOUNT_IDS: frozenset[str] = frozenset(
    {CLEAN_ACCOUNT_ID, "444455556666", "777788889999"}
)
"""Every account id treated as clean. Includes the two account ids named in the
spec's own sample scenarios (cost skip-branch and security skip-branch) in
addition to the primary ``CLEAN_ACCOUNT_ID``, so a request naming either literal
account number still exercises the skip branch it was written to demonstrate."""

DEFAULT_ACCOUNT_ID: str = "222222222222"
"""Account assumed when a request names none and state carries none."""

TRANSCRIPT_DIR: Path = Path("transcripts")
"""Directory for per-scenario JSON transcripts."""

LOG_PATH: Path = TRANSCRIPT_DIR / "run.log"
"""JSON-line decision log written by ``logging_setup.configure_logging``."""


def require_api_key() -> None:
    """Raise ``RuntimeError`` if ``OPENAI_API_KEY`` is missing; call at run time, never at import."""
    if not os.getenv("OPENAI_API_KEY", "").strip():
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Add it to the .env file in the project root, "
            "or export OPENAI_API_KEY in the shell before running the demo."
        )
