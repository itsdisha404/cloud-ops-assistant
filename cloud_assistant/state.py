
from __future__ import annotations

from typing import Annotated, Literal, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

__all__ = [
    "CostFinding",
    "CostAnalysisResult",
    "SavingsEstimate",
    "CostRecommendationResult",
    "SecurityFinding",
    "SecurityAuditResult",
    "RemediationStep",
    "SecurityRemediationResult",
    "SupervisorDecision",
    "CloudOpsState",
]


# --------------------------------------------------------------------------- #
# Workflow 1 — cost optimization
# --------------------------------------------------------------------------- #


class CostFinding(BaseModel):
    """One idle or wasteful resource identified during cost analysis."""

    resource_id: str = Field(
        description="Provider resource identifier exactly as returned by the tool, e.g. 'i-0abc123' or 'vol-0def456'."
    )
    resource_type: str = Field(
        description="Resource category. One of: 'ec2-instance', 'ebs-volume', 'elastic-ip', 'rds-instance'."
    )
    region: str = Field(description="AWS region the resource lives in, e.g. 'us-east-1'.")
    monthly_cost_usd: float = Field(
        description="Current monthly cost of this resource in USD. Tool payloads may give this as a string; convert it to a number."
    )
    idle_reason: str = Field(
        description="Concrete evidence that the resource is idle, quoting the tool's reason, e.g. 'CPU below 2% for 30 days'. Never speculate."
    )


class CostAnalysisResult(BaseModel):
    """Structured output of the cost analysis agent."""

    account_id: str = Field(description="The 12-digit AWS account id that was analyzed.")
    total_monthly_spend_usd: float = Field(
        description="Total month-to-date spend in USD, summed across every service returned by the cost tool."
    )
    top_services: list[str] = Field(
        description="Service names ordered by spend, highest first. Use the service names exactly as the tool reports them."
    )
    idle_resources: list[CostFinding] = Field(
        description="Every resource the idle-resource tool flagged. Empty list if the account is clean — never invent entries."
    )
    idle_resource_count: int = Field(
        description="Number of entries in idle_resources. This MUST equal the length of that list."
    )
    summary: str = Field(
        description="Two to four sentences for a human reader: where the money goes and what is being wasted. If a tool returned an 'Error' key, say so plainly here and report only what you could actually gather."
    )


class SavingsEstimate(BaseModel):
    """Projected savings for acting on a single idle resource."""

    resource_id: str = Field(description="Resource identifier this estimate applies to, matching a CostFinding.resource_id.")
    action: str = Field(
        description="Recommended action. One of: 'stop', 'delete', 'rightsize', 'release'."
    )
    estimated_monthly_savings_usd: float = Field(
        description="Monthly USD saved by taking the action, as returned by the estimate_savings tool. Do not recompute it yourself."
    )
    risk: Literal["low", "medium", "high"] = Field(
        description="Operational risk of taking the action: 'low' for unattached or clearly idle resources, 'high' when the resource may still serve traffic."
    )


class CostRecommendationResult(BaseModel):
    """Structured output of the cost recommendation agent."""

    estimates: list[SavingsEstimate] = Field(
        description="One estimate per idle resource supplied, taken from the estimate_savings tool output."
    )
    total_estimated_monthly_savings_usd: float = Field(
        description="Sum of estimated_monthly_savings_usd across all estimates, in USD."
    )
    prioritized_actions: list[str] = Field(
        description="Human-readable action items ordered by return on investment: largest savings first, ties broken toward lower risk. Each item names the resource and the action."
    )
    summary: str = Field(
        description="Two to four sentences telling the operator what to do first and what it is worth per month."
    )


# --------------------------------------------------------------------------- #
# Workflow 2 — security audit
# --------------------------------------------------------------------------- #


class SecurityFinding(BaseModel):
    """One confirmed security risk found during the audit."""

    finding_id: str = Field(
        description="Stable identifier of the form 'S3-PUBLIC-<n>' for public buckets or 'IAM-PERM-<n>' for over-permissioned roles, numbered from 1."
    )
    resource_arn: str = Field(description="ARN of the affected resource, e.g. 'arn:aws:s3:::my-bucket'.")
    finding_type: str = Field(
        description="Risk category. One of: 'public_s3_bucket', 'overpermissioned_iam_role'."
    )
    severity: Literal["low", "medium", "high", "critical"] = Field(
        description="Severity of this finding, taken from the tool's Severity field where present."
    )
    description: str = Field(
        description="One or two sentences naming the specific misconfiguration and its exposure, e.g. which access block is disabled or which wildcard policy is attached."
    )


