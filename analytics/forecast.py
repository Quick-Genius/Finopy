"""Cost forecasting module — predicts future cloud spend."""

from __future__ import annotations

import pandas as pd
import numpy as np
from prophet import Prophet
from config import settings


def forecast_monthly_spend(cost_history: pd.DataFrame, horizon_days: int | None = None) -> dict:
    """Forecast future cloud spend using Prophet.

    Args:
        cost_history: DataFrame with columns ['date', 'cost'] (daily aggregated).
        horizon_days: Days to forecast ahead. Defaults to config setting.

    Returns:
        Dictionary with forecast DataFrame and summary stats.
    """
    if horizon_days is None:
        horizon_days = settings.forecast_horizon_days

    df = cost_history[["date", "cost"]].rename(columns={"date": "ds", "cost": "y"})

    model = Prophet(
        interval_width=0.90,
        yearly_seasonality=True,
        weekly_seasonality=True,
        daily_seasonality=False,
    )
    model.fit(df)

    future = model.make_future_dataframe(periods=horizon_days)
    forecast = model.predict(future)

    # Extract only future predictions
    future_forecast = forecast[forecast["ds"] > df["ds"].max()]

    projected_total = future_forecast["yhat"].sum()
    lower_bound = future_forecast["yhat_lower"].sum()
    upper_bound = future_forecast["yhat_upper"].sum()

    return {
        "forecast_df": future_forecast[["ds", "yhat", "yhat_lower", "yhat_upper"]],
        "projected_total": round(projected_total, 2),
        "confidence_interval": (round(lower_bound, 2), round(upper_bound, 2)),
        "daily_average": round(projected_total / horizon_days, 2),
        "horizon_days": horizon_days,
    }


def forecast_by_service(cost_data: pd.DataFrame, horizon_days: int = 30) -> dict[str, dict]:
    """Forecast spend per service."""
    results = {}
    for service, group in cost_data.groupby("service"):
        if len(group) < 14:
            continue
        daily = group.groupby("date")["cost"].sum().reset_index()
        results[service] = forecast_monthly_spend(daily, horizon_days)
    return results
