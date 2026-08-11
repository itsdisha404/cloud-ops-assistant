"""FastAPI backend: run the compiled graph from HTTP requests, serve the
small static frontend, and persist a per-run history so the frontend's log
page can show which agents and tools each past run touched.

Run with:
    venv\\Scripts\\python.exe -m uvicorn cloud_assistant.api.server:app --reload
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from cloud_assistant import config
from cloud_assistant.api.capture import capture_decisions
from cloud_assistant.api.history import list_runs, load_run, new_run_id, save_run
from cloud_assistant.demo.scenarios import SCENARIOS
from cloud_assistant.graph import build_graph
from cloud_assistant.logging_setup import configure_logging

WEBAPP_DIR: Path = Path(__file__).resolve().parent.parent / "webapp"

RESULT_KEYS = (
    "cost_analysis_result",
    "cost_recommendation_result",
    "security_audit_result",
    "security_remediation_result",
)

configure_logging()

app = FastAPI(title="Cloud Ops Assistant API")

_graph = None


def _get_graph():
    """Build the graph on first use, so an unset OPENAI_API_KEY fails on the
    first request rather than at import time (which would take the whole
    process down before it can even report why)."""
    global _graph
    if _graph is None:
        config.require_api_key()
        _graph = build_graph()
    return _graph


class RunRequest(BaseModel):
    """A request to route through the graph, same shape as a demo scenario."""

    request: str = Field(min_length=1, description="The natural-language request to route.")
    account_id: str | None = Field(default=None, description="Optional 12-digit account id.")


def _serialize(value: Any) -> Any:
    """Render Pydantic results as JSON-safe data, leaving everything else alone."""
    return value.model_dump(mode="json") if isinstance(value, BaseModel) else value


def _run_graph(user_request: str, account_id: str | None) -> dict[str, Any]:
    """Execute one request through the graph and return a full run record."""
    graph = _get_graph()
    started_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    run_id = new_run_id()

    initial = {"user_request": user_request, "account_id": account_id or "", "messages": []}

    path: list[str] = []
    state: dict[str, Any] = {}
    with capture_decisions() as decisions:
        for chunk in graph.stream(initial, stream_mode="updates"):
            for node_name, update in chunk.items():
                path.append(node_name)
                if isinstance(update, dict):
                    state.update({k: v for k, v in update.items() if k != "messages"})

    events = [d["event"] for d in decisions]
    agents_called = ["supervisor"] if "classified" in events else []
    agents_called += [d["component"] for d in decisions if d["event"] == "invoking_subagent"]
    tools_called = [d["detail"].get("tool") for d in decisions if d["event"] == "tool_call"]

    record: dict[str, Any] = {
        "run_id": run_id,
        "started_at": started_at,
        "request": user_request,
        # state["account_id"] is what the supervisor actually resolved and used
        # (it may differ from the form field, e.g. when the request text itself
        # names an account); fall back to the submitted value only if the graph
        # never got far enough to resolve one.
        "account_id": state.get("account_id") or account_id,
        "workflow": state.get("workflow"),
        "supervisor_rationale": state.get("supervisor_rationale"),
        "supervisor_confidence": state.get("supervisor_confidence"),
        "path": path,
        "idle_resource_count": state.get("idle_resource_count"),
        "security_finding_count": state.get("security_finding_count"),
        "results": {key: _serialize(state[key]) for key in RESULT_KEYS if key in state},
        "final_response": state.get("final_response"),
        "error": state.get("error"),
        "error_node": state.get("error_node"),
        "agents_called": agents_called,
        "tools_called": tools_called,
        "decisions": decisions,
    }
    save_run(record)
    return record


@app.post("/api/run")
def run_scenario(payload: RunRequest) -> dict[str, Any]:
    """Run one ad-hoc request through the graph and persist it to history."""
    try:
        return _run_graph(payload.request, payload.account_id)
    except Exception as exc:  # noqa: BLE001 — surface as a clean 500, not a stack trace to the browser
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


@app.get("/api/runs")
def get_runs(limit: int = 50) -> list[dict[str, Any]]:
    """List past runs, most recent first."""
    return list_runs(limit=limit)


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    """Full detail for one past run, including its captured decision log."""
    record = load_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="run not found")
    return record


@app.get("/api/scenarios")
def get_scenarios() -> list[dict[str, Any]]:
    """The repo's own acceptance scenarios, for the frontend's quick-fill list."""
    return [
        {"slug": s["slug"], "request": s["request"], "account_id": s["account_id"], "covers": s["covers"]}
        for s in SCENARIOS
    ]


app.mount("/static", StaticFiles(directory=str(WEBAPP_DIR)), name="static")


@app.get("/")
def index_page() -> FileResponse:
    return FileResponse(str(WEBAPP_DIR / "index.html"))


@app.get("/logs")
def logs_page() -> FileResponse:
    return FileResponse(str(WEBAPP_DIR / "logs.html"))
