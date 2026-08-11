"""HTTP frontend for the compiled graph.

Two endpoints do the real work. ``GET /api/config`` tells the page which accounts
and sample requests exist, so the UI never hardcodes anything the Python side
already defines. ``POST /api/query`` runs one request through the graph and
streams the result back as server-sent events.

Streaming is the point rather than a flourish: the same
``graph.stream(..., stream_mode="updates")`` call the demo uses to record the path
taken also yields one chunk per finished node, so forwarding those chunks lets the
browser light up the traversal live and makes the conditional edges visible —
a skipped node is one that never arrives.

Usage:
    python -m cloud_assistant.web.server
    uvicorn cloud_assistant.web.server:app --reload
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from cloud_assistant import config
from cloud_assistant.demo.scenarios import SCENARIOS
from cloud_assistant.logging_setup import configure_logging, log_decision

STATIC_DIR: Path = Path(__file__).parent / "static"

NODES: tuple[str, ...] = (
    "supervisor",
    "cost_analysis",
    "cost_recommendation",
    "security_audit",
    "security_remediation",
)

RESULT_KEYS: tuple[str, ...] = (
    "cost_analysis_result",
    "cost_recommendation_result",
    "security_audit_result",
    "security_remediation_result",
)

ACCOUNTS: list[dict[str, str]] = [
    {
        "id": config.DEFAULT_ACCOUNT_ID,
        "label": "Default — waste and findings present",
        "note": "Both workflows run end to end on this account.",
    },
    {
        "id": "333333333333",
        "label": "Security demo — public buckets and loose IAM",
        "note": "Different seed, so different plausible data.",
    },
    {
        "id": config.CLEAN_ACCOUNT_ID,
        "label": "Clean — nothing to fix",
        "note": "Exercises both skip branches: the second node never runs.",
    },
    {
        "id": config.FAULT_ACCOUNT_ID,
        "label": "Fault injection — every tool fails",
        "note": "Tools return malformed payloads; the run degrades without crashing.",
    },
]

_graph: Any = None

app = FastAPI(title="Cloud Operations Assistant", docs_url="/api/docs", openapi_url="/api/openapi.json")


class QueryRequest(BaseModel):
    """One natural-language request to route through the graph."""

    request: str = Field(min_length=1, max_length=2000, description="The operator's question.")
    account_id: str = Field(
        default=config.DEFAULT_ACCOUNT_ID,
        pattern=r"^\d{12}$",
        description="12-digit mocked AWS account id to run against.",
    )


def get_graph() -> Any:
    """Build the graph once, on first use, and reuse it for every later request."""
    # Imported here rather than at module scope on purpose: importing
    # cloud_assistant.graph constructs every agent, which needs a live API key.
    # Deferring it means the page itself still serves without one and the missing
    # key surfaces as a readable error in the UI instead of a failed import.
    global _graph
    if _graph is None:
        config.require_api_key()
        configure_logging()
        from cloud_assistant.graph import build_graph

        _graph = build_graph()
    return _graph


def _serialize(value: Any) -> Any:
    """Render Pydantic results as JSON-safe data, leaving everything else alone."""
    return value.model_dump(mode="json") if isinstance(value, BaseModel) else value


def _clean_update(update: Any) -> dict[str, Any]:
    """Turn one node's partial state update into JSON the browser can render."""
    if not isinstance(update, dict):
        return {}
    # messages carries the whole LangChain transcript, which is large, not
    # JSON-serializable as-is, and already summarized by the structured results.
    return {key: _serialize(value) for key, value in update.items() if key != "messages"}


def _sse(payload: dict[str, Any]) -> str:
    """Format one payload as a server-sent event frame."""
    return f"data: {json.dumps(payload, default=str)}\n\n"


def _event_stream(user_request: str, account_id: str) -> Iterator[str]:
    """Run one request through the graph, yielding an SSE frame per finished node."""
    path: list[str] = []
    state: dict[str, Any] = {}

    yield _sse({"type": "start", "request": user_request, "account_id": account_id})

    try:
        graph = get_graph()
    except Exception as exc:  # noqa: BLE001 — a missing key must read as a message, not a 500
        yield _sse({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        return

    log_decision("web", "query_start", {"account_id": account_id, "chars": len(user_request)})

    initial = {"user_request": user_request, "account_id": account_id, "messages": []}

    try:
        for chunk in graph.stream(initial, stream_mode="updates"):
            for node_name, update in chunk.items():
                path.append(node_name)
                clean = _clean_update(update)
                state.update(clean)
                yield _sse({"type": "node", "node": node_name, "update": clean, "path": list(path)})
    except Exception as exc:  # noqa: BLE001 — report the failure, never drop the connection
        log_decision("web", "node_error", {"error": str(exc), "type": type(exc).__name__})
        yield _sse({"type": "error", "message": f"{type(exc).__name__}: {exc}", "path": path})
        return

    log_decision("web", "query_complete", {"path": path, "workflow": state.get("workflow")})
    yield _sse({"type": "done", "path": path, "state": state})


@app.get("/api/config")
def read_config() -> dict[str, Any]:
    """Describe the accounts, sample requests, and node names the UI should offer."""
    return {
        "model": config.MODEL_ID,
        "reference_date": config.REFERENCE_DATE.isoformat(),
        "default_account_id": config.DEFAULT_ACCOUNT_ID,
        "nodes": list(NODES),
        "result_keys": list(RESULT_KEYS),
        "accounts": ACCOUNTS,
        "samples": [
            {
                "slug": s["slug"],
                "request": s["request"],
                "account_id": s["account_id"],
                "expected_path": s["expected_path"],
                "covers": s["covers"],
            }
            for s in SCENARIOS
        ],
    }


@app.post("/api/query")
def run_query(payload: QueryRequest) -> StreamingResponse:
    """Stream one request through the graph as server-sent events."""
    return StreamingResponse(
        _event_stream(payload.request.strip(), payload.account_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Disables proxy buffering, which would otherwise hold every frame
            # until the run finished and defeat the live path display.
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/")
def index() -> FileResponse:
    """Serve the single-page query console."""
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def main() -> None:
    """Run the development server."""
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
