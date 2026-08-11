"""Acceptance scenarios with the graph path each one must take.

``expected_path`` is what turns this file from a demo script into a test. Each
scenario asserts not just that an answer came back, but that it came back
*through the right nodes* — which is the only way to prove the conditional edges
actually branch rather than every request quietly running the full pipeline.

Paths list node names only. Every run ends at END, so recording it would add a
constant to all six lists and distinguish nothing.
"""

from __future__ import annotations

from typing import TypedDict

from cloud_assistant import config


class Scenario(TypedDict):
    """One acceptance scenario: a request, the account it runs against, and the path it must take."""

    id: int
    slug: str
    request: str
    account_id: str
    expected_path: list[str]
    covers: str


SCENARIOS: list[Scenario] = [
    {
        "id": 1,
        "slug": "cost-full",
        "request": (
            "Our AWS bill jumped 30% this month — find the waste and tell me what to shut off."
        ),
        "account_id": config.DEFAULT_ACCOUNT_ID,
        "expected_path": ["supervisor", "cost_analysis", "cost_recommendation"],
        "covers": "Workflow 1 end to end: waste found, so recommendations run.",
    },
    {
        "id": 2,
        "slug": "cost-clean",
        "request": "Review this account for wasted spend.",
        "account_id": config.CLEAN_ACCOUNT_ID,
        "expected_path": ["supervisor", "cost_analysis"],
        "covers": "Conditional skip: no idle resources, so recommendation is skipped entirely.",
    },
    {
        "id": 3,
        "slug": "security-full",
        "request": "Audit this account for public S3 buckets and over-permissioned IAM roles.",
        "account_id": "333333333333",
        "expected_path": ["supervisor", "security_audit", "security_remediation"],
        "covers": "Workflow 2 end to end: findings exist, so remediation runs.",
    },
    {
        "id": 4,
        "slug": "security-clean",
        "request": "Run a compliance check on this account.",
        "account_id": config.CLEAN_ACCOUNT_ID,
        "expected_path": ["supervisor", "security_audit"],
        "covers": "Conditional skip: no findings, so remediation is skipped entirely.",
    },
    {
        "id": 5,
        "slug": "ambiguous",
        "request": "Can you take a look at my cloud setup?",
        "account_id": config.DEFAULT_ACCOUNT_ID,
        "expected_path": ["supervisor"],
        "covers": "Ambiguous request: neither workflow is touched, a clarification is returned.",
    },
    {
        "id": 6,
        "slug": "fault-injection",
        "request": "Find idle resources and estimate savings.",
        "account_id": config.FAULT_ACCOUNT_ID,
        "expected_path": ["supervisor", "cost_analysis"],
        "covers": "Fault injection: both cost tools fail, the run degrades without crashing.",
    },
    # ------------------------------------------------------------------- #
    # Sample scenarios from the spec (probable input -> expected output),
    # reproduced verbatim so the acceptance suite also proves fidelity to
    # the exact wording and account ids given in the task spec, not just to
    # this repo's own scenario set above.
    # ------------------------------------------------------------------- #
    {
        "id": 7,
        "slug": "spec-cost-savings-found",
        "request": "Can you check if we're wasting money on idle infra in account 111122223333?",
        "account_id": "111122223333",
        "expected_path": ["supervisor", "cost_analysis", "cost_recommendation"],
        "covers": "Spec scenario 1: cost workflow, savings found.",
    },
    {
        "id": 8,
        "slug": "spec-cost-no-savings",
        "request": "Any cost savings available in account 444455556666?",
        "account_id": "444455556666",
        "expected_path": ["supervisor", "cost_analysis"],
        "covers": "Spec scenario 2: cost workflow, nothing to optimize (skip branch).",
    },
    {
        "id": 9,
        "slug": "spec-security-findings",
        "request": "Run a compliance check on our prod account for public buckets and IAM issues.",
        "account_id": config.DEFAULT_ACCOUNT_ID,
        "expected_path": ["supervisor", "security_audit", "security_remediation"],
        "covers": "Spec scenario 3: security workflow, findings found.",
    },
    {
        "id": 10,
        "slug": "spec-security-clean",
        "request": "Is account 777788889999 compliant?",
        "account_id": "777788889999",
        "expected_path": ["supervisor", "security_audit"],
        "covers": "Spec scenario 4: security workflow, clean account (skip branch).",
    },
    {
        "id": 11,
        "slug": "spec-offtopic",
        "request": "What's the weather like today?",
        "account_id": config.DEFAULT_ACCOUNT_ID,
        "expected_path": ["supervisor"],
        "covers": "Spec scenario 5: ambiguous/out-of-scope routing, graceful rejection.",
    },
]
