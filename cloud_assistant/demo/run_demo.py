"""Run every acceptance scenario, print what happened, and write one transcript each.

This doubles as CI: the process exits non-zero if any scenario's actual graph path
diverges from the path it was expected to take, so a regression in the routing
logic fails a build rather than merely looking different in the output.

Usage:
    python -m cloud_assistant.demo.run_demo
    python -m cloud_assistant.demo.run_demo --only cost-clean
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from pydantic import BaseModel

from cloud_assistant import config
from cloud_assistant.demo.scenarios import SCENARIOS, Scenario
from cloud_assistant.graph import build_graph
from cloud_assistant.logging_setup import configure_logging, log_decision

RULE = "=" * 78
RESULT_KEYS = (
    "cost_analysis_result",
    "cost_recommendation_result",
    "security_audit_result",
    "security_remediation_result",
)


def _serialize(value: Any) -> Any:
    """Render Pydantic results as JSON-safe data, leaving everything else alone."""
    return value.model_dump(mode="json") if isinstance(value, BaseModel) else value


def _run_scenario(graph: Any, scenario: Scenario) -> dict[str, Any]:
    """Execute one scenario, returning its actual path and accumulated final state."""
    path: list[str] = []
    state: dict[str, Any] = {}

    initial = {
        "user_request": scenario["request"],
        "account_id": scenario["account_id"],
        "messages": [],
    }

    # stream_mode="updates" yields {node_name: partial_update} as each node finishes,
    # so accumulating the keys gives the real path for free — no instrumentation and
    # no second run just to find out where the graph went.
    for chunk in graph.stream(initial, stream_mode="updates"):
        for node_name, update in chunk.items():
            path.append(node_name)
            if isinstance(update, dict):
                state.update({k: v for k, v in update.items() if k != "messages"})

    return {"path": path, "state": state}


def _print_block(scenario: Scenario, path: list[str], state: dict[str, Any], passed: bool) -> None:
    """Print one human-readable scenario report."""
    print(f"\n{RULE}")
    print(f"SCENARIO {scenario['id']} — {scenario['slug']}")
    print(RULE)
    print(f"  request        : {scenario['request']}")
    print(f"  account        : {scenario['account_id']}")
    print(f"  covers         : {scenario['covers']}")
    print(f"  classified as  : {state.get('workflow', '(none)')} "
          f"(confidence {state.get('supervisor_confidence', 0):.2f})")
    print(f"  rationale      : {state.get('supervisor_rationale', '(none)')}")
    print(f"  expected path  : {' -> '.join([*scenario['expected_path'], 'END'])}")
    print(f"  actual path    : {' -> '.join([*path, 'END'])}")
    print(f"  path match     : {'PASS' if passed else 'FAIL'}")

    if state.get("error"):
        print(f"  degraded at    : {state.get('error_node')}")
        print(f"  error          : {str(state['error'])[:160]}")

    counts = []
    if "idle_resource_count" in state:
        counts.append(f"idle_resource_count={state['idle_resource_count']}")
    if "security_finding_count" in state:
        counts.append(f"security_finding_count={state['security_finding_count']}")
    if counts:
        print(f"  routing counts : {', '.join(counts)}")

    print("\n  final response:")
    for line in str(state.get("final_response", "(none)")).splitlines():
        print(f"    {line}")


def _write_transcript(scenario: Scenario, path: list[str], state: dict[str, Any], passed: bool) -> str:
    """Write one scenario transcript to the transcripts directory and return its path."""
    config.TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    target = config.TRANSCRIPT_DIR / f"scenario_{scenario['id']}_{scenario['slug']}.json"

    payload = {
        "scenario": dict(scenario),
        "expected_path": scenario["expected_path"],
        "actual_path": path,
        "passed": passed,
        "workflow": state.get("workflow"),
        "supervisor_rationale": state.get("supervisor_rationale"),
        "supervisor_confidence": state.get("supervisor_confidence"),
        "idle_resource_count": state.get("idle_resource_count"),
        "security_finding_count": state.get("security_finding_count"),
        "results": {key: _serialize(state[key]) for key in RESULT_KEYS if key in state},
        "final_response": state.get("final_response"),
        "error": state.get("error"),
        "error_node": state.get("error_node"),
    }
    target.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return str(target)


def main() -> int:
    """Run every scenario, write transcripts, and return a process exit code."""
    parser = argparse.ArgumentParser(description="Run the cloud assistant acceptance scenarios.")
    parser.add_argument("--only", help="Run a single scenario by slug, e.g. --only cost-clean")
    args = parser.parse_args()

    selected = [s for s in SCENARIOS if args.only in (None, s["slug"])]
    if not selected:
        print(f"No scenario with slug {args.only!r}. Known slugs: {[s['slug'] for s in SCENARIOS]}")
        return 2

    config.require_api_key()
    configure_logging()
    graph = build_graph()

    summary: list[tuple[Scenario, list[str], bool, str | None]] = []

    for scenario in selected:
        log_decision("demo", "scenario_start", {"id": scenario["id"], "slug": scenario["slug"],
                                                "account_id": scenario["account_id"]})
        try:
            outcome = _run_scenario(graph, scenario)
            path, state = outcome["path"], outcome["state"]
        except Exception as exc:  # noqa: BLE001 — one bad scenario must not end the run
            path, state = [], {"error": f"{type(exc).__name__}: {exc}", "error_node": "graph"}

        passed = path == scenario["expected_path"]
        _print_block(scenario, path, state, passed)
        transcript = _write_transcript(scenario, path, state, passed)
        print(f"\n  transcript     : {transcript}")

        log_decision("demo", "scenario_complete", {"id": scenario["id"], "slug": scenario["slug"],
                                                   "passed": passed, "path": path})
        summary.append((scenario, path, passed, state.get("error_node")))

    print(f"\n{RULE}")
    print("SUMMARY")
    print(RULE)
    print(f"  {'#':<3} {'slug':<17} {'result':<7} {'degraded':<10} path")
    for scenario, path, passed, error_node in summary:
        print(f"  {scenario['id']:<3} {scenario['slug']:<17} {'PASS' if passed else 'FAIL':<7} "
              f"{error_node or '-':<10} {' -> '.join(path) or '(none)'}")
        if not passed:
            print(f"      expected: {' -> '.join(scenario['expected_path'])}")

    failed = [s["slug"] for s, _, passed, _ in summary if not passed]
    print(f"\n  {len(summary) - len(failed)}/{len(summary)} scenarios took their expected path")
    print(f"  log: {config.LOG_PATH}")

    if failed:
        print(f"  FAILED: {', '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
