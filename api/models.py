"""Database models for persisting cost data, anomalies, and recommendations."""

from datetime import datetime
from sqlalchemy import (
    Column,
    String,
    Float,
    DateTime,
    Boolean,
    Integer,
    Text,
    JSON,
    Enum as SAEnum,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from config import settings


class Base(DeclarativeBase):
    pass


class CostRecord(Base):
    """Daily cost record per service/region."""

    __tablename__ = "cost_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(DateTime, nullable=False, index=True)
    service = Column(String(256), nullable=False, index=True)
    region = Column(String(64), nullable=False)
    cost = Column(Float, nullable=False)
    usage_quantity = Column(Float)
    provider = Column(String(16), default="aws")  # aws | gcp
    ingested_at = Column(DateTime, default=datetime.utcnow)


class AnomalyRecord(Base):
    """Persisted anomaly detection result."""

    __tablename__ = "anomalies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    detected_at = Column(DateTime, default=datetime.utcnow, index=True)
    service = Column(String(256), nullable=False)
    region = Column(String(64), nullable=False)
    expected_cost = Column(Float, nullable=False)
    actual_cost = Column(Float, nullable=False)
    deviation_pct = Column(Float, nullable=False)
    severity = Column(String(16), nullable=False)
    detection_method = Column(String(32))  # "isolation_forest" | "prophet"
    resolved = Column(Boolean, default=False)


class RecommendationRecord(Base):
    """Persisted optimization recommendation."""

    __tablename__ = "recommendations"

    id = Column(String(64), primary_key=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    title = Column(String(512), nullable=False)
    description = Column(Text)
    estimated_savings_monthly = Column(Float, nullable=False)
    risk_level = Column(String(16), nullable=False)
    action_type = Column(String(32), nullable=False)
    resource_ids = Column(JSON, default=list)
    terraform_plan = Column(Text)
    approved = Column(Boolean, nullable=True)
    approved_by = Column(String(128))
    approved_at = Column(DateTime)
    executed = Column(Boolean, default=False)
    executed_at = Column(DateTime)
    execution_status = Column(String(32))


class AgentRun(Base):
    """Record of each agent orchestration cycle."""

    __tablename__ = "agent_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime)
    trigger = Column(String(32))  # "scheduled" | "user_query" | "alert"
    user_query = Column(Text)
    anomalies_found = Column(Integer, default=0)
    recommendations_generated = Column(Integer, default=0)
    actions_executed = Column(Integer, default=0)
    total_savings_identified = Column(Float, default=0.0)
    report = Column(Text)
    status = Column(String(16), default="running")  # running | completed | failed


# Database setup

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db():
    """Create all tables."""
    Base.metadata.create_all(bind=engine)


def get_db():
    """Dependency for FastAPI — yields a DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
