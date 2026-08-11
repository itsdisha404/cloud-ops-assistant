"""Shared model builder, sub-agent invoker, and graceful-degradation helper.

All four agent nodes have the same skeleton — build a prompt, call a sub-agent,
flatten the typed result into graph state — and the same failure requirement:
never propagate an exception into the graph, because a node that raises takes the
whole run down. The three helpers here are what keep that skeleton identical
across nodes instead of four near-copies that drift apart.
"""

from __future__ import annotations

from typing import Any, Final

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseLanguageModel
from langchain_core.messages import HumanMessage
from langchain_core.runnables import Runnable
from pydantic import BaseModel

from cloud_assistant import config
from cloud_assistant.logging_setup import log_decision
from cloud_assistant.state import CloudOpsState

RECURSION_LIMIT: Final[int] = 12
"""Hard cap on a sub-agent's tool-calling loop.

Each agent needs at most two tool calls and one structured response, so 12 leaves
generous headroom. The cap exists because a model that keeps re-calling a tool
instead of answering would otherwise hang the run indefinitely; with it, LangGraph
raises GraphRecursionError, the node catches it, and ``_degrade`` turns a hang into
a reported failure.
"""

_STAGE_LABELS: Final[dict[str, str]] = {
    "supervisor": "request classification",
    "cost_analysis": "cost analysis",
    "cost_recommendation": "savings recommendation",
    "security_audit": "security audit",
    "security_remediation": "security remediation",
}


def build_model() -> BaseLanguageModel:
    """Return the shared chat model; model and temperature policy live here only."""
    # temperature=0 because every downstream consumer is a typed schema, not prose:
    # sampling variety buys nothing and costs reproducibility.
    return init_chat_model(config.MODEL_ID, temperature=0)


def _degrade(node_name: str, exc: Exception, state: CloudOpsState) -> dict[str, Any]:
    """Turn an exception into a safe partial state update that routes deterministically to END."""
    log_decision(
        node_name,
        "node_error",
        {
            "error": str(exc),
            "type": type(exc).__name__,
            "account_id": state.get("account_id"),
        },
    )
    stage = _STAGE_LABELS.get(node_name, node_name.replace("_", " "))
    return {
        "error": str(exc),
        "error_node": node_name,
        "final_response": (
            f"I wasn't able to complete the {stage} stage for this request "
            f"({type(exc).__name__}: {exc}). I've stopped here rather than reporting "
            f"figures I couldn't verify."
        ),
        # Zeroing BOTH counts is the critical part. Every conditional router reads
        # these ints, so setting them to 0 guarantees a degraded node routes to END
        # instead of into a node whose inputs were never populated.
        "idle_resource_count": 0,
        "security_finding_count": 0,
    }


def invoke_subagent(
    agent: Runnable,
    node_name: str,
    prompt: str,
    result_model: type[BaseModel],
) -> BaseModel:
    """Invoke a sub-agent and return its validated structured response, or raise."""
    log_decision(node_name, "invoking_subagent", {"prompt_chars": len(prompt)})
    result = agent.invoke(
        {"messages": [HumanMessage(content=prompt)]},
        {"recursion_limit": RECURSION_LIMIT},
    )

    structured = result.get("structured_response")
    if not isinstance(structured, result_model):
        raise ValueError(
            f"{node_name}: agent returned no structured_response of type {result_model.__name__} "
            f"(got {type(structured).__name__})"
        )

    messages = result.get("messages", [])
    log_decision(
        node_name,
        "subagent_completed",
        {
            # Deliberately a summary, not the transcript — a log line nobody can
            # read is the same as no log line.
            "result_type": result_model.__name__,
            "messages": len(messages),
            "tool_calls": sum(len(getattr(m, "tool_calls", []) or []) for m in messages),
        },
    )
    return structured
