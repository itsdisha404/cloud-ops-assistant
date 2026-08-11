"""Persisted history of API-triggered graph runs.

One JSON file per run, same convention the demo script already uses for its
own scenario transcripts, rather than a second storage format for the same
kind of record. Listing just stats the directory — this is a small local
tool, not a service with enough run volume to need an index or a database.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from cloud_assistant import config

RUN_HISTORY_DIR: Path = config.TRANSCRIPT_DIR / "api_runs"

_SUMMARY_KEYS = (
    "run_id",
    "started_at",
    "request",
    "account_id",
    "workflow",
    "path",
    "final_response",
    "error",
    "error_node",
    "agents_called",
    "tools_called",
)


def new_run_id() -> str:
    """A short, URL-safe id for one run."""
    return uuid.uuid4().hex[:12]


def save_run(record: dict[str, Any]) -> Path:
    """Write one run record to its own file and return the path written."""
    RUN_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    stamp = record["started_at"].replace(":", "").replace("-", "").replace(".", "")
    path = RUN_HISTORY_DIR / f"{stamp}_{record['run_id']}.json"
    path.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
    return path


def list_runs(limit: int = 50) -> list[dict[str, Any]]:
    """Return run summaries, most recent first."""
    if not RUN_HISTORY_DIR.exists():
        return []
    files = sorted(RUN_HISTORY_DIR.glob("*.json"), key=lambda p: p.name, reverse=True)
    summaries: list[dict[str, Any]] = []
    for f in files[:limit]:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        summaries.append({key: data.get(key) for key in _SUMMARY_KEYS})
    return summaries


def load_run(run_id: str) -> dict[str, Any] | None:
    """Return the full record for one run id, or None if it doesn't exist."""
    if not RUN_HISTORY_DIR.exists():
        return None
    for f in RUN_HISTORY_DIR.glob(f"*_{run_id}.json"):
        return json.loads(f.read_text(encoding="utf-8"))
    return None
