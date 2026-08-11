from __future__ import annotations

from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from cloud_assistant.mock_data import fixtures
from cloud_assistant.tools import guarded_call


class GetCostByServiceArgs(BaseModel):
    """Arguments for get_cost_by_service."""

    account_id: str = Field(
        description="The 12-digit AWS account id to analyze, e.g. '222222222222'. Use the account id given in the request."
    )


class GetIdleResourcesArgs(BaseModel):
    """Arguments for get_idle_resources."""

    account_id: str = Field(
        description="The 12-digit AWS account id to scan, e.g. '222222222222'. Use the account id given in the request."
    )


@tool("get_cost_by_service", args_schema=GetCostByServiceArgs)
def get_cost_by_service(account_id: str) -> dict[str, Any]:
    """Return month-to-date spend grouped by AWS service for one account.

    Call this to find out where the money is going. The response is AWS Cost
    Explorer shaped: ResultsByTime[0].Groups[] where each group has Keys[0] as
    the service name and Metrics.UnblendedCost.Amount as the spend. Amounts are
    returned as strings and must be converted to numbers before you add them.
    Groups are not sorted, so rank them yourself. If the response contains an
    "Error" key, the call failed: report that rather than inventing figures.
    """
    return guarded_call(
        "get_cost_by_service",
        account_id,
        lambda: fixtures.cost_by_service(account_id),
    )


@tool("get_idle_resources", args_schema=GetIdleResourcesArgs)
def get_idle_resources(account_id: str) -> dict[str, Any]:
    """Return idle, unattached, or under-used resources for one account.

    Call this to find waste. The response is {"IdleResources": [...]} where each
    entry has ResourceId, ResourceType, State, Region, MonthlyCostUsd and
    IdleReason. An empty list is a valid, meaningful answer: it means the account
    has no waste, and you must report that rather than inventing resources. If
    the response contains an "Error" key, the call failed: say so.
    """
    return guarded_call(
        "get_idle_resources",
        account_id,
        lambda: fixtures.idle_resources(account_id),
    )
