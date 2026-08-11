"""Security posture inspection tools bound to the security audit agent.

Both tools deliberately return *every* resource they inspect, safe ones included,
rather than pre-filtering to the risky ones. Deciding what counts as a finding is
the agent's job, and handing it only bad news would make the clean-account skip
branch pass for the wrong reason.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from cloud_assistant.mock_data import fixtures
from cloud_assistant.tools import guarded_call


class CheckPublicS3BucketsArgs(BaseModel):
    """Arguments for check_public_s3_buckets."""

    account_id: str = Field(
        description="The 12-digit AWS account id to inspect, e.g. '333333333333'. Use the account id given in the request."
    )


class CheckOverpermissionedIamRolesArgs(BaseModel):
    """Arguments for check_overpermissioned_iam_roles."""

    account_id: str = Field(
        description="The 12-digit AWS account id to inspect, e.g. '333333333333'. Use the account id given in the request."
    )


@tool("check_public_s3_buckets", args_schema=CheckPublicS3BucketsArgs)
def check_public_s3_buckets(account_id: str) -> dict[str, Any]:
    """Return every S3 bucket in one account together with its public-access posture.

    The response is {"Buckets": [...]} where each bucket has Name, PolicyStatus.IsPublic,
    a PublicAccessBlockConfiguration of four booleans, and a Severity. Buckets listed
    here are NOT all problems: a bucket is only a finding when PolicyStatus.IsPublic
    is true. A bucket with IsPublic false and all four access blocks enabled is
    correctly configured and must not be reported. If the response contains an
    "Error" key, or Buckets is null rather than a list, the call failed: report the
    failure instead of concluding the account is clean.
    """
    return guarded_call(
        "check_public_s3_buckets",
        account_id,
        lambda: fixtures.public_buckets(account_id),
    )


@tool("check_overpermissioned_iam_roles", args_schema=CheckOverpermissionedIamRolesArgs)
def check_overpermissioned_iam_roles(account_id: str) -> dict[str, Any]:
    """Return every IAM role in one account together with its attached policies.

    The response is {"Roles": [...]} where each role has RoleName, Arn,
    AttachedPolicies, an OverPermissioned boolean and a Severity. Roles listed here
    are NOT all problems: a role is only a finding when OverPermissioned is true,
    which happens when it carries a broad policy such as AdministratorAccess or
    IAMFullAccess. A role holding only read-only or service-role policies is
    correctly scoped and must not be reported. If the response contains an "Error"
    key, or Roles is null rather than a list, the call failed: report the failure
    instead of concluding the account is clean.
    """
    return guarded_call(
        "check_overpermissioned_iam_roles",
        account_id,
        lambda: fixtures.iam_roles(account_id),
    )
