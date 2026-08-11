"""Tools bound to the sub-agents.

Every tool in this package follows one execution pattern: log the call, consult
the fault injector, produce a payload from the fixtures, convert any unexpected
exception into an error dict, and log the result with its duration.

``guarded_call`` implements that pattern once so all six tools behave identically
under failure. This matters more than it looks: a tool that raises would escape
into the agent's tool-calling loop and kill the run, so "never raise" is an
invariant of the whole design rather than a nicety of each tool.

This module deliberately does *not* re-export the tools themselves — agents
import them from their specific modules, which keeps this import edge one-way.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from cloud_assistant.logging_setup import log_decision
from cloud_assistant.mock_data.errors import maybe_inject_fault


def guarded_call(
    tool_name: str,
    account_id: str | None,
    produce: Callable[[], dict[str, Any]],
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a tool body with logging, fault injection, and never-raise error handling."""
    log_decision("tool", "tool_call", {"tool": tool_name, "account_id": account_id, **(detail or {})})
    started = time.perf_counter()
    try:
        fault = maybe_inject_fault(account_id, tool_name) if account_id else None
        payload = fault if fault is not None else produce()
    except Exception as exc:  # noqa: BLE001 — a tool must never raise into the agent loop
        payload = {"Error": {"Code": type(exc).__name__, "Message": str(exc)}}
    log_decision(
        "tool",
        "tool_result",
        {
            "tool": tool_name,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            # A structurally malformed payload has no "Error" key and is reported
            # ok=True here on purpose: noticing it is the agent's job, not the
            # wrapper's, and the demo needs that failure mode to reach the model.
            "ok": "Error" not in payload,
        },
    )
    return payload
