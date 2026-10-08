"""Shared test fixtures and configuration."""

import pytest
import pandas as pd
import numpy as np
from datetime import date, timedelta


@pytest.fixture
def sample_cost_data():
    """Generate 30 days of sample cost data with a spike on day 15."""
    dates = pd.date_range("2024-01-01", periods=30, freq="D")
    np.random.seed(42)

    services = [
        ("Amazon EC2", "us-east-1"),
        ("Amazon S3", "us-east-1"),
        ("Amazon RDS", "us-west-2"),
    ]

    results = []
    for d in dates:
        groups = []
        for service, region in services:
            base = {"Amazon EC2": 100, "Amazon S3": 30, "Amazon RDS": 80}[service]
            noise = np.random.normal(0, base * 0.1)
            cost = base + noise

            # Inject anomaly on day 15 for EC2
            if d == dates[14] and service == "Amazon EC2":
                cost = 500.0

            groups.append({
                "keys": [service, region],
                "metrics": {
                    "UnblendedCost": {"Amount": str(max(cost, 0)), "Unit": "USD"},
                    "UsageQuantity": {"Amount": "100", "Unit": "Hours"},
                },
            })

        results.append({
            "time_period": {"start": str(d.date()), "end": str(d.date())},
            "groups": groups,
        })

    return {"results_by_time": results}


@pytest.fixture
def sample_cost_dataframe():
    """Cost data as a DataFrame (for analytics modules)."""
    dates = pd.date_range("2024-01-01", periods=30, freq="D")
    np.random.seed(42)
    rows = []

    for d in dates:
        for service, base in [("EC2", 100), ("S3", 30), ("RDS", 80)]:
            cost = base + np.random.normal(0, base * 0.1)
            rows.append({"date": d, "service": service, "region": "us-east-1", "cost": max(cost, 0)})

    return pd.DataFrame(rows)


@pytest.fixture
def stable_cost_data():
    """Perfectly stable cost data — should produce no anomalies."""
    dates = pd.date_range("2024-01-01", periods=30, freq="D")
    return {
        "results_by_time": [
            {
                "time_period": {"start": str(d.date()), "end": str(d.date())},
                "groups": [
                    {
                        "keys": ["Amazon S3", "us-west-2"],
                        "metrics": {"UnblendedCost": {"Amount": "50.00", "Unit": "USD"}},
                    }
                ],
            }
            for d in dates
        ]
    }


@pytest.fixture
def mock_anomalies():
    """Sample anomaly list for testing downstream agents."""
    return [
        {
            "service": "Amazon EC2",
            "region": "us-east-1",
            "expected_cost": 100.0,
            "actual_cost": 500.0,
            "deviation_pct": 400.0,
            "timestamp": "2024-01-15",
            "severity": "critical",
        },
        {
            "service": "Amazon RDS",
            "region": "us-west-2",
            "expected_cost": 80.0,
            "actual_cost": 140.0,
            "deviation_pct": 75.0,
            "timestamp": "2024-01-20",
            "severity": "high",
        },
    ]


@pytest.fixture
def mock_recommendations():
    """Sample recommendations for testing executor/notifications."""
    return [
        {
            "id": "rec-001",
            "title": "Terminate idle i-abc123",
            "description": "Instance has <2% CPU for 14 days",
            "estimated_savings_monthly": 45.0,
            "risk_level": "low",
            "action_type": "terminate",
            "resource_ids": ["i-abc123"],
            "terraform_plan": None,
            "approved": None,
        },
        {
            "id": "rec-002",
            "title": "Rightsize i-def456 from m5.xlarge to m5.large",
            "description": "Max CPU never exceeds 30%",
            "estimated_savings_monthly": 92.0,
            "risk_level": "medium",
            "action_type": "rightsize",
            "resource_ids": ["i-def456"],
            "terraform_plan": 'resource "aws_instance" "example" { instance_type = "m5.large" }',
            "approved": None,
        },
    ]
