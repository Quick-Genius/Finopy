"""AWS Cost Explorer integration."""

from __future__ import annotations

import boto3
from config import settings


def _get_ce_client():
    """Get Cost Explorer client."""
    return boto3.client("ce", region_name=settings.aws_region)


async def fetch_cost_and_usage(
    start_date: str,
    end_date: str,
    granularity: str = "DAILY",
    group_by: list[str] | None = None,
) -> dict:
    """Fetch cost and usage data from AWS Cost Explorer.

    Args:
        start_date: Start date (YYYY-MM-DD).
        end_date: End date (YYYY-MM-DD).
        granularity: DAILY | MONTHLY | HOURLY.
        group_by: Dimensions to group by (SERVICE, REGION, etc.).

    Returns:
        Normalized cost data dictionary.
    """
    client = _get_ce_client()

    group_definitions = []
    for dim in (group_by or ["SERVICE"]):
        group_definitions.append({"Type": "DIMENSION", "Key": dim})

    response = client.get_cost_and_usage(
        TimePeriod={"Start": start_date, "End": end_date},
        Granularity=granularity,
        Metrics=["UnblendedCost", "UsageQuantity"],
        GroupBy=group_definitions,
    )

    # Normalize response
    results = []
    for period in response.get("ResultsByTime", []):
        results.append({
            "time_period": {
                "start": period["TimePeriod"]["Start"],
                "end": period["TimePeriod"]["End"],
            },
            "groups": [
                {
                    "keys": group["Keys"],
                    "metrics": group["Metrics"],
                }
                for group in period.get("Groups", [])
            ],
        })

    return {"results_by_time": results}


async def get_rightsizing_recommendations() -> list[dict]:
    """Fetch EC2 rightsizing recommendations."""
    client = _get_ce_client()

    response = client.get_rightsizing_recommendation(
        Service="AmazonEC2",
        Configuration={
            "RecommendationTarget": "SAME_INSTANCE_FAMILY",
            "BenefitsConsidered": True,
        },
    )

    return [
        {
            "instance_id": rec.get("CurrentInstance", {}).get("ResourceId"),
            "current_type": rec.get("CurrentInstance", {}).get("ResourceDetails", {}).get(
                "EC2ResourceDetails", {}
            ).get("InstanceType"),
            "recommended_type": rec.get("ModifyRecommendationDetail", {}).get(
                "TargetInstances", [{}]
            )[0].get("ResourceDetails", {}).get("EC2ResourceDetails", {}).get("InstanceType"),
            "estimated_savings": float(
                rec.get("ModifyRecommendationDetail", {}).get(
                    "TargetInstances", [{}]
                )[0].get("EstimatedMonthlySavings", {}).get("Value", 0)
            ),
        }
        for rec in response.get("RightsizingRecommendations", [])
    ]
