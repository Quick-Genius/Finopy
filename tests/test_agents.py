"""Tests for the agent supervisor graph construction."""

import pytest
from unittest.mock import patch, AsyncMock
from agents.state import FinOpsState


class TestSupervisorGraph:
    def test_graph_compiles(self):
        """The LangGraph should compile without errors."""
        from agents.supervisor import build_finops_graph

        graph = build_finops_graph()
        assert graph is not None

    def test_routing_with_no_cost_data(self):
        """If observe returns no data, graph should end early."""
        from agents.supervisor import should_diagnose

        state: FinOpsState = {
            "messages": [],
            "cost_data": None,
            "anomalies": [],
            "recommendations": [],
            "approved_actions": [],
            "execution_results": [],
            "current_phase": "observe",
            "user_query": None,
            "report": None,
        }
        assert should_diagnose(state) == "__end__"

    def test_routing_with_cost_data(self):
        """If cost data exists, should route to diagnose."""
        from agents.supervisor import should_diagnose

        state: FinOpsState = {
            "messages": [],
            "cost_data": {"results_by_time": [{"groups": []}]},
            "anomalies": [],
            "recommendations": [],
            "approved_actions": [],
            "execution_results": [],
            "current_phase": "observe",
            "user_query": None,
            "report": None,
        }
        assert should_diagnose(state) == "diagnose"

    def test_routing_no_anomalies_skips_recommend(self):
        """If no anomalies, should skip to report."""
        from agents.supervisor import should_recommend

        state: FinOpsState = {
            "messages": [],
            "cost_data": {},
            "anomalies": [],
            "recommendations": [],
            "approved_actions": [],
            "execution_results": [],
            "current_phase": "diagnose",
            "user_query": None,
            "report": None,
        }
        assert should_recommend(state) == "report"

    def test_report_generation(self, mock_anomalies, mock_recommendations):
        """Report generation should produce markdown with savings."""
        from agents.supervisor import generate_report

        state: FinOpsState = {
            "messages": [],
            "cost_data": {},
            "anomalies": mock_anomalies,
            "recommendations": mock_recommendations,
            "approved_actions": [],
            "execution_results": [],
            "current_phase": "recommend",
            "user_query": None,
            "report": None,
        }

        result = generate_report(state)
        assert "FinOps Agent Report" in result["report"]
        assert "$137.00" in result["report"]
        assert result["current_phase"] == "complete"
