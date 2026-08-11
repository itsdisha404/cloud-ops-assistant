"""Cost analysis agent and wrapper node — the first hop of workflow 1."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from langchain.agents import create_agent
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable

from cloud_assistant import config
from cloud_assistant.agents._common import _degrade, build_model, invoke_subagent
from cloud_assistant.state import CloudOpsState, CostAnalysisResult
from cloud_assistant.tools.cost_tools import get_cost_by_service, get_idle_resources

COST_ANALYSIS_PROMPT = """You are a cloud cost analyst. You examine one AWS account and
report where its money goes and what of that is being wasted.

Before you answer, call each of these tools EXACTLY ONCE:
- get_cost_by_service, to see spend broken down by service
- get_idle_resources, to see which resources are idle or unattached

Never call the same tool twice. These tools are deterministic — a second call
returns exactly what the first one returned, so repeating a call cannot improve your
answer and only wastes time. Once you hold both results, write your report.

Rules for the report:
- Cost Explorer returns Metrics.UnblendedCost.Amount as a STRING. Convert every amount
  to a number before adding it, and set total_monthly_spend_usd to the sum across all
  groups.
- The groups come back unsorted. Rank them yourself and list top_services from highest
  spend to lowest, using the service names exactly as the tool reports them.
- Create one entry in idle_resources for each item the idle-resource tool returned,
  copying its identifiers and cost verbatim. Never add a resource the tool did not
  report.
- idle_resource_count MUST equal the number of entries in idle_resources.
- An empty idle-resource list is a COMPLETE and CORRECT answer, not a problem to
  retry. If get_idle_resources returns {"IdleResources": []}, the account genuinely
  has no waste: report the spend breakdown, say the account has no idle resources,
  set idle_resources to an empty list and idle_resource_count to 0, and finish. Do
  not call the tool again to check.
- If either tool returns a payload containing an "Error" key, or a payload whose
  expected list is null, that call FAILED. Say so explicitly in your summary, report
  only what the successful call gave you, and never fill the gap with invented numbers.
"""

COST_ANALYSIS_TASK = """Analyze AWS account {account_id}.

The user asked: {user_request}

Call both tools for account {account_id}, then report the account's spend breakdown and
every idle resource you found."""


@lru_cache(maxsize=1)
def _agent() -> Runnable:
    """Build the cost analysis agent once, on first use."""
    return create_agent(
        model=build_model(),
        tools=[get_cost_by_service, get_idle_resources],
        system_prompt=COST_ANALYSIS_PROMPT,
        response_format=CostAnalysisResult,
    )


def cost_analysis_node(state: CloudOpsState) -> dict[str, Any]:
    """Run the cost analysis agent and flatten its result into graph state."""
    try:
        account_id = state.get("account_id") or config.DEFAULT_ACCOUNT_ID
        prompt = COST_ANALYSIS_TASK.format(
            account_id=account_id,
            user_request=state.get("user_request", "Review this account for wasted spend."),
        )
        result = invoke_subagent(_agent(), "cost_analysis", prompt, CostAnalysisResult)
    except Exception as exc:  # noqa: BLE001 — a node must never raise into the graph
        return _degrade("cost_analysis", exc, state)

    return {
        "cost_analysis_result": result,
        # Recomputed from len(), never taken from result.idle_resource_count: the
        # next edge is chosen from this number, and routing must not depend on the
        # model getting its own arithmetic right.
        "idle_resource_count": len(result.idle_resources),
        # Set here because this node is terminal whenever there is no waste to
        # recommend on — without it the skip branch would end with an empty answer.
        "final_response": result.summary,
        "messages": [AIMessage(content=result.summary)],
    }
