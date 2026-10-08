"""Demo/mock data for local development without AWS credentials."""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta

SERVICES = ["Amazon EC2", "Amazon S3", "Amazon RDS", "AWS Lambda", "Amazon CloudFront", "Amazon EKS"]
REGIONS = ["us-east-1", "us-west-2", "eu-west-1"]


def generate_demo_analysis(query: str) -> dict:
    """Generate realistic demo analysis results."""
    anomalies = [
        {
            "service": "Amazon EC2",
            "region": "us-east-1",
            "expected_cost": 2450.00,
            "actual_cost": 4120.50,
            "deviation_pct": 68.2,
            "timestamp": "2026-06-20",
            "severity": "critical",
        },
        {
            "service": "Amazon RDS",
            "region": "us-west-2",
            "expected_cost": 890.00,
            "actual_cost": 1340.00,
            "deviation_pct": 50.6,
            "timestamp": "2026-06-22",
            "severity": "high",
        },
        {
            "service": "Amazon S3",
            "region": "us-east-1",
            "expected_cost": 320.00,
            "actual_cost": 445.00,
            "deviation_pct": 39.1,
            "timestamp": "2026-06-23",
            "severity": "medium",
        },
        {
            "service": "AWS Lambda",
            "region": "eu-west-1",
            "expected_cost": 150.00,
            "actual_cost": 188.00,
            "deviation_pct": 25.3,
            "timestamp": "2026-06-24",
            "severity": "low",
        },
    ]

    recommendations = [
        {
            "id": str(uuid.uuid4()),
            "title": "Terminate 3 idle EC2 instances (i-0a1b2c, i-3d4e5f, i-6g7h8i)",
            "description": "These m5.xlarge instances have averaged <3% CPU over 21 days. Terminating saves $432/mo with zero performance impact.",
            "estimated_savings_monthly": 432.00,
            "risk_level": "low",
            "action_type": "terminate",
            "resource_ids": ["i-0a1b2c3d4", "i-3d4e5f6g7", "i-6g7h8i9j0"],
            "approved": None,
        },
        {
            "id": str(uuid.uuid4()),
            "title": "Rightsize RDS db.r5.2xlarge → db.r5.xlarge",
            "description": "Production RDS instance peaks at 35% CPU. Downsizing to xlarge maintains headroom while cutting costs.",
            "estimated_savings_monthly": 580.00,
            "risk_level": "medium",
            "action_type": "rightsize",
            "resource_ids": ["db-prod-analytics-01"],
            "approved": None,
        },
        {
            "id": str(uuid.uuid4()),
            "title": "Purchase EC2 Savings Plan for steady-state workloads",
            "description": "12 instances run 24/7. A 1-year No Upfront Compute Savings Plan yields 30% discount.",
            "estimated_savings_monthly": 1240.00,
            "risk_level": "low",
            "action_type": "reserve",
            "resource_ids": ["i-prod-web-01", "i-prod-web-02", "i-prod-api-01"],
            "approved": None,
        },
        {
            "id": str(uuid.uuid4()),
            "title": "Schedule dev/staging EC2 off-hours (nights + weekends)",
            "description": "18 dev instances run 24/7 but are only used during business hours. Scheduling saves 65% on these.",
            "estimated_savings_monthly": 890.00,
            "risk_level": "low",
            "action_type": "schedule",
            "resource_ids": ["i-dev-01", "i-dev-02", "i-staging-01"],
            "approved": None,
        },
        {
            "id": str(uuid.uuid4()),
            "title": "Delete 12 unattached EBS volumes (1.2 TB total)",
            "description": "Orphaned gp3 volumes from terminated instances. No snapshots depend on them.",
            "estimated_savings_monthly": 96.00,
            "risk_level": "low",
            "action_type": "terminate",
            "resource_ids": ["vol-01a", "vol-02b", "vol-03c"],
            "approved": None,
        },
    ]

    total_savings = sum(r["estimated_savings_monthly"] for r in recommendations)

    report = f"""## FinOps Agent Report

### Anomalies Detected: {len(anomalies)}
- **Amazon EC2** (us-east-1): 68.2% over expected ($4,120.50 vs $2,450.00) — CRITICAL
- **Amazon RDS** (us-west-2): 50.6% over expected ($1,340.00 vs $890.00) — HIGH
- **Amazon S3** (us-east-1): 39.1% over expected ($445.00 vs $320.00) — MEDIUM
- **AWS Lambda** (eu-west-1): 25.3% over expected ($188.00 vs $150.00) — LOW

### Recommendations: {len(recommendations)}
- [LOW] Terminate 3 idle EC2 instances: ~$432.00/mo
- [MEDIUM] Rightsize RDS db.r5.2xlarge → db.r5.xlarge: ~$580.00/mo
- [LOW] Purchase EC2 Savings Plan: ~$1,240.00/mo
- [LOW] Schedule dev/staging off-hours: ~$890.00/mo
- [LOW] Delete 12 unattached EBS volumes: ~$96.00/mo

### Total Potential Monthly Savings: ${total_savings:,.2f}

### Root Cause Analysis
The EC2 spike is driven by an auto-scaling event on 2026-06-20 that was never scaled back down due to a misconfigured CloudWatch alarm threshold."""

    return {
        "report": report,
        "anomalies": anomalies,
        "recommendations": recommendations,
        "anomalies_count": len(anomalies),
        "recommendations_count": len(recommendations),
        "total_potential_savings": total_savings,
    }


