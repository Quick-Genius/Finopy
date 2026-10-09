"""FastAPI application — exposes agent functionality via REST API."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, BackgroundTasks, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from api.models import (
    init_db,
    get_db,
    RecommendationRecord,
    AgentRun,
    AnomalyRecord,
)
from api.schemas import (
    QueryRequest,
    ApprovalRequest,
    AnalysisConfig,
    AnalysisResponse,
    AnomalyResponse,
    RecommendationResponse,
    ForecastResponse,
    HealthResponse,
    ConversationResponse,
)
from api.demo_data import (
    generate_demo_analysis,
    generate_demo_answer,
    generate_demo_forecast,
    generate_demo_recommendations,
    generate_demo_history,
)
from config import settings


def _is_demo_mode() -> bool:
    """Check if running without real cloud credentials (demo mode)."""
    return not settings.aws_access_key_id


async def _run_finops_cycle(user_query: str):
    """Lazy import so demo mode boots without agent deps (langgraph, etc.)."""
    from agents.supervisor import run_finops_cycle

    return await run_finops_cycle(user_query=user_query)


# Demo-mode approval memory: demo recommendations use stable IDs (see
# api/demo_data.py), so approvals can be tracked without a database row.
_DEMO_DECIDED: dict[str, bool] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize DB on startup."""
    init_db()
    yield


