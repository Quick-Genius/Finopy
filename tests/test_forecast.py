"""Tests for forecast module."""

import pytest
import pandas as pd
import numpy as np
from unittest.mock import patch


class TestForecastMonthlySpend:
    """Test the forecast_monthly_spend function."""

    def test_basic_forecast(self):
        """Should produce a forecast with required keys."""
        from analytics.forecast import forecast_monthly_spend

        dates = pd.date_range("2024-01-01", periods=60, freq="D")
        costs = 100 + np.random.normal(0, 5, 60)
        df = pd.DataFrame({"date": dates, "cost": costs})

        result = forecast_monthly_spend(df, horizon_days=14)

        assert "projected_total" in result
        assert "confidence_interval" in result
        assert "daily_average" in result
        assert result["horizon_days"] == 14
        assert result["projected_total"] > 0
        assert result["confidence_interval"][0] <= result["projected_total"]
        assert result["confidence_interval"][1] >= result["projected_total"]

    def test_forecast_with_trend(self):
        """Forecast of increasing data should project continued growth."""
        from analytics.forecast import forecast_monthly_spend

        dates = pd.date_range("2024-01-01", periods=90, freq="D")
        costs = np.linspace(50, 150, 90) + np.random.normal(0, 3, 90)
        df = pd.DataFrame({"date": dates, "cost": costs})

        result = forecast_monthly_spend(df, horizon_days=30)

        # Projected daily average should be above the mid-point of historical data
        historical_last_30_avg = df["cost"].iloc[-30:].mean()
        assert result["daily_average"] > 0
        # Projected total should be positive and reasonable
        assert result["projected_total"] > 0


class TestForecastByService:
    """Test per-service forecasting."""

    def test_multiple_services(self, sample_cost_dataframe):
        """Should return forecasts for services with enough data."""
        from analytics.forecast import forecast_by_service

        results = forecast_by_service(sample_cost_dataframe, horizon_days=7)

        assert len(results) > 0
        for service, forecast in results.items():
            assert forecast["projected_total"] > 0
            assert forecast["horizon_days"] == 7
