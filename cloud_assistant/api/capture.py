"""Capture the decision-log records emitted by one graph run.

``log_decision`` already fires at every classification, routing choice, agent
invocation and tool call (see ``logging_setup.py``) — the log file is meant to
let a reader reconstruct a run without touching code. This module taps that
same stream instead of adding a second instrumentation path.

One global buffer guarded by a lock, not a thread-local one: an earlier
version scoped the buffer to the calling thread, on the assumption that a
graph run stays on whichever thread FastAPI handed the request. It doesn't —
``create_agent``'s ReAct loop dispatches at least some tool execution onto a
worker thread of its own, so a thread-local buffer silently missed every
``tool_call``/``tool_result`` record. The lock instead serializes graph runs
one at a time process-wide, which a local single-user test tool doesn't need
to avoid anyway, and guarantees nothing is missed regardless of which thread
LangChain/LangGraph actually run tool calls on.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from cloud_assistant.logging_setup import LOGGER_NAME

_run_lock = threading.Lock()
_buffer: list[dict[str, Any]] | None = None

_install_lock = threading.Lock()
_installed = False


class _CaptureHandler(logging.Handler):
    """Append each record to the active buffer, if a run is in progress."""

    def emit(self, record: logging.LogRecord) -> None:
        if _buffer is not None:
            _buffer.append(
                {
                    "ts": time.strftime("%H:%M:%S", time.localtime(record.created)),
                    "component": getattr(record, "component", "unknown"),
                    "event": getattr(record, "event", record.getMessage()),
                    "detail": getattr(record, "detail", {}) or {},
                }
            )


_handler = _CaptureHandler()


def _ensure_installed() -> None:
    global _installed
    if _installed:
        return
    with _install_lock:
        if not _installed:
            logging.getLogger(LOGGER_NAME).addHandler(_handler)
            _installed = True


@contextmanager
def capture_decisions() -> Iterator[list[dict[str, Any]]]:
    """Serialize one graph run and collect every log_decision(...) it makes,
    on whichever thread each call happens to fire on."""
    global _buffer
    _ensure_installed()
    with _run_lock:
        _buffer = []
        try:
            yield _buffer
        finally:
            _buffer = None
