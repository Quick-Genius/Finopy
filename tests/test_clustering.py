"""Tests for workload clustering module."""

import pytest
import pandas as pd
import numpy as np


class TestClusterWorkloads:
    """Test cluster_workloads function."""

    def test_clusters_with_distinct_patterns(self):
        """Should separate workloads with different cost behaviors."""
        from analytics.clustering import cluster_workloads

        dates = pd.date_range("2024-01-01", periods=30, freq="D")
        rows = []

        # Pattern A: stable high cost (good for reservations)
        for d in dates:
            rows.append({"date": d, "service": "ProdDB", "region": "us-east-1", "cost": 200.0})

        # Pattern B: weekday-only traffic (schedulable)
        for d in dates:
            cost = 80.0 if d.dayofweek < 5 else 5.0
            rows.append({"date": d, "service": "DevServer", "region": "us-east-1", "cost": cost})

        # Pattern C: bursty (spot candidates)
        for d in dates:
            cost = np.random.choice([10, 10, 10, 300], p=[0.7, 0.1, 0.1, 0.1])
            rows.append({"date": d, "service": "BatchJob", "region": "us-west-2", "cost": cost})

        df = pd.DataFrame(rows)
        result = cluster_workloads(df)

        assert result["n_clusters"] >= 2
        assert result["silhouette_score"] > 0
        assert len(result["profiles"]) == result["n_clusters"]

    def test_insufficient_data(self):
        """Should gracefully handle insufficient data."""
        from analytics.clustering import cluster_workloads

        df = pd.DataFrame({"date": ["2024-01-01"], "service": ["X"], "region": ["us"], "cost": [1]})
        result = cluster_workloads(df)

        assert "error" in result

    def test_optimization_hints(self):
        """Each cluster profile should have an optimization hint."""
        from analytics.clustering import cluster_workloads

        dates = pd.date_range("2024-01-01", periods=30, freq="D")
        rows = []
        for svc in ["A", "B", "C", "D"]:
            for d in dates:
                rows.append({"date": d, "service": svc, "region": "us-east-1", "cost": np.random.uniform(10, 100)})

        df = pd.DataFrame(rows)
        result = cluster_workloads(df)

        for profile in result.get("profiles", []):
            assert "optimization_hint" in profile
            assert len(profile["optimization_hint"]) > 0
