"""Security remediation agent and wrapper node — the second hop of workflow 2.

Only reached when the audit produced at least one finding, so this module can
assume ``security_audit_result`` is populated; if it somehow is not, the guard
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
from cloud_assistant.state import CloudOpsState, SecurityRemediationResult
from cloud_assistant.tools.remediation_tools import generate_remediation_plan

SECURITY_REMEDIATION_PROMPT = """You are a cloud security remediation planner. The findings
have already been confirmed by the audit stage — your job is to turn them into an ordered
plan an on-call engineer can execute, not to re-audit the account.

Rules:
- Call generate_remediation_plan exactly ONCE, passing every finding in a single call.
- `steps` is REQUIRED and must never be omitted or left empty when the tool returned
  results. Put exactly one entry in it for every element of the tool's RemediationSteps
  list, mapping FindingId -> finding_id, Action -> action and Priority -> priority. If
  the tool returned 4 steps, `steps` has 4 entries.
- Use the Action text the tool returns, as written. Do not paraphrase or shorten it: it
  names the specific control to change.
- Use the Priority the tool returns. Keep the steps in P0, P1, P2, P3 order.
- Every step's rationale MUST reference that finding's severity explicitly — for example
  "critical severity, publicly readable, so this goes first".
- In your summary, state what to fix first and why it outranks the rest.
- If the tool returns a payload containing an "Error" key, say so in your summary rather
  than inventing remediation steps.
"""

SECURITY_REMEDIATION_TASK = """Account {account_id} has {count} confirmed security
finding(s) from the audit stage:

{findings_json}

The user asked: {user_request}

Call generate_remediation_plan once with all {count} findings, then give the ordered
remediation plan."""


@lru_cache(maxsize=1)
def _agent() -> Runnable:
    """Build the security remediation agent once, on first use."""
    return create_agent(
        model=build_model(),
        tools=[generate_remediation_plan],
        system_prompt=SECURITY_REMEDIATION_PROMPT,
        response_format=SecurityRemediationResult,
    )


def security_remediation_node(state: CloudOpsState) -> dict[str, Any]:
    """Turn confirmed findings into an ordered remediation plan."""
    try:
        audit = state.get("security_audit_result")
        if audit is None:
            raise ValueError("security_remediation: no security_audit_result on state")

        # Serialized in the tool's own field names so the model passes them through
        # verbatim instead of transcribing them.
        findings = [
            {
                "finding_id": finding.finding_id,
                "finding_type": finding.finding_type,
                "severity": finding.severity,
                "resource_arn": finding.resource_arn,
            }
            for finding in audit.findings
        ]

        prompt = SECURITY_REMEDIATION_TASK.format(
            account_id=audit.account_id or state.get("account_id", ""),
            count=len(findings),
            findings_json=json.dumps(findings, indent=2),
            user_request=state.get("user_request", "How do I fix these?"),
        )
        result = invoke_subagent(
            _agent(), "security_remediation", prompt, SecurityRemediationResult
        )
    except Exception as exc:  # noqa: BLE001 — a node must never raise into the graph
        return _degrade("security_remediation", exc, state)

    # The terminal answer covers the whole workflow, not just this last hop.
    final_response = f"{audit.summary}\n\n{result.summary}"
    return {
        "security_remediation_result": result,
        "final_response": final_response,
        "messages": [AIMessage(content=result.summary)],
    }
