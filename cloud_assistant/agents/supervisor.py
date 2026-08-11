"""Routing supervisor: classify a request into a workflow, as typed data.

This is the one place an LLM is allowed to influence which edge the graph takes,
and it does so by returning a ``SupervisorDecision`` — never by emitting prose
that something downstream has to parse. It calls no tools, so it is built with
``with_structured_output`` rather than ``create_agent``: a tool-calling loop
around a pure classification would be latency and failure surface for nothing.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import Runnable

from cloud_assistant import config
from cloud_assistant.agents._common import _degrade, build_model
from cloud_assistant.logging_setup import log_decision
from cloud_assistant.state import CloudOpsState, SupervisorDecision

SUPERVISOR_PROMPT = """You are the routing supervisor for a cloud operations assistant.
Your only job is to classify the user's request into exactly one workflow. You do not
answer the request yourself and you do not call tools.

Choose the workflow:
- "cost" — anything about spend, bills, budget, waste, idle or unused resources,
  rightsizing, savings, or "why is this so expensive".
- "security" — anything about public S3 buckets, IAM permissions or roles, exposure,
  access control, or auditing the account's posture. This also covers every mention of
  compliance, audits, and posture reviews: there is no cost-compliance workflow here, so
  "run a compliance check", "audit this account" and "is this account compliant" are
  always "security", even when they name no specific control.
- "unclear" — anything ambiguous, anything that spans both workflows, and anything
  outside cloud cost or cloud security. When you choose "unclear" you MUST put a
  specific, answerable follow-up question in `clarification` — one that would let you
  pick between cost and security next time. Never leave it null for "unclear".

Also extract the account id: if the request contains a 12-digit AWS account number,
put it in `account_id`. If it does not, set `account_id` to null. Never invent one.

Set `confidence` below 0.5 when the request could plausibly belong to either workflow.

Examples:

Request: "Our AWS bill jumped 30% this month, find the waste and tell me what to shut off."
-> workflow "cost", account_id null, confidence 0.97,
   rationale: "asks about a bill increase and what to shut off, which is spend and waste".

Request: "Audit account 333333333333 for public S3 buckets and over-permissioned IAM roles."
-> workflow "security", account_id "333333333333", confidence 0.98,
   rationale: "names public buckets and IAM roles, which is access exposure".

Request: "Run a compliance check on this account."
-> workflow "security", account_id null, confidence 0.85,
   rationale: "a compliance check is a posture audit, which is the security workflow".

Request: "Can you take a look at my cloud setup?"
-> workflow "unclear", account_id null, confidence 0.25,
   rationale: "no indication whether the concern is spend or exposure",
   clarification: "Happy to help — are you looking to reduce spend, or to check the
   account for security exposure such as public buckets and over-permissioned roles?"
"""

_GENERIC_CLARIFICATION = (
    "I can help with two things on this account: finding wasted spend, or auditing "
    "security exposure such as public S3 buckets and over-permissioned IAM roles. "
    "Which would you like?"
)


@lru_cache(maxsize=1)
def _classifier() -> Runnable:
    """Build the structured-output classifier once, on first use."""
    return build_model().with_structured_output(SupervisorDecision)


def classify(user_request: str) -> SupervisorDecision:
    """Classify a request into a workflow, returning a typed decision."""
    decision = _classifier().invoke(
        [SystemMessage(content=SUPERVISOR_PROMPT), HumanMessage(content=user_request)]
    )
    if not isinstance(decision, SupervisorDecision):
        raise ValueError(f"supervisor: classifier returned {type(decision).__name__}, not SupervisorDecision")
    return decision


def _resolve_request(state: CloudOpsState) -> str:
    """Return the request text, falling back to the last human message on the state."""
    request = state.get("user_request")
    if request:
        return request
    for message in reversed(state.get("messages", [])):
        if getattr(message, "type", None) == "human":
            return str(message.content)
    return ""


def supervisor_node(state: CloudOpsState) -> dict[str, Any]:
    """Classify the request and write the routing decision into graph state."""
    user_request = _resolve_request(state)

    try:
        decision = classify(user_request)
    except Exception as exc:  # noqa: BLE001 — the graph must always get a routable value
        # "unclear" is added on top of the degrade update so route_from_supervisor
        # still has a workflow to switch on; without it the run would end on a
        # KeyError rather than on a message the user can read.
        return {**_degrade("supervisor", exc, state), "workflow": "unclear"}

    log_decision(
        "supervisor",
        "classified",
        {
            "workflow": decision.workflow,
            "confidence": decision.confidence,
            "account_id": decision.account_id,
            "rationale": decision.rationale,
        },
    )

    update: dict[str, Any] = {
        "user_request": user_request,
        "workflow": decision.workflow,
        "supervisor_rationale": decision.rationale,
        "supervisor_confidence": decision.confidence,
        "account_id": decision.account_id or state.get("account_id") or config.DEFAULT_ACCOUNT_ID,
    }

    if decision.workflow == "unclear":
        # The unclear branch goes straight to END, so this node is the last chance
        # to put something in front of the user.
        update["final_response"] = decision.clarification or _GENERIC_CLARIFICATION

    return update
