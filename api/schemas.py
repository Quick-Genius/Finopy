"""Pydantic schemas for API request/response models."""

from typing import Optional, List, Dict, Tuple

from pydantic import BaseModel, Field
from datetime import datetime
from enum import Enum


class RiskLevel(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class ActionType(str, Enum):
    rightsize = "rightsize"
    terminate = "terminate"
    reserve = "reserve"
    schedule = "schedule"
    migrate = "migrate"


class Severity(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


# --- Requests ---


class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)


class ApprovalRequest(BaseModel):
    recommendation_id: str
    approved: bool
    reason: Optional[str] = None


class AnalysisConfig(BaseModel):
    include_gcp: bool = False
    anomaly_sensitivity: float = Field(default=0.95, ge=0.5, le=0.99)
    forecast_days: int = Field(default=30, ge=7, le=365)


# --- Responses ---


class AnomalyResponse(BaseModel):
    service: str
    region: str
    expected_cost: float
    actual_cost: float
    deviation_pct: float
    timestamp: str
    severity: Severity


class RecommendationResponse(BaseModel):
    id: str
    title: str
    description: str
    estimated_savings_monthly: float
    risk_level: RiskLevel
    action_type: ActionType
    resource_ids: List[str]
    approved: Optional[bool] = None


class AnalysisResponse(BaseModel):
    report: Optional[str]
    anomalies_count: int
    recommendations_count: int
    total_potential_savings: float
    anomalies: List[AnomalyResponse]
    recommendations: List[RecommendationResponse]


class ForecastResponse(BaseModel):
    projected_total: float
    confidence_interval: Tuple[float, float]
    daily_average: float
    horizon_days: int
    by_service: Optional[Dict[str, float]] = None


class HealthResponse(BaseModel):
    status: str
    version: str
    agents_ready: bool
    cloud_connected: bool
    timestamp: datetime


class ConversationResponse(BaseModel):
    answer: str
    sources: List[str] = []
    follow_up_suggestions: List[str] = []
