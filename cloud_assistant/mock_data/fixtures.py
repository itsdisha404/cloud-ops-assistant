from __future__ import annotations

import random
from datetime import timedelta
from typing import Any, Final

from cloud_assistant import config

# --------------------------------------------------------------------------- #
# Catalogs — every value the generators draw from lives here
# --------------------------------------------------------------------------- #

REGIONS: Final[tuple[str, ...]] = (
    "us-east-1",
    "us-west-2",
    "eu-west-1",
    "eu-central-1",
    "ap-south-1",
)

# (service name as Cost Explorer reports it, min monthly USD, max monthly USD)
SERVICE_CATALOG: Final[tuple[tuple[str, float, float], ...]] = (
    ("Amazon Elastic Compute Cloud - Compute", 900.0, 3600.0),
    ("Amazon Relational Database Service", 220.0, 1400.0),
    ("Amazon Simple Storage Service", 90.0, 850.0),
    ("Elastic Load Balancing", 40.0, 280.0),
    ("Amazon CloudFront", 25.0, 320.0),
    ("AWS Lambda", 8.0, 190.0),
    ("Amazon CloudWatch", 18.0, 160.0),
    ("Amazon Virtual Private Cloud", 12.0, 110.0),
)

# Idle-resource archetypes: type, id prefix, reported state, evidence, cost band.
IDLE_CATALOG: Final[tuple[dict[str, Any], ...]] = (
    {
        "ResourceType": "ec2-instance",
        "prefix": "i-0",
        "State": "running",
        "IdleReason": "Average CPU utilization below 2% for the last 30 days",
        "low": 62.0,
        "high": 490.0,
    },
    {
        "ResourceType": "ebs-volume",
        "prefix": "vol-0",
        "State": "available",
        "IdleReason": "Volume has been unattached and in the 'available' state for 45 days",
        "low": 8.0,
        "high": 130.0,
    },
    {
        "ResourceType": "elastic-ip",
        "prefix": "eipalloc-0",
        "State": "unassociated",
        "IdleReason": "Elastic IP is allocated but not associated with any running instance",
        "low": 3.6,
        "high": 7.3,
    },
    {
        "ResourceType": "rds-instance",
        "prefix": "db-",
        "State": "available",
        "IdleReason": "Zero client connections recorded over the last 21 days",
        "low": 130.0,
        "high": 940.0,
    },
)

RDS_NAMES: Final[tuple[str, ...]] = (
    "legacy-reporting",
    "staging-mysql-01",
    "analytics-replica",
    "invoicing-archive",
)

# (bucket name stem, severity if the bucket turns out to be public)
BUCKET_CATALOG: Final[tuple[tuple[str, str], ...]] = (
    ("customer-invoices", "critical"),
    ("db-backups", "critical"),
    ("terraform-state", "critical"),
    ("cloudtrail-logs", "high"),
    ("ml-training-data", "high"),
    ("static-site-assets", "medium"),
    ("public-web-images", "low"),
)

# (policy name, policy arn, grants far more than needed, severity)
POLICY_CATALOG: Final[tuple[tuple[str, str, bool, str], ...]] = (
    ("AdministratorAccess", "arn:aws:iam::aws:policy/AdministratorAccess", True, "critical"),
    ("IAMFullAccess", "arn:aws:iam::aws:policy/IAMFullAccess", True, "critical"),
    ("PowerUserAccess", "arn:aws:iam::aws:policy/PowerUserAccess", True, "high"),
    ("AmazonS3FullAccess", "arn:aws:iam::aws:policy/AmazonS3FullAccess", True, "high"),
    ("AmazonS3ReadOnlyAccess", "arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess", False, "none"),
    ("CloudWatchLogsReadOnlyAccess", "arn:aws:iam::aws:policy/CloudWatchLogsReadOnlyAccess", False, "none"),
    (
        "AWSLambdaBasicExecutionRole",
        "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
        False,
        "none",
    ),
)

ROLE_NAMES: Final[tuple[str, ...]] = (
    "ci-deploy-role",
    "lambda-exec-role",
    "eks-node-role",
    "data-eng-analyst",
    "legacy-batch-runner",
    "ops-oncall-role",
)

_SEVERITY_RANK: Final[dict[str, int]] = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


# --------------------------------------------------------------------------- #
# Seeding
# --------------------------------------------------------------------------- #


def _rng(account_id: str, salt: str) -> random.Random:
    """Return a private Random seeded from the base seed, the account id, and a per-generator salt."""
    # A local Random instance, never the module-global one, so nothing else in the
    # process can perturb these draws. The salt keeps each generator on its own
    # stream, so cost data and idle data are not correlated artifacts of one seed.
    return random.Random(f"{config.MOCK_SEED}:{account_id}:{salt}")


def _is_clean(account_id: str) -> bool:
    """True when the account is one of the reserved 'nothing wrong here' accounts."""
    return account_id in config.CLEAN_ACCOUNT_IDS