app = FastAPI(
    title="Agentic FinOps Optimizer",
    description="AI-powered cloud cost optimization with multi-agent orchestration",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(
        status="ok",
        version="1.0.0",
        agents_ready=True,
        cloud_connected=settings.aws_access_key_id is not None,
        timestamp=datetime.utcnow(),
    )


@app.post("/analyze", response_model=AnalysisResponse)
async def analyze_costs(
    request: QueryRequest,
    db: Session = Depends(get_db),
):
    """Trigger a full FinOps analysis cycle and persist results."""
    if _is_demo_mode():
        data = generate_demo_analysis(request.query)
        return AnalysisResponse(
            report=data["report"],
            anomalies_count=data["anomalies_count"],
            recommendations_count=data["recommendations_count"],
            total_potential_savings=data["total_potential_savings"],
            anomalies=[AnomalyResponse(**a) for a in data["anomalies"]],
            recommendations=[
                RecommendationResponse(**{k: v for k, v in r.items() if k != "terraform_plan"})
                for r in data["recommendations"]
            ],
        )

    result = await _run_finops_cycle(user_query=request.query)

    anomalies = result.get("anomalies", [])
    recommendations = result.get("recommendations", [])
    total_savings = sum(r["estimated_savings_monthly"] for r in recommendations)

    # Persist agent run
    run = AgentRun(
        trigger="user_query",
        user_query=request.query,
        anomalies_found=len(anomalies),
        recommendations_generated=len(recommendations),
        total_savings_identified=total_savings,
        report=result.get("report"),
        status="completed",
        completed_at=datetime.utcnow(),
    )
    db.add(run)

    # Persist anomalies
    for a in anomalies:
        db.add(AnomalyRecord(
            service=a["service"],
            region=a["region"],
            expected_cost=a["expected_cost"],
            actual_cost=a["actual_cost"],
            deviation_pct=a["deviation_pct"],
            severity=a["severity"],
        ))

    # Persist recommendations
    for r in recommendations:
        db.add(RecommendationRecord(
            id=r["id"],
            title=r["title"],
            description=r["description"],
            estimated_savings_monthly=r["estimated_savings_monthly"],
            risk_level=r["risk_level"],
            action_type=r["action_type"],
            resource_ids=r["resource_ids"],
            terraform_plan=r.get("terraform_plan"),
        ))

    db.commit()

    return AnalysisResponse(
        report=result.get("report"),
        anomalies_count=len(anomalies),
        recommendations_count=len(recommendations),
        total_potential_savings=total_savings,
        anomalies=[AnomalyResponse(**a) for a in anomalies],
        recommendations=[
            RecommendationResponse(
                id=r["id"],
                title=r["title"],
                description=r["description"],
                estimated_savings_monthly=r["estimated_savings_monthly"],
                risk_level=r["risk_level"],
                action_type=r["action_type"],
                resource_ids=r["resource_ids"],
                approved=r.get("approved"),
            )
            for r in recommendations
        ],
    )


@app.post("/ask", response_model=ConversationResponse)
async def ask_question(request: QueryRequest):
    """Ask a natural-language question about cloud costs (conversational)."""
    if _is_demo_mode():
        data = generate_demo_answer(request.query)
        return ConversationResponse(**data)

    result = await _run_finops_cycle(user_query=request.query)
    return ConversationResponse(
        answer=result.get("report", "Unable to generate answer."),
        sources=["AWS Cost Explorer", "CloudWatch Metrics"],
        follow_up_suggestions=[
            "Which service grew the most this month?",
            "Show me idle EC2 instances",
            "Forecast next month's spend",
        ],
    )


@app.post("/approve")
async def approve_action(request: ApprovalRequest, db: Session = Depends(get_db)):
    """Approve or reject a recommendation and optionally trigger execution."""
    if _is_demo_mode():
        # No DB rows in demo mode — track the decision in memory so the
        # pending queue actually clears.
        known_ids = {r["id"] for r in generate_demo_recommendations()}
        if request.recommendation_id not in known_ids:
            raise HTTPException(status_code=404, detail="Recommendation not found")
        _DEMO_DECIDED[request.recommendation_id] = request.approved
        return {
            "recommendation_id": request.recommendation_id,
            "approved": request.approved,
            "status": "queued_for_execution" if request.approved else "rejected",
        }

    rec = db.query(RecommendationRecord).filter_by(id=request.recommendation_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Recommendation not found")

    rec.approved = request.approved
    rec.approved_by = "api_user"
    rec.approved_at = datetime.utcnow()
    db.commit()

    return {
        "recommendation_id": request.recommendation_id,
        "approved": request.approved,
        "status": "queued_for_execution" if request.approved else "rejected",
    }


@app.get("/recommendations", response_model=List[RecommendationResponse])
async def list_recommendations(
    pending_only: bool = False,
    db: Session = Depends(get_db),
):
    """List all recommendations, optionally filtered to pending approval."""
    if _is_demo_mode():
        recs = generate_demo_recommendations()
        if pending_only:
            recs = [r for r in recs if r["id"] not in _DEMO_DECIDED]
        else:
            recs = [
                {**r, "approved": _DEMO_DECIDED.get(r["id"])}
                for r in recs
            ]
        return [
            RecommendationResponse(**{k: v for k, v in r.items() if k != "terraform_plan"})
            for r in recs
        ]

    query = db.query(RecommendationRecord)
    if pending_only:
        query = query.filter(RecommendationRecord.approved.is_(None))
    recs = query.order_by(RecommendationRecord.created_at.desc()).limit(50).all()

    return [
        RecommendationResponse(
            id=r.id,
            title=r.title,
            description=r.description or "",
            estimated_savings_monthly=r.estimated_savings_monthly,
            risk_level=r.risk_level,
            action_type=r.action_type,
            resource_ids=r.resource_ids or [],
            approved=r.approved,
        )
        for r in recs
    ]


@app.get("/forecast", response_model=ForecastResponse)
async def forecast_spend(horizon_days: int = 30):
    """Forecast future cloud spend."""
    if _is_demo_mode():
        data = generate_demo_forecast(horizon_days)
        return ForecastResponse(**data)

    import pandas as pd
    from cloud.aws.cost_explorer import fetch_cost_and_usage
    from datetime import date, timedelta

    end = date.today()
    start = end - timedelta(days=90)
    raw = await fetch_cost_and_usage(start.isoformat(), end.isoformat(), granularity="DAILY")

    # Aggregate to daily totals
    rows = []
    for period in raw.get("results_by_time", []):
        d = period["time_period"]["start"]
        total = sum(
            float(g["metrics"].get("UnblendedCost", {}).get("Amount", 0))
            for g in period.get("groups", [])
        )
        rows.append({"date": d, "cost": total})

    df = pd.DataFrame(rows)
    if df.empty:
        raise HTTPException(status_code=400, detail="Insufficient cost data for forecasting")

    result = forecast_monthly_spend(df, horizon_days=horizon_days)

    return ForecastResponse(
        projected_total=result["projected_total"],
        confidence_interval=result["confidence_interval"],
        daily_average=result["daily_average"],
        horizon_days=result["horizon_days"],
    )


@app.get("/history/runs")
async def agent_run_history(limit: int = 20, db: Session = Depends(get_db)):
    """Get history of agent runs."""
    if _is_demo_mode():
        return generate_demo_history()

    runs = db.query(AgentRun).order_by(AgentRun.started_at.desc()).limit(limit).all()
    return [
        {
            "id": r.id,
            "started_at": r.started_at,
            "status": r.status,
            "anomalies_found": r.anomalies_found,
            "recommendations_generated": r.recommendations_generated,
            "total_savings_identified": r.total_savings_identified,
            "trigger": r.trigger,
        }
        for r in runs
    ]


@app.get("/integrations")
async def list_integrations():
    """List integration status for cloud and communication platforms.

    Status is derived from configured credentials: `connected` means the
    backend has what it needs, `not_configured` means set the listed env
    vars, `coming_soon` means no backend support yet.
    """
    return [
        {
            "id": "aws",
            "name": "AWS",
            "category": "cloud",
            "description": "Cost Explorer, CloudWatch and EC2 rightsizing.",
            "status": "connected" if settings.aws_access_key_id else "not_configured",
            "capabilities": ["Billing ingest", "Utilization", "Rightsizing"],
            "setup_hint": "FINOPS_AWS_ACCESS_KEY_ID / FINOPS_AWS_SECRET_ACCESS_KEY",
        },
        {
            "id": "gcp",
            "name": "Google Cloud",
            "category": "cloud",
            "description": "BigQuery billing export for GCP spend.",
            "status": "connected" if settings.gcp_project_id else "not_configured",
            "capabilities": ["Billing export"],
            "setup_hint": "FINOPS_GCP_PROJECT_ID / FINOPS_GCP_BILLING_ACCOUNT_ID",
        },
        {
            "id": "azure",
            "name": "Azure",
            "category": "cloud",
            "description": "Cost Management + Advisor ingestion.",
            "status": "coming_soon",
            "capabilities": [],
            "setup_hint": "FINOPS_AZURE_SUBSCRIPTION_ID (upcoming)",
        },
        {
            "id": "slack",
            "name": "Slack",
            "category": "communication",
            "description": "Anomaly alerts and approval requests.",
            "status": "connected" if settings.slack_webhook_url else "not_configured",
            "capabilities": ["Alerts", "Approval gates"],
            "setup_hint": "FINOPS_SLACK_WEBHOOK_URL",
        },
        {
            "id": "email",
            "name": "Email",
            "category": "communication",
            "description": "Anomaly alerts and weekly savings reports over SMTP.",
            "status": "connected" if settings.smtp_host else "not_configured",
            "capabilities": ["Alerts", "Weekly reports"],
            "setup_hint": "FINOPS_SMTP_HOST / FINOPS_SMTP_USER",
        },
        {
            "id": "jira",
            "name": "Jira",
            "category": "communication",
            "description": "File savings actions as tracked tickets.",
            "status": "coming_soon",
            "capabilities": [],
            "setup_hint": "FINOPS_JIRA_URL (upcoming)",
        },
        {
            "id": "telegram",
            "name": "Telegram",
            "category": "communication",
            "description": "Approve or reject actions from chat.",
            "status": "coming_soon",
            "capabilities": [],
            "setup_hint": "FINOPS_TELEGRAM_BOT_TOKEN (upcoming)",
        },
    ]
