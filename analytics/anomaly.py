"""Anomaly detection on cloud cost time-series."""

from __future__ import annotations

import pandas as pd
import numpy as np
from sklearn.ensemble import IsolationForest
from typing import Any

from agents.state import CostAnomaly


def detect_anomalies_isolation_forest(
    cost_data: dict, contamination: float = 0.05
) -> list[CostAnomaly]:
    """Detect cost anomalies using Isolation Forest."""
    df = _cost_data_to_dataframe(cost_data)
    if df.empty:
        return []

    anomalies = []
    for (service, region), group in df.groupby(["service", "region"]):
        if len(group) < 7:  # Need at least a week of data
            continue

        values = group["cost"].values.reshape(-1, 1)
        model = IsolationForest(contamination=contamination, random_state=42)
        predictions = model.fit_predict(values)

        for idx, pred in enumerate(predictions):
            if pred == -1:  # Anomaly
                row = group.iloc[idx]
                expected = group["cost"].median()
                deviation = ((row["cost"] - expected) / expected) * 100

                anomalies.append(CostAnomaly(
                    service=service,
                    region=region,
                    expected_cost=round(expected, 2),
                    actual_cost=round(row["cost"], 2),
                    deviation_pct=round(deviation, 1),
                    timestamp=str(row["date"]),
                    severity=_classify_severity(deviation),
                ))

    return anomalies


def detect_anomalies_prophet(cost_data: dict) -> list[CostAnomaly]:
    """Detect cost anomalies using Prophet's uncertainty intervals."""
    from prophet import Prophet

    df = _cost_data_to_dataframe(cost_data)
    if df.empty:
        return []

    anomalies = []
    for (service, region), group in df.groupby(["service", "region"]):
        if len(group) < 14:  # Prophet needs more data
            continue

        prophet_df = group[["date", "cost"]].rename(columns={"date": "ds", "cost": "y"})

        model = Prophet(
            interval_width=0.95,
            daily_seasonality=False,
            weekly_seasonality=True,
            changepoint_prior_scale=0.05,
        )
        model.fit(prophet_df)

        forecast = model.predict(prophet_df)

        for idx, row in forecast.iterrows():
            actual = prophet_df.iloc[idx]["y"]
            if actual > row["yhat_upper"]:
                deviation = ((actual - row["yhat"]) / row["yhat"]) * 100
                anomalies.append(CostAnomaly(
                    service=service,
                    region=region,
                    expected_cost=round(row["yhat"], 2),
                    actual_cost=round(actual, 2),
                    deviation_pct=round(deviation, 1),
                    timestamp=str(row["ds"].date()),
                    severity=_classify_severity(deviation),
                ))

    return anomalies


def _cost_data_to_dataframe(cost_data: dict) -> pd.DataFrame:
    """Convert AWS Cost Explorer response format to a DataFrame."""
    rows = []
    for period in cost_data.get("results_by_time", []):
        date = period.get("time_period", {}).get("start", "")
        for group in period.get("groups", []):
            keys = group.get("keys", [])
            cost = float(group.get("metrics", {}).get("UnblendedCost", {}).get("Amount", 0))
            rows.append({
                "date": pd.to_datetime(date),
                "service": keys[0] if len(keys) > 0 else "Unknown",
                "region": keys[1] if len(keys) > 1 else "global",
                "cost": cost,
            })
    return pd.DataFrame(rows)


def _classify_severity(deviation_pct: float) -> str:
    """Classify anomaly severity based on deviation percentage."""
    abs_dev = abs(deviation_pct)
    if abs_dev >= 100:
        return "critical"
    elif abs_dev >= 50:
        return "high"
    elif abs_dev >= 25:
        return "medium"
    return "low"