def generate_demo_answer(query: str) -> dict:
    """Generate a demo conversational answer."""
    answers = {
        "default": f"""Based on your last 30 days of billing data:

**Total Spend:** $18,420.50 (up 12% from previous period)

**Top Services by Cost:**
1. Amazon EC2: $8,340 (45%)  ↑ 22%
2. Amazon RDS: $4,120 (22%)  ↑ 8%
3. Amazon S3: $2,890 (16%)   → flat
4. AWS Lambda: $1,450 (8%)   ↓ 3%
5. CloudFront: $920 (5%)     → flat

**Key Insight:** The EC2 spike is caused by 3 idle instances left running after a load test on June 20th, plus an auto-scaling group that didn't scale back down.

**Immediate Actions:**
- Terminate idle instances i-0a1b2c, i-3d4e5f, i-6g7h8i (saves $432/mo)
- Fix ASG cooldown policy to prevent over-provisioning
- Consider Savings Plans for the 12 steady-state instances ($1,240/mo savings)""",
    }

    return {
        "answer": answers["default"],
        "sources": ["AWS Cost Explorer (30-day)", "CloudWatch Metrics", "EC2 Utilization Data"],
        "follow_up_suggestions": [
            "Which EC2 instances are idle?",
            "Show me the RDS rightsizing opportunity",
            "Forecast next month's spend",
            "What Savings Plans should I buy?",
        ],
    }


def generate_demo_forecast(horizon_days: int) -> dict:
    """Generate demo forecast data."""
    daily_avg = 614.00
    projected = daily_avg * horizon_days
    lower = projected * 0.85
    upper = projected * 1.18

    return {
        "projected_total": round(projected, 2),
        "confidence_interval": (round(lower, 2), round(upper, 2)),
        "daily_average": round(daily_avg, 2),
        "horizon_days": horizon_days,
        "by_service": None,
    }


def generate_demo_recommendations() -> list:
    """Return demo pending recommendations."""
    return generate_demo_analysis("")["recommendations"]


def generate_demo_history() -> list:
    """Generate demo agent run history."""
    runs = []
    for i in range(10):
        dt = datetime.utcnow() - timedelta(days=i * 2)
        runs.append({
            "id": i + 1,
            "started_at": dt.isoformat(),
            "status": "completed",
            "anomalies_found": random.randint(1, 6),
            "recommendations_generated": random.randint(2, 8),
            "total_savings_identified": round(random.uniform(500, 4000), 2),
            "trigger": random.choice(["scheduled", "user_query", "alert"]),
        })
    return runs
