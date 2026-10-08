"""Shared agent state definition for LangGraph."""

from typing import Annotated, Optional, List, TypedDict
from langgraph.graph.message import add_messages


class CostAnomaly(TypedDict):
    """Detected cost anomaly."""

    service: str
    region: str
    expected_cost: float
    actual_cost: float
    deviation_pct: float
    timestamp: str
    severity: str  # "low" | "medium" | "high" | "critical"


class Recommendation(TypedDict):
    """Cost optimization recommendation."""

    id: str
    title: str
    description: str
    estimated_savings_monthly: float
    risk_level: str  # "low" | "medium" | "high"
    action_type: str  # "rightsize" | "terminate" | "reserve" | "schedule" | "migrate"
    resource_ids: List[str]
    terraform_plan: Optional[str]
    approved: Optional[bool]


class FinOpsState(TypedDict):
    """Global state flowing through the agent graph."""

    messages: Annotated[list, add_messages]
    cost_data: Optional[dict]
    anomalies: List[CostAnomaly]
    recommendations: List[Recommendation]
    approved_actions: List[str]
    execution_results: List[dict]
    current_phase: str  # "observe" | "diagnose" | "recommend" | "execute"
    user_query: Optional[str]
    report: Optional[str]
