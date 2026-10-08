"""Deterministic seed: LocalStack resources + CloudWatch CPU, then Postgres reference data and 60 days of synthetic cost.

Runs as a one-shot container with DUMMY LocalStack credentials. Idempotent for LocalStack (resources are looked up by
the SeedRole tag) and a full reset for the Postgres tables. Nothing here can reach a real cloud: the endpoint is fixed.
"""
import hashlib
import math
import os
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import boto3
import psycopg

ENDPOINT = os.environ["AWS_ENDPOINT_URL"]
assert "localstack" in ENDPOINT or "localhost" in ENDPOINT, "refusing to seed anything but LocalStack"
REGION = "us-east-1"
TODAY = date.fromisoformat(os.environ["SEED_TODAY"]) if os.environ.get("SEED_TODAY") else datetime.now(timezone.utc).date()
DB = os.environ["OWNER_DATABASE_URL"]

# role -> (name, tags, cpu profile)
EC2 = {
    "pay_batch_1": ("payments-batch-dev-1", {"TenantId": "payments", "Owner": "payments-platform", "Environment": "dev", "Workload": "batch-dev"}, "idle"),
    "pay_batch_2": ("payments-batch-dev-2", {"TenantId": "payments", "Owner": "payments-platform", "Environment": "dev", "Workload": "batch-dev"}, "idle"),
    "pay_dr": ("payments-dr-standby", {"TenantId": "payments", "Owner": "payments-platform", "Environment": "production", "Protected": "true", "Workload": "dr-standby"}, "idle"),
    "pay_shared": ("payments-shared-ingest", {"TenantId": "payments", "Owner": "payments-risk", "Environment": "staging", "Workload": "shared-ingest"}, "idle"),
    "pay_api": ("payments-api-1", {"TenantId": "payments", "Owner": "payments-platform", "Environment": "production", "Workload": "checkout-api"}, "busy"),
    "ana_etl": ("analytics-etl-1", {"TenantId": "analytics", "Owner": "analytics-data", "Environment": "production", "Workload": "etl"}, "busy"),
}
VOLUMES = {"ana_vol": ("analytics-notebook-scratch", {"TenantId": "analytics", "Owner": "analytics-data", "Environment": "dev", "Workload": "notebooks"})}


