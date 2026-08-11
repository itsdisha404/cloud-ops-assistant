"""Cost recommendation agent and wrapper node — the second hop of workflow 1.

Only reached when cost analysis actually found idle resources, so this module can
assume ``cost_analysis_result`` is populated; if it somehow is not, the guard
below degrades rather than raising.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from langchain.agents import create_agent
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable

from cloud_assistant.agents._common import _degrade, build_model, invoke_subagent
from cloud_assistant.state import CloudOpsState, CostRecommendationResult
from cloud_assistant.tools.savings_tools import estimate_savings

COST_RECOMMENDATION_PROMPT = """You are a cloud cost optimization advisor. The idle
resources have already been identified for you — your job is to price the fix and put
the actions in the right order, not to search for more waste.

Rules:
- Call estimate_savings exactly ONCE, passing every resource you were given in a single
  call. Do not call it per resource, and do not call it twice.
- The tool's numbers are authoritative. Copy EstimatedMonthlySavingsUsd, Action and Risk
  into your answer exactly as returned; do not recompute, re-round or re-estimate them.
- `estimates` is REQUIRED and must never be omitted or left empty when the tool returned
  results. Put exactly one entry in it for every element of the tool's SavingsEstimates
  list, mapping ResourceId -> resource_id, Action -> action,
  EstimatedMonthlySavingsUsd -> estimated_monthly_savings_usd, and Risk -> risk. If the
  tool returned 3 estimates, `estimates` has 3 entries.
- total_estimated_monthly_savings_usd must be the tool's TotalEstimatedMonthlySavingsUsd.
- Order prioritized_actions by estimated monthly savings, largest first. Break ties
  toward the lower-risk action. Each entry should name the resource, the action, and
  what it saves per month.
- Note in your summary when an action carries high risk, so the reader knows what needs
  a maintenance window rather than a click.
- If the tool returns a payload containing an "Error" key, say so in your summary and do
  not invent savings figures.
"""

COST_RECOMMENDATION_TASK = """Account {account_id} has {count} idle resource(s), already
identified by the cost analysis stage:

{resources_json}

The user asked: {user_request}

Call estimate_savings once with all {count} of these resources, then give the prioritized
set of actions."""


@lru_cache(maxsize=1)
def _agent() -> Runnable:
    """Build the cost recommendation agent once, on first use."""
    return create_agent(
        model=build_model(),
        tools=[estimate_savings],
        system_prompt=COST_RECOMMENDATION_PROMPT,
        response_format=CostRecommendationResult,
    )


def cost_recommendation_node(state: CloudOpsState) -> dict[str, Any]:
    """Price and prioritize the idle resources found by cost analysis."""
    try:
        analysis = state.get("cost_analysis_result")
        if analysis is None:
            raise ValueError("cost_recommendation: no cost_analysis_result on state")

        # Serialized in the tool's own PascalCase shape so the model can pass these
        # straight through to estimate_savings instead of transcribing field names.
        resources = [
            {
                "ResourceId": finding.resource_id,
                "ResourceType": finding.resource_type,
                "MonthlyCostUsd": finding.monthly_cost_usd,
                "Region": finding.region,
                "IdleReason": finding.idle_reason,
            }
            for finding in analysis.idle_resources
        ]

        prompt = COST_RECOMMENDATION_TASK.format(
            account_id=analysis.account_id or state.get("account_id", ""),
            count=len(resources),
            resources_json=json.dumps(resources, indent=2),
            user_request=state.get("user_request", "Tell me what to shut off."),
        )
        result = invoke_subagent(_agent(), "cost_recommendation", prompt, CostRecommendationResult)
    except Exception as exc:  # noqa: BLE001 — a node must never raise into the graph
        return _degrade("cost_recommendation", exc, state)

    # The terminal answer covers the whole workflow, not just this last hop.
    final_response = f"{analysis.summary}\n\n{result.summary}"
    return {
        "cost_recommendation_result": result,
        "final_response": final_response,
        "messages": [AIMessage(content=result.summary)],
    }
