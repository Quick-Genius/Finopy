"""Application configuration via environment variables."""

from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """FinOps Optimizer settings."""

    # AWS
    aws_region: str = "us-east-1"
    aws_access_key_id: Optional[str] = None
    aws_secret_access_key: Optional[str] = None

    # GCP (optional)
    gcp_project_id: Optional[str] = None
    gcp_billing_account_id: Optional[str] = None

    # LLM
    llm_provider: str = "bedrock"  # "bedrock" | "openai" | "azure"
    bedrock_model_id: str = "anthropic.claude-sonnet-4-20250514"
    openai_api_key: Optional[str] = None

    # Database
    database_url: str = "sqlite:///finops.db"
    redis_url: str = "redis://localhost:6379/0"

    # Notifications
    slack_webhook_url: Optional[str] = None
    slack_approval_channel: str = "#finops-approvals"

    # Agent Config
    approval_required: bool = True
    max_auto_savings_threshold: float = 100.0  # Auto-approve below this $
    anomaly_sensitivity: float = 0.95
    forecast_horizon_days: int = 30

    # Observability
    langsmith_api_key: Optional[str] = None
    langsmith_project: str = "agentic-finops"

    model_config = {"env_file": ".env", "env_prefix": "FINOPS_"}


settings = Settings()