class SecurityAuditResult(BaseModel):
    """Structured output of the security audit agent."""

    account_id: str = Field(description="The 12-digit AWS account id that was audited.")
    findings: list[SecurityFinding] = Field(
        description="One entry per ACTUALLY risky item only. A bucket with IsPublic false, or a role with OverPermissioned false, is not a finding. Empty list when the account is clean."
    )
    security_finding_count: int = Field(
        description="Number of entries in findings. This MUST equal the length of that list."
    )
    highest_severity: Literal["none", "low", "medium", "high", "critical"] = Field(
        description="The most severe level present in findings. Use 'none' when findings is empty."
    )
    summary: str = Field(
        description="Two to four sentences on the account's security posture. If a tool returned an 'Error' key, say so plainly rather than implying the account is clean."
    )


class RemediationStep(BaseModel):
    """One prioritized fix for a security finding."""

    finding_id: str = Field(description="The finding_id this step remediates, matching a SecurityFinding.")
    action: str = Field(description="The concrete remediation action returned by the generate_remediation_plan tool.")
    priority: Literal["P0", "P1", "P2", "P3"] = Field(
        description="Execution priority derived from severity: critical->P0, high->P1, medium->P2, low->P3."
    )
    rationale: str = Field(
        description="Why this step sits at this priority. Must explicitly reference the finding's severity."
    )


class SecurityRemediationResult(BaseModel):
    """Structured output of the security remediation agent."""

    steps: list[RemediationStep] = Field(
        description="Remediation steps ordered by priority, P0 first through P3 last."
    )
    summary: str = Field(
        description="Two to four sentences on what to fix first and why, written for an on-call engineer."
    )


# --------------------------------------------------------------------------- #
# Routing
# --------------------------------------------------------------------------- #


class SupervisorDecision(BaseModel):
    """The supervisor's routing decision, expressed as typed data rather than prose."""

    workflow: Literal["cost", "security", "unclear"] = Field(
        description="Which workflow handles this request: 'cost' for spend, waste, idle resources, billing or savings; 'security' for public buckets, IAM permissions, compliance or exposure; 'unclear' for anything ambiguous, mixed, or out of scope."
    )
    account_id: str | None = Field(
        description="The 12-digit AWS account id mentioned in the request, or null if the request does not contain one. Never invent an account id."
    )
    rationale: str = Field(description="One sentence explaining the classification, citing the wording that decided it.")
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence in the classification, from 0.0 to 1.0. Use a value below 0.5 when the request could plausibly belong to either workflow.",
    )
    clarification: str | None = Field(
        description="Populated ONLY when workflow is 'unclear': a specific follow-up question that would let you route the request. Null otherwise."
    )


# --------------------------------------------------------------------------- #
# Graph state
# --------------------------------------------------------------------------- #


class CloudOpsState(TypedDict, total=False):
    """State threaded through every node of the graph.

    ``total=False`` because nodes return partial updates: each one writes only the
    keys it owns and LangGraph merges them. The two ``*_count`` fields are
    deliberately flattened to the top level so router functions read a plain int
    instead of reaching through an optional nested result object.
    """

    messages: Annotated[list[AnyMessage], add_messages]
    user_request: str
    account_id: str

    # Set by the supervisor.
    workflow: Literal["cost", "security", "unclear"]
    supervisor_rationale: str
    supervisor_confidence: float

    # Workflow 1 — cost.
    cost_analysis_result: CostAnalysisResult
    idle_resource_count: int
    cost_recommendation_result: CostRecommendationResult

    # Workflow 2 — security.
    security_audit_result: SecurityAuditResult
    security_finding_count: int
    security_remediation_result: SecurityRemediationResult

    # Terminal output and failure bookkeeping.
    final_response: str
    error: str | None
    error_node: str | None
