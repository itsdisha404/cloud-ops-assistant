"""Remediation plan generation, bound to the security remediation agent.

The action text and the severity-to-priority mapping are fixed lookups, not model
output. Remediation advice is the part a reader is most likely to act on, so it
comes from a table that can be reviewed once rather than being regenerated — and
possibly re-invented — on every run.
"""

from __future__ import annotations

from typing import Any, Final

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from cloud_assistant.tools import guarded_call

_ACTION_BY_TYPE: Final[dict[str, str]] = {
    "public_s3_bucket": (
        "Enable PublicAccessBlock on the bucket (all four settings) and remove the "
        "public statement from its bucket policy"
    ),
    "overpermissioned_iam_role": (
        "Replace the wildcard policy with a least-privilege inline policy scoped to "
        "the role's observed actions"
    ),
}
_DEFAULT_ACTION: Final[str] = (
    "Review this finding manually and apply the least-privilege configuration for the affected resource"
)

_PRIORITY_BY_SEVERITY: Final[dict[str, str]] = {
    "critical": "P0",
    "high": "P1",
    "medium": "P2",
    "low": "P3",
}
_DEFAULT_PRIORITY: Final[str] = "P2"
_PRIORITY_ORDER: Final[dict[str, int]] = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


class SecurityFindingInput(BaseModel):
    """One confirmed security finding, in the shape of a SecurityFinding.

    Every field is required with no default: OpenAI's strict function schema
    requires ``required`` to name every property, so an optional field would make
    the tool definition itself invalid.
    """

    finding_id: str = Field(description="Stable finding identifier, e.g. 'S3-PUBLIC-1' or 'IAM-PERM-2'.")
    finding_type: str = Field(
        description="One of 'public_s3_bucket' or 'overpermissioned_iam_role'. This decides the remediation action."
    )
    severity: str = Field(
        description="One of 'low', 'medium', 'high', 'critical'. This decides the priority."
    )
    resource_arn: str = Field(description="ARN of the affected resource.")


class GenerateRemediationPlanArgs(BaseModel):
    """Arguments for generate_remediation_plan."""

    findings: list[SecurityFindingInput] = Field(
        description="Every confirmed finding to plan for. Pass them all in a single call rather than one call per finding."
    )
    account_id: str | None = Field(
        description="The 12-digit AWS account id the findings belong to. Pass null if you do not know it."
    )


def _as_dict(item: Any) -> dict[str, Any]:
    """Normalize a finding that may arrive as a validated model or a raw dict."""
    return item.model_dump() if isinstance(item, BaseModel) else dict(item)


def _plan(findings: list[Any]) -> dict[str, Any]:
    """Map each finding to its fixed action and severity-derived priority, ordered P0 first."""
    steps = []
    for raw in findings:
        finding = _as_dict(raw)
        severity = str(finding.get("severity", "")).lower()
        steps.append(
            {
                "FindingId": finding.get("finding_id", ""),
                "ResourceArn": finding.get("resource_arn", ""),
                "Severity": severity,
                "Action": _ACTION_BY_TYPE.get(str(finding.get("finding_type", "")), _DEFAULT_ACTION),
                "Priority": _PRIORITY_BY_SEVERITY.get(severity, _DEFAULT_PRIORITY),
            }
        )

    steps.sort(key=lambda step: _PRIORITY_ORDER[step["Priority"]])
    return {"RemediationSteps": steps}


@tool("generate_remediation_plan", args_schema=GenerateRemediationPlanArgs)
def generate_remediation_plan(findings: list[Any], account_id: str | None = None) -> dict[str, Any]:
    """Turn confirmed security findings into a prioritized remediation plan.

    Call this exactly once, passing every finding together. It returns
    {"RemediationSteps": [...]} where each step carries FindingId, the concrete
    Action to take, the source Severity, and a Priority of P0 through P3 derived
    from that severity. Steps come back already ordered P0 first. Use the returned
    Action text as given rather than rewriting it, keep the returned order, and
    make each step's rationale refer to the finding's severity.
    """
    return guarded_call(
        "generate_remediation_plan",
        account_id,
        lambda: _plan(findings),
        {"finding_count": len(findings)},
    )