def jitter(key: str, i: int, spread: float) -> float:
    h = int(hashlib.sha256(f"{key}:{i}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return (h - 0.5) * 2 * spread


def seed_localstack() -> dict:
    ec2 = boto3.client("ec2", region_name=REGION)
    cw = boto3.client("cloudwatch", region_name=REGION)
    ami = ec2.describe_images()["Images"][0]["ImageId"]
    ids = {}
    existing = {}
    for rv in ec2.describe_instances(Filters=[{"Name": "tag-key", "Values": ["SeedRole"]}])["Reservations"]:
        for i in rv["Instances"]:
            t = {x["Key"]: x["Value"] for x in i.get("Tags", [])}
            if i["State"]["Name"] != "terminated":
                existing[t["SeedRole"]] = i["InstanceId"]
    for role, (name, tags, prof) in EC2.items():
        if role in existing:
            ids[role] = existing[role]
            continue
        allt = {**tags, "Name": name, "SeedRole": role}
        r = ec2.run_instances(ImageId=ami, InstanceType="t3.micro", MinCount=1, MaxCount=1,
                              TagSpecifications=[{"ResourceType": "instance", "Tags": [{"Key": k, "Value": v} for k, v in allt.items()]}])
        ids[role] = r["Instances"][0]["InstanceId"]
    for role, (name, tags) in VOLUMES.items():
        vols = ec2.describe_volumes(Filters=[{"Name": "tag:SeedRole", "Values": [role]}])["Volumes"]
        if vols:
            ids[role] = vols[0]["VolumeId"]
            continue
        allt = {**tags, "Name": name, "SeedRole": role}
        v = ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=100, VolumeType="gp3",
                              TagSpecifications=[{"ResourceType": "volume", "Tags": [{"Key": k, "Value": v} for k, v in allt.items()]}])
        ids[role] = v["VolumeId"]
    # CloudWatch CPU: idle roles hover at 1-4%, busy roles at 35-55% (7 daily points each, inside the 2-week put window)
    now = datetime.now(timezone.utc)
    for role, (_, _, prof) in EC2.items():
        for d in range(7):
            val = (2.2 + jitter(role, d, 1.6)) if prof == "idle" else (45 + jitter(role, d, 10))
            cw.put_metric_data(Namespace="AWS/EC2", MetricData=[{
                "MetricName": "CPUUtilization", "Dimensions": [{"Name": "InstanceId", "Value": ids[role]}],
                "Timestamp": now - timedelta(days=d, hours=1), "Value": round(val, 2), "Unit": "Percent"}])
    print("localstack ids:", ids)
    return ids


def seed_db(ids: dict) -> None:
    wk = lambda d: 1.0 if d.weekday() < 5 else 0.8
    day = lambda n: TODAY - timedelta(days=n)
    with psycopg.connect(DB) as c:
        cur = c.cursor()
        c.execute("truncate tenants cascade")      # full tenant-data reset (principals cascade with tenants)
        c.execute("insert into tenants values ('payments','Payments','payments-eng','{\"min_daily_excess_usd\":5}',now()), "
                  "('analytics','Analytics','analytics-eng','{\"min_daily_excess_usd\":5}',now())")
        cur.executemany("insert into owners values (%s,%s,%s)", [
            ("payments", "payments-platform", "Payments Platform"), ("payments", "payments-risk", "Payments Risk"),
            ("analytics", "analytics-data", "Analytics Data"), ("analytics", "analytics-ml", "Analytics ML")])
        cur.executemany("insert into deployments values (%s,%s,%s,%s,%s)", [
            ("payments", ids["pay_batch_1"], "payments-platform", "terraform:payments-batch", day(30)),
            ("payments", ids["pay_batch_2"], "payments-platform", "terraform:payments-batch", day(30)),
            ("payments", ids["pay_dr"], "payments-platform", "terraform:payments-dr", day(300)),
            ("payments", ids["pay_shared"], "payments-platform", "terraform:payments-ingest", day(120)),   # conflicts with Owner=payments-risk tag
            ("analytics", ids["ana_vol"], "analytics-data", "notebook-service", day(40))])
        cur.executemany("insert into naming_rules values (%s,%s,%s,%s)", [
            ("payments", "name", "payments-batch-*", "payments-platform"), ("payments", "name", "payments-dr-*", "payments-platform"),
            ("analytics", "name", "analytics-notebook-*", "analytics-data")])
        c.execute("insert into known_events values ('ev_checkout_v2','payments','checkout-api',%s,%s,'launch','Checkout v2 launch campaign',150)",
                  (day(5), day(-3)))
        cur.executemany("""insert into principals (principal_id, tenant_id, role, display_name, token_sha256, pin_hash, scope) values (%s,%s,%s,%s,%s,%s,%s)""", principals())

        rows = []

        def add(tenant, d, service, workload, rid, owner, amount):
            rows.append((tenant, d, service, workload, rid or "", owner, Decimal(str(round(amount, 4))), "USD"))

        for n in range(60, 0, -1):                     # day(60) .. day(1): last complete day is yesterday
            d = day(n)
            add("payments", d, "AmazonRDS", "ledger-db", None, "payments-platform", 38 * wk(d) + jitter("ledger", n, 0.8))
            launch = 2.4 if n <= 3 else 1.0           # launch spike in the 3-day analysis window
            add("payments", d, "AWSLambda", "checkout-api", None, "payments-platform", (22 * wk(d) + jitter("lambda", n, 0.6)) * launch)
            add("payments", d, "AmazonEC2", "checkout-api", ids["pay_api"], "payments-platform", 15.0)
            add("payments", d, "AmazonEC2", "batch-dev", None, "payments-platform", 4.0 + jitter("batchbase", n, 0.25))
            if n <= 9:                                 # two idle instances forgotten ~9 days ago
                add("payments", d, "AmazonEC2", "batch-dev", ids["pay_batch_1"], "payments-platform", 7.0)
                add("payments", d, "AmazonEC2", "batch-dev", ids["pay_batch_2"], "payments-platform", 7.0)
            add("payments", d, "AmazonEC2", "dr-standby", ids["pay_dr"], "payments-platform", 12.5)
            add("payments", d, "AmazonEC2", "shared-ingest", ids["pay_shared"], None, 9.0)
            add("analytics", d, "AmazonEC2", "etl", ids["ana_etl"], "analytics-data", 21 * wk(d) + jitter("etl", n, 0.5))
            add("analytics", d, "AmazonS3", "warehouse", None, "analytics-data", 14 * wk(d) + jitter("s3", n, 0.4))
            add("analytics", d, "AmazonEBS", "notebooks", ids["ana_vol"], "analytics-data", 5.33)
        cur.executemany("insert into cost_facts (tenant_id, usage_date, service, workload, resource_id, owner_tag, amount, currency) values (%s,%s,%s,%s,%s,%s,%s,%s)", rows)

        util = []
        for role, (_, tags, prof) in EC2.items():
            for n in range(1, 15):
                v = (2.2 + jitter(role, n, 1.6)) if prof == "idle" else (45 + jitter(role, n, 10))
                util.append((tags["TenantId"], ids[role], day(n), Decimal(str(round(max(v, 0.1), 2)))))
        cur.executemany("insert into utilization_facts values (%s,%s,%s,%s)", util)
        c.commit()
    print(f"seeded {len(rows)} cost facts, anchor date {TODAY}")


def principals() -> list:
    sys.path.insert(0, "/seed")
    h = lambda s: hashlib.sha256(s.encode()).hexdigest()
    pin = lambda p: hashlib.pbkdf2_hmac("sha256", p.encode(), b"sentinel-demo-salt", 20_000).hex()
    mk = lambda pid, t, role, name, pn=None: (pid, t, role, name, h(f"tok_{pid}_demo"), pin(pn) if pn else None, [])
    return [
        mk("payments-analyst", "payments", "analyst", "Priya (Payments analyst)"),
        mk("payments-approver", "payments", "approver", "Arjun (Payments owner)", "1111"),
        mk("payments-reviewer", "payments", "reviewer", "Rhea (Payments platform reviewer)", "2222"),
        mk("payments-finance", "payments", "finance", "Farah (Payments finance)"),
        mk("analytics-analyst", "analytics", "analyst", "Anil (Analytics analyst)"),
        mk("analytics-approver", "analytics", "approver", "Asha (Analytics owner)", "3333"),
        mk("analytics-reviewer", "analytics", "reviewer", "Ravi (Analytics platform reviewer)", "4444"),
        ("n8n-automation", None, "automation", "n8n workflows", h(os.environ["N8N_API_TOKEN"]), None, ["payments", "analytics"]),
    ]


if __name__ == "__main__":
    seed_db(seed_localstack())
