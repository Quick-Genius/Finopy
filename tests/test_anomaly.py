"""Tests for anomaly detection module."""

import pytest
import pandas as pd
import numpy as np
from analytics.anomaly import detect_anomalies_isolation_forest, _classify_severity


@pytest.fixture
def sample_cost_data():
    """Generate sample cost data with an injected anomaly."""
    dates = pd.date_range("2024-01-01", periods=30, freq="D")
    normal_costs = np.random.normal(100, 10, size=30)
    # Inject anomaly on day 15
    normal_costs[14] = 500

    return {
        "results_by_time": [
            {
                "time_period": {"start": str(date.date()), "end": str(date.date())},
                "groups": [
                    {
                        "keys": ["Amazon EC2", "us-east-1"],
                        "metrics": {"UnblendedCost": {"Amount": str(cost)}},
                    }
                ],
            }
            for date, cost in zip(dates, normal_costs)
        ]
    }


def test_isolation_forest_detects_spike(sample_cost_data):
    """Should detect the injected cost spike."""
    anomalies = detect_anomalies_isolation_forest(sample_cost_data, contamination=0.1)
    assert len(anomalies) > 0
    # The biggest anomaly should be around day 15
    max_anomaly = max(anomalies, key=lambda a: a["actual_cost"])
    assert max_anomaly["actual_cost"] > 400


def test_classify_severity():
    assert _classify_severity(150) == "critical"
    assert _classify_severity(75) == "high"
    assert _classify_severity(35) == "medium"
    assert _classify_severity(10) == "low"


def test_no_anomalies_for_stable_data():
    """Stable data should produce few/no anomalies."""
    dates = pd.date_range("2024-01-01", periods=30, freq="D")
    stable_costs = [100.0] * 30  # Perfectly stable

    cost_data = {
        "results_by_time": [
            {
                "time_period": {"start": str(date.date()), "end": str(date.date())},
                "groups": [
                    {
                        "keys": ["Amazon S3", "us-west-2"],
                        "metrics": {"UnblendedCost": {"Amount": str(cost)}},
                    }
                ],
            }
            for date, cost in zip(dates, stable_costs)
        ]
    }

    anomalies = detect_anomalies_isolation_forest(cost_data)
    assert len(anomalies) == 0
