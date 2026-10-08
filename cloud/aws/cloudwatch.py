"""AWS CloudWatch metrics integration."""

import boto3
from datetime import datetime, timedelta
from config import settings


def _get_cw_client():
    return boto3.client("cloudwatch", region_name=settings.aws_region)


async def get_ec2_utilization(instance_ids: list[str], days: int = 14) -> dict[str, dict]:
    """Fetch CPU/memory utilization for EC2 instances."""
    client = _get_cw_client()
    end_time = datetime.utcnow()
    start_time = end_time - timedelta(days=days)

    results = {}
    for instance_id in instance_ids:
        response = client.get_metric_statistics(
            Namespace="AWS/EC2",
            MetricName="CPUUtilization",
            Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
            StartTime=start_time,
            EndTime=end_time,
            Period=3600,  # Hourly
            Statistics=["Average", "Maximum"],
        )

        datapoints = response.get("Datapoints", [])
        if datapoints:
            avg_cpu = sum(d["Average"] for d in datapoints) / len(datapoints)
            max_cpu = max(d["Maximum"] for d in datapoints)
        else:
            avg_cpu, max_cpu = 0.0, 0.0

        results[instance_id] = {
            "avg_cpu_pct": round(avg_cpu, 2),
            "max_cpu_pct": round(max_cpu, 2),
            "is_idle": avg_cpu < 5.0,  # Under 5% avg = likely idle
            "is_oversized": max_cpu < 40.0,  # Never exceeds 40% = oversized
            "datapoints_count": len(datapoints),
        }

    return results
