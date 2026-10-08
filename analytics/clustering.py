"""Workload clustering by cost profile."""

from __future__ import annotations

import pandas as pd
import numpy as np
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score


def cluster_workloads(cost_data: pd.DataFrame, max_clusters: int = 8) -> dict:
    """Cluster workloads/services by their cost behavior patterns.

    Features extracted per workload:
    - Mean daily cost
    - Cost variance (volatility)
    - Trend slope (growing vs shrinking)
    - Weekend/weekday ratio (scheduled workloads)
    - Peak-to-trough ratio (burstiness)

    Returns:
        Dict with cluster assignments, profiles, and optimization hints.
    """
    features = _extract_features(cost_data)
    if features.empty or len(features) < 3:
        return {"clusters": [], "error": "Insufficient data for clustering"}

    scaler = StandardScaler()
    X = scaler.fit_transform(features.drop(columns=["workload"]))

    # Find optimal k using silhouette score
    best_k, best_score = 2, -1
    for k in range(2, min(max_clusters + 1, len(features))):
        km = KMeans(n_clusters=k, random_state=42, n_init=10)
        labels = km.fit_predict(X)
        score = silhouette_score(X, labels)
        if score > best_score:
            best_k, best_score = k, score

    # Final clustering
    km = KMeans(n_clusters=best_k, random_state=42, n_init=10)
    features["cluster"] = km.fit_predict(X)

    # Profile each cluster
    profiles = []
    for cluster_id in range(best_k):
        cluster_data = features[features["cluster"] == cluster_id]
        profiles.append({
            "cluster_id": cluster_id,
            "workloads": cluster_data["workload"].tolist(),
            "avg_daily_cost": round(cluster_data["mean_cost"].mean(), 2),
            "volatility": round(cluster_data["cost_variance"].mean(), 2),
            "optimization_hint": _suggest_optimization(cluster_data),
        })

    return {
        "n_clusters": best_k,
        "silhouette_score": round(best_score, 3),
        "profiles": profiles,
        "assignments": features[["workload", "cluster"]].to_dict("records"),
    }


def _extract_features(cost_data: pd.DataFrame) -> pd.DataFrame:
    """Extract behavioral features per workload."""
    rows = []
    for workload, group in cost_data.groupby("service"):
        daily = group.groupby("date")["cost"].sum().sort_index()
        if len(daily) < 7:
            continue

        # Trend via linear regression slope
        x = np.arange(len(daily))
        slope = np.polyfit(x, daily.values, 1)[0] if len(daily) > 1 else 0

        # Weekend/weekday ratio
        group_with_dow = group.copy()
        group_with_dow["dow"] = pd.to_datetime(group_with_dow["date"]).dt.dayofweek
        weekend = group_with_dow[group_with_dow["dow"] >= 5]["cost"].mean() or 0
        weekday = group_with_dow[group_with_dow["dow"] < 5]["cost"].mean() or 1
        ww_ratio = weekend / weekday if weekday > 0 else 0

        rows.append({
            "workload": workload,
            "mean_cost": daily.mean(),
            "cost_variance": daily.std(),
            "trend_slope": slope,
            "weekend_weekday_ratio": ww_ratio,
            "peak_trough_ratio": daily.max() / daily.min() if daily.min() > 0 else 0,
        })

    return pd.DataFrame(rows)


def _suggest_optimization(cluster_data: pd.DataFrame) -> str:
    """Suggest optimization strategy based on cluster characteristics."""
    avg_variance = cluster_data["cost_variance"].mean()
    avg_ww_ratio = cluster_data["weekend_weekday_ratio"].mean()
    avg_slope = cluster_data["trend_slope"].mean()

    if avg_ww_ratio < 0.3:
        return "Schedule-based: Consider shutting down on weekends"
    elif avg_variance > cluster_data["mean_cost"].mean() * 0.5:
        return "Bursty: Use spot/preemptible instances or autoscaling"
    elif avg_slope > 0:
        return "Growing: Evaluate Reserved Instances or Savings Plans"
    else:
        return "Stable: Good candidate for committed-use discounts"