def _hex_id(rng: random.Random, prefix: str) -> str:
    """Build an AWS-style resource id such as 'i-0a1b2c3d4e5f60718'."""
    return f"{prefix}{rng.getrandbits(64):016x}"


# --------------------------------------------------------------------------- #
# Generators
# --------------------------------------------------------------------------- #


def cost_by_service(account_id: str) -> dict[str, Any]:
    """Return month-to-date spend grouped by service, in AWS Cost Explorer wire format."""
    rng = _rng(account_id, "cost_by_service")
    # Driven by config.REFERENCE_DATE rather than date.today() so the billing window
    # is as reproducible as the amounts; without it a committed transcript would
    # stop matching a freshly generated one the next day.
    today = config.REFERENCE_DATE
    period = {
        "Start": today.replace(day=1).isoformat(),
        "End": (today + timedelta(days=1)).isoformat(),  # Cost Explorer's End is exclusive
    }

    count = rng.randint(5, len(SERVICE_CATALOG))
    groups = []
    for service, low, high in SERVICE_CATALOG[:count]:
        amount = round(rng.uniform(low, high), 2)
        groups.append(
            {
                "Keys": [service],
                # Amounts are strings here because that is what Cost Explorer
                # actually returns. Left deliberately unsorted, so ranking the
                # top services is real work for the agent rather than a copy.
                "Metrics": {"UnblendedCost": {"Amount": f"{amount:.2f}", "Unit": "USD"}},
            }
        )

    return {
        "ResultsByTime": [
            {
                "TimePeriod": period,
                "Total": {},
                "Groups": groups,
                "Estimated": True,
            }
        ],
        "GroupDefinitions": [{"Type": "DIMENSION", "Key": "SERVICE"}],
    }


def idle_resources(account_id: str) -> dict[str, Any]:
    """Return idle or unattached resources for the account; empty for the clean account."""
    if _is_clean(account_id):
        return {"IdleResources": []}

    rng = _rng(account_id, "idle_resources")
    rds_pool = list(RDS_NAMES)
    rng.shuffle(rds_pool)

    resources = []
    for archetype in rng.choices(IDLE_CATALOG, k=rng.randint(2, 5)):
        if archetype["ResourceType"] == "rds-instance" and rds_pool:
            resource_id = f"{archetype['prefix']}{rds_pool.pop()}"
        else:
            resource_id = _hex_id(rng, archetype["prefix"])
        resources.append(
            {
                "ResourceId": resource_id,
                "ResourceType": archetype["ResourceType"],
                "State": archetype["State"],
                "Region": rng.choice(REGIONS),
                "MonthlyCostUsd": round(rng.uniform(archetype["low"], archetype["high"]), 2),
                "IdleReason": archetype["IdleReason"],
            }
        )

    return {"IdleResources": resources}


def public_buckets(account_id: str) -> dict[str, Any]:
    """Return S3 buckets with their public-access posture; the clean account has none public."""
    rng = _rng(account_id, "public_buckets")
    clean = _is_clean(account_id)

    stems = rng.sample(BUCKET_CATALOG, k=rng.randint(4, 6))
    public_count = 0 if clean else rng.randint(1, min(3, len(stems)))

    buckets = []
    for index, (stem, severity) in enumerate(stems):
        is_public = index < public_count
        buckets.append(
            {
                "Name": f"{stem}-{account_id[-6:]}",
                "CreationDate": f"20{rng.randint(19, 24)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
                "PolicyStatus": {"IsPublic": is_public},
                "PublicAccessBlockConfiguration": {
                    "BlockPublicAcls": not is_public,
                    "IgnorePublicAcls": not is_public,
                    "BlockPublicPolicy": not is_public,
                    "RestrictPublicBuckets": not is_public,
                },
                "Severity": severity if is_public else "none",
            }
        )

    return {"Buckets": buckets}


def iam_roles(account_id: str) -> dict[str, Any]:
    """Return IAM roles with their attached policies; the clean account has none over-permissioned."""
    rng = _rng(account_id, "iam_roles")
    clean = _is_clean(account_id)

    risky = [policy for policy in POLICY_CATALOG if policy[2]]
    safe = [policy for policy in POLICY_CATALOG if not policy[2]]

    names = rng.sample(ROLE_NAMES, k=rng.randint(3, 5))
    over_permissioned_count = 0 if clean else rng.randint(1, min(2, len(names)))

    roles = []
    for index, role_name in enumerate(names):
        attached = [rng.choice(safe)]
        if index < over_permissioned_count:
            attached.insert(0, rng.choice(risky))

        over_permissioned = any(policy[2] for policy in attached)
        severity = max(
            (policy[3] for policy in attached if policy[2]),
            key=lambda level: _SEVERITY_RANK[level],
            default="none",
        )
        roles.append(
            {
                "RoleName": role_name,
                "Arn": f"arn:aws:iam::{account_id}:role/{role_name}",
                "AttachedPolicies": [
                    {"PolicyName": name, "PolicyArn": arn} for name, arn, _, _ in attached
                ],
                "OverPermissioned": over_permissioned,
                "Severity": severity,
            }
        )

    return {"Roles": roles}
