"""Savings estimation tool bound to the cost recommendation agent.

The arithmetic here is fixed and deterministic — no RNG, no model involvement.
The recommendation agent's job is prioritization, not invention, so the numbers
it reports must come from this table rather than from its own head.
"""

from __future__ import annotations

from typing import Any, Final

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from cloud_assistant.tools import guarded_call

# resource type -> (remediation action, operational risk of taking it)
_PLAN_BY_TYPE: Final[dict[str, tuple[str, str]]] = {
    "ec2-instance": ("stop", "medium"),  # running, so it may still serve traffic
    "ebs-volume": ("delete", "low"),  # unattached for weeks; snapshot first
    "elastic-ip": ("release", "low"),  # costs money precisely because it is unused
    "rds-instance": ("rightsize", "high"),  # a database restart is never free
}
_DEFAULT_PLAN: Final[tuple[str, str]] = ("rightsize", "high")

# Fraction of current monthly cost recovered by each action.
_SAVINGS_MULTIPLIER: Final[dict[str, float]] = {
    "stop": 1.0,
    "delete": 1.0,
    "release": 1.0,
    "rightsize": 0.4,  # a smaller instance class still costs something
}


class IdleResourceInput(BaseModel):
    """One idle resource, in the shape returned by get_idle_resources.

    Every field is required with no default. OpenAI's strict function schema
    demands that ``required`` list every property, so an optional field here
    makes the whole tool definition invalid at call time rather than merely
    lenient. Fields that may genuinely be unknown are nullable instead.
    """

    # Field names mirror the tool payload's PascalCase keys on purpose: the model
    # copies these straight across from get_idle_resources output, and renaming
    # them here would invite transcription errors for no benefit.
    ResourceId: str = Field(description="Resource identifier, copied exactly from get_idle_resources.")
    ResourceType: str = Field(
        description="One of 'ec2-instance', 'ebs-volume', 'elastic-ip', 'rds-instance'. This decides the recommended action."
    )
    MonthlyCostUsd: float = Field(description="Current monthly cost of the resource in USD.")
    Region: str = Field(description="AWS region of the resource. Pass an empty string if you do not know it.")
    IdleReason: str = Field(
        description="Why the resource was flagged as idle. Pass an empty string if you do not know it."
    )


class EstimateSavingsArgs(BaseModel):
    """Arguments for estimate_savings."""

    resources: list[IdleResourceInput] = Field(
        description="Every idle resource to price. Pass them all in a single call rather than one call per resource."
    )
    account_id: str | None = Field(
        description="The 12-digit AWS account id the resources belong to. Pass null if you do not know it."
    )


def _as_dict(item: Any) -> dict[str, Any]:
    """Normalize a resource that may arrive as a validated model or a raw dict."""
    return item.model_dump() if isinstance(item, BaseModel) else dict(item)


def _estimate(resources: list[Any]) -> dict[str, Any]:
    """Apply the fixed action/multiplier table to each resource and total the result."""
    estimates = []
    for raw in resources:
        resource = _as_dict(raw)
        action, risk = _PLAN_BY_TYPE.get(str(resource.get("ResourceType", "")), _DEFAULT_PLAN)
        monthly_cost = float(resource.get("MonthlyCostUsd") or 0.0)
        estimates.append(
            {
                "ResourceId": resource.get("ResourceId", ""),
                "ResourceType": resource.get("ResourceType", ""),
                "Action": action,
                "CurrentMonthlyCostUsd": round(monthly_cost, 2),
                "EstimatedMonthlySavingsUsd": round(monthly_cost * _SAVINGS_MULTIPLIER[action], 2),
                "Risk": risk,
            }
        )

    total = round(sum(item["EstimatedMonthlySavingsUsd"] for item in estimates), 2)
    return {"SavingsEstimates": estimates, "TotalEstimatedMonthlySavingsUsd": total}


@tool("estimate_savings", args_schema=EstimateSavingsArgs)
def estimate_savings(resources: list[Any], account_id: str | None = None) -> dict[str, Any]:
    """Price the savings available from a set of already-identified idle resources.

    Call this exactly once, passing every idle resource together. It returns
    {"SavingsEstimates": [...], "TotalEstimatedMonthlySavingsUsd": n} where each
    estimate carries the recommended Action, the EstimatedMonthlySavingsUsd and a
    Risk label of low, medium or high. These figures are authoritative — report
    them as returned and do not recalculate or round them yourself. Your job is
    to order the actions by value, breaking ties toward lower risk.
    """
    return guarded_call(
        "estimate_savings",
        account_id,
        lambda: _estimate(resources),
        {"resource_count": len(resources)},
    )
