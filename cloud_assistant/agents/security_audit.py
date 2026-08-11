"""Security audit agent and wrapper node — the first hop of workflow 2."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from langchain.agents import create_agent
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable

from cloud_assistant import config
from cloud_assistant.agents._common import _degrade, build_model, invoke_subagent
from cloud_assistant.state import CloudOpsState, SecurityAuditResult
from cloud_assistant.tools.security_tools import (
    check_overpermissioned_iam_roles,
    check_public_s3_buckets,
)

SECURITY_AUDIT_PROMPT = """You are a cloud security auditor. You examine one AWS account
and report only what is genuinely misconfigured.

Before you answer, call each of these tools EXACTLY ONCE:
- check_public_s3_buckets
- check_overpermissioned_iam_roles

Never call the same tool twice. These tools are deterministic — a second call returns
exactly what the first one returned, so repeating a call cannot improve your answer
and only wastes time. Once you hold both results, write your report.

What counts as a finding — read this carefully, because both tools return safe resources
alongside risky ones:
- A bucket is a finding ONLY when PolicyStatus.IsPublic is true. A bucket with IsPublic
  false, or with all four PublicAccessBlockConfiguration settings enabled, is correctly
  configured. Do not report it. Do not report it as low severity either.
- A role is a finding ONLY when OverPermissioned is true. A role holding only read-only
  or service-role policies is correctly scoped. Do not report it.
- If neither tool returns anything risky, the correct answer is an empty findings list,
  security_finding_count of 0, and highest_severity of "none". That is a COMPLETE and
  CORRECT audit and a good result for the account — not a failure, and not something to
  re-check by calling the tools again. Say plainly that the account is clean and finish.

Recording findings:
- Give each finding a stable id: "S3-PUBLIC-1", "S3-PUBLIC-2", ... for buckets and
  "IAM-PERM-1", "IAM-PERM-2", ... for roles, numbered from 1 in the order you report them.
- Use finding_type "public_s3_bucket" for buckets and "overpermissioned_iam_role" for roles.
- Take severity from the tool's Severity field for that resource.
- In each description, name the specific misconfiguration: which access-block settings are
  disabled, or which over-broad policy is attached.
- Set highest_severity to the most severe level present in findings, or "none" if empty.
- If either tool returns a payload containing an "Error" key, or one whose expected list
  is null instead of a list, that call FAILED. Say so explicitly in your summary and do
  not describe the account as clean on the strength of a failed check.
"""

SECURITY_AUDIT_TASK = """Audit AWS account {account_id}.

The user asked: {user_request}

Call both tools for account {account_id}, then report only the genuinely risky
buckets and roles."""


@lru_cache(maxsize=1)
def _agent() -> Runnable:
    """Build the security audit agent once, on first use."""
    return create_agent(
        model=build_model(),
        tools=[check_public_s3_buckets, check_overpermissioned_iam_roles],
        system_prompt=SECURITY_AUDIT_PROMPT,
        response_format=SecurityAuditResult,
    )


def security_audit_node(state: CloudOpsState) -> dict[str, Any]:
    """Run the security audit agent and flatten its result into graph state."""
    try:
        account_id = state.get("account_id") or config.DEFAULT_ACCOUNT_ID
        prompt = SECURITY_AUDIT_TASK.format(
            account_id=account_id,
            user_request=state.get("user_request", "Run a compliance check on this account."),
        )
        result = invoke_subagent(_agent(), "security_audit", prompt, SecurityAuditResult)
    except Exception as exc:  # noqa: BLE001 — a node must never raise into the graph
        return _degrade("security_audit", exc, state)

    return {
        "security_audit_result": result,
        # Recomputed from len() for the same reason as the cost side: the next edge
        # is chosen from this int, so it cannot depend on the model's own count.
        "security_finding_count": len(result.findings),
        # Terminal whenever the account is clean, so the answer is set here too.
        "final_response": result.summary,
        "messages": [AIMessage(content=result.summary)],
    }
