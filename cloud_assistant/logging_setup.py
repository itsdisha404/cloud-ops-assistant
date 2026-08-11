"""JSON-line decision logging.

The goal is that a reviewer can reconstruct a full graph traversal from the log
file alone — which workflow was chosen, which tools ran, which branch each router
took, and where anything failed. One line per decision, one JSON object per line,
so the log is greppable by hand and parseable by a test.

``log_decision`` is the only logging entry point the rest of the package uses.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from cloud_assistant import config

LOGGER_NAME: Final[str] = "cloud_assistant"

EVENTS: Final[tuple[str, ...]] = (
    "classified",
    "routing",
    "invoking_subagent",
    "subagent_completed",
    "tool_call",
    "tool_result",
    "node_error",
    "scenario_start",
    "scenario_complete",
    "query_start",
    "query_complete",
)
"""The closed event vocabulary. Keeping it short is what keeps the log greppable."""

_CONSOLE_VALUE_LIMIT: Final[int] = 70


class JsonLineFormatter(logging.Formatter):
    """Render one log record as a single compact JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        """Emit ``{"ts","level","component","event","detail"}`` on one line."""
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "component": getattr(record, "component", "unknown"),
            "event": getattr(record, "event", record.getMessage()),
            "detail": getattr(record, "detail", {}),
        }
        # default=str so a Pydantic model or enum in a detail dict can never
        # raise inside the formatter and take down a run mid-flight.
        return json.dumps(payload, default=str, separators=(",", ":"))


class ConsoleFormatter(logging.Formatter):
    """Render one log record as a short human-readable line for the terminal."""

    def format(self, record: logging.LogRecord) -> str:
        """Emit ``HH:MM:SS component event key=value ...`` with long values truncated."""
        component = getattr(record, "component", "unknown")
        event = getattr(record, "event", record.getMessage())
        detail: dict[str, Any] = getattr(record, "detail", {})
        rendered = " ".join(f"{key}={_shorten(value)}" for key, value in detail.items())
        stamp = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        return f"{stamp}  {component:<22} {event:<20} {rendered}".rstrip()


def _shorten(value: object) -> str:
    """Collapse a detail value to a single short string for console display."""
    text = str(value).replace("\n", " ")
    return text if len(text) <= _CONSOLE_VALUE_LIMIT else f"{text[:_CONSOLE_VALUE_LIMIT]}..."


def configure_logging(log_path: Path = config.LOG_PATH, level: int = logging.INFO) -> logging.Logger:
    """Attach one file and one stream handler to the package logger; safe to call repeatedly."""
    logger = logging.getLogger(LOGGER_NAME)
    if logger.handlers:
        # Idempotent on purpose: a second demo run in the same process must not
        # duplicate every line and silently double-count the Step 14 grep checks.
        return logger

    logger.setLevel(level)
    logger.propagate = False  # never hand records to the root logger as well

    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    file_handler = logging.FileHandler(path, encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(JsonLineFormatter())
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(level)
    stream_handler.setFormatter(ConsoleFormatter())
    logger.addHandler(stream_handler)

    return logger


def log_decision(component: str, event: str, detail: dict[str, Any] | None = None) -> None:
    """Record one decision point; the only logging call site in the package."""
    logging.getLogger(LOGGER_NAME).info(
        event,
        extra={"component": component, "event": event, "detail": detail or {}},
    )
