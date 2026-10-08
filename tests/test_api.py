"""Tests for the FastAPI application."""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient

from api.main import app
from api.models import get_db


def _override_db():
    """Mock DB session for testing without PostgreSQL."""
    mock_session = MagicMock()
    mock_session.add = MagicMock()
    mock_session.commit = MagicMock()
    mock_session.query.return_value.filter_by.return_value.first.return_value = None
    mock_session.query.return_value.order_by.return_value.limit.return_value.all.return_value = []
    yield mock_session


app.dependency_overrides[get_db] = _override_db


@pytest.fixture
def client():
    return TestClient(app)


class TestHealthEndpoint:
    def test_health_returns_ok(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["version"] == "1.0.0"


class TestAnalyzeEndpoint:
    @patch("api.main.run_finops_cycle", new_callable=AsyncMock)
    def test_analyze_returns_report(self, mock_run, client, mock_anomalies, mock_recommendations):
        mock_run.return_value = {
            "report": "## Test Report",
            "anomalies": mock_anomalies,
            "recommendations": mock_recommendations,
            "messages": [],
        }

        response = client.post("/analyze", json={"query": "test scan"})
        assert response.status_code == 200
        data = response.json()
        assert data["anomalies_count"] == 2
        assert data["recommendations_count"] == 2
        assert data["total_potential_savings"] == 137.0

    def test_analyze_rejects_empty_query(self, client):
        response = client.post("/analyze", json={"query": ""})
        assert response.status_code == 422


class TestAskEndpoint:
    @patch("api.main.run_finops_cycle", new_callable=AsyncMock)
    def test_ask_returns_answer(self, mock_run, client):
        mock_run.return_value = {
            "report": "Your EC2 costs increased 40%.",
            "messages": [],
        }

        response = client.post("/ask", json={"query": "Why did my bill spike?"})
        assert response.status_code == 200
        data = response.json()
        assert "EC2" in data["answer"]
        assert "follow_up_suggestions" in data


class TestApproveEndpoint:
    def test_approve_not_found(self, client):
        response = client.post(
            "/approve",
            json={"recommendation_id": "nonexistent", "approved": True},
        )
        assert response.status_code == 404


class TestForecastEndpoint:
    @patch("cloud.aws.cost_explorer.fetch_cost_and_usage", new_callable=AsyncMock)
    def test_forecast_insufficient_data(self, mock_fetch, client):
        mock_fetch.return_value = {"results_by_time": []}

        response = client.get("/forecast", params={"horizon_days": 30})
        assert response.status_code == 400
