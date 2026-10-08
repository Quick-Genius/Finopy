"""AWS EC2 Advisor — identifies idle, oversized, and unattached resources."""

from __future__ import annotations

import boto3
from config import settings
from cloud.aws.cloudwatch import get_ec2_utilization


def _get_ec2_client():
    return boto3.client("ec2", region_name=settings.aws_region)


async def find_idle_instances(cpu_threshold: float = 5.0, days: int = 14) -> list[dict]:
    """Find EC2 instances with consistently low CPU utilization."""
    ec2 = _get_ec2_client()

    # Get all running instances
    response = ec2.describe_instances(
        Filters=[{"Name": "instance-state-name", "Values": ["running"]}]
    )

    instance_ids = []
    instance_meta = {}
    for reservation in response.get("Reservations", []):
        for inst in reservation.get("Instances", []):
            iid = inst["InstanceId"]
            instance_ids.append(iid)
            instance_meta[iid] = {
                "instance_type": inst.get("InstanceType"),
                "launch_time": str(inst.get("LaunchTime", "")),
                "tags": {t["Key"]: t["Value"] for t in inst.get("Tags", [])},
            }

    if not instance_ids:
        return []

    utilization = await get_ec2_utilization(instance_ids, days=days)

    idle_instances = []
    for iid, metrics in utilization.items():
        if metrics["is_idle"]:
            idle_instances.append({
                "instance_id": iid,
                "avg_cpu_pct": metrics["avg_cpu_pct"],
                "max_cpu_pct": metrics["max_cpu_pct"],
                **instance_meta.get(iid, {}),
            })

    return idle_instances


async def find_oversized_instances(max_cpu_threshold: float = 40.0, days: int = 14) -> list[dict]:
    """Find instances that never exceed a CPU threshold — candidates for downsizing."""
    ec2 = _get_ec2_client()

    response = ec2.describe_instances(
        Filters=[{"Name": "instance-state-name", "Values": ["running"]}]
    )

    instance_ids = []
    instance_meta = {}
    for reservation in response.get("Reservations", []):
        for inst in reservation.get("Instances", []):
            iid = inst["InstanceId"]
            instance_ids.append(iid)
            instance_meta[iid] = {
                "instance_type": inst.get("InstanceType"),
                "tags": {t["Key"]: t["Value"] for t in inst.get("Tags", [])},
            }

    if not instance_ids:
        return []

    utilization = await get_ec2_utilization(instance_ids, days=days)

    oversized = []
    for iid, metrics in utilization.items():
        if metrics["is_oversized"] and not metrics["is_idle"]:
            oversized.append({
                "instance_id": iid,
                "avg_cpu_pct": metrics["avg_cpu_pct"],
                "max_cpu_pct": metrics["max_cpu_pct"],
                **instance_meta.get(iid, {}),
            })

    return oversized


async def find_unattached_ebs_volumes() -> list[dict]:
    """Find EBS volumes that are not attached to any instance."""
    ec2 = _get_ec2_client()

    response = ec2.describe_volumes(
        Filters=[{"Name": "status", "Values": ["available"]}]
    )

    return [
        {
            "volume_id": vol["VolumeId"],
            "size_gb": vol["Size"],
            "volume_type": vol["VolumeType"],
            "created": str(vol.get("CreateTime", "")),
            "monthly_cost_estimate": _estimate_ebs_cost(vol["Size"], vol["VolumeType"]),
        }
        for vol in response.get("Volumes", [])
    ]


async def find_unattached_elastic_ips() -> list[dict]:
    """Find Elastic IPs not associated with any instance ($3.6/month each)."""
    ec2 = _get_ec2_client()

    response = ec2.describe_addresses()

    return [
        {
            "allocation_id": addr["AllocationId"],
            "public_ip": addr["PublicIp"],
            "monthly_cost": 3.60,
        }
        for addr in response.get("Addresses", [])
        if not addr.get("InstanceId") and not addr.get("NetworkInterfaceId")
    ]


def _estimate_ebs_cost(size_gb: int, volume_type: str) -> float:
    """Estimate monthly cost for an EBS volume."""
    prices_per_gb = {
        "gp3": 0.08,
        "gp2": 0.10,
        "io1": 0.125,
        "io2": 0.125,
        "st1": 0.045,
        "sc1": 0.015,
        "standard": 0.05,
    }
    return round(size_gb * prices_per_gb.get(volume_type, 0.10), 2)
