"""Fault injection for the reserved failure account.

Account ``config.FAULT_ACCOUNT_ID`` exists so the demo can prove its error
handling instead of asserting it. Faults are looked up per tool name, never
drawn at random, so the same tool fails the same way on every run — a flaky
demo would prove nothing.

Two distinct failure modes are covered on purpose:

* a **returned payload** the agent must notice is wrong — either an AWS-style
  ``Error`` envelope or a structurally malformed response; and
* a **raised exception**, which exercises the ``except Exception`` branch in the
  tool wrapper and proves that branch is not dead code.
"""

from __future__ import annotations

import copy
from typing import Any, Final

from cloud_assistant import config


class MockFaultError(RuntimeError):
    """Raised by an injected fault to exercise the unexpected-exception path."""


_THROTTLING: Final[dict[str, Any]] = {
    "Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}
}

_FAULTS: Final[dict[str, dict[str, Any]]] = {
    "get_cost_by_service": _THROTTLING,
    # Structurally wrong rather than an error envelope: the expected key is
    # present but null, which is the nastier case for a naive consumer.
    "check_public_s3_buckets": {"Buckets": None},
    "check_overpermissioned_iam_roles": {
        "Error": {
            "Code": "AccessDeniedException",
            "Message": "User is not authorized to perform: iam:ListAttachedRolePolicies",
        }
    },
    "estimate_savings": _THROTTLING,
    "generate_remediation_plan": _THROTTLING,
}

_RAISING_TOOLS: Final[frozenset[str]] = frozenset({"get_idle_resources"})
"""Tools whose injected fault raises instead of returning, exercising the tool's except branch."""

_DEFAULT_FAULT: Final[dict[str, Any]] = {
    "Error": {"Code": "InternalServiceException", "Message": "The request processing has failed"}
}


def maybe_inject_fault(account_id: str, tool_name: str) -> dict[str, Any] | None:
    """Return a malformed payload for the fault account, ``None`` otherwise; may raise MockFaultError."""
    if account_id != config.FAULT_ACCOUNT_ID:
        return None

    if tool_name in _RAISING_TOOLS:
        raise MockFaultError(f"injected fault: {tool_name} failed for account {account_id}")

    # Deep copy so a caller mutating the payload cannot corrupt the fault table.
    return copy.deepcopy(_FAULTS.get(tool_name, _DEFAULT_FAULT))
