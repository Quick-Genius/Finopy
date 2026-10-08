"""Initial schema — cost records, anomalies, recommendations, agent runs.

Revision ID: 001
Create Date: 2026-06-25
"""

from alembic import op
import sqlalchemy as sa

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cost_records",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("date", sa.DateTime, nullable=False, index=True),
        sa.Column("service", sa.String(256), nullable=False, index=True),
        sa.Column("region", sa.String(64), nullable=False),
        sa.Column("cost", sa.Float, nullable=False),
        sa.Column("usage_quantity", sa.Float),
        sa.Column("provider", sa.String(16), server_default="aws"),
        sa.Column("ingested_at", sa.DateTime, server_default=sa.func.now()),
    )

    op.create_table(
        "anomalies",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("detected_at", sa.DateTime, server_default=sa.func.now(), index=True),
        sa.Column("service", sa.String(256), nullable=False),
        sa.Column("region", sa.String(64), nullable=False),
        sa.Column("expected_cost", sa.Float, nullable=False),
        sa.Column("actual_cost", sa.Float, nullable=False),
        sa.Column("deviation_pct", sa.Float, nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("detection_method", sa.String(32)),
        sa.Column("resolved", sa.Boolean, server_default="false"),
    )

    op.create_table(
        "recommendations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), index=True),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("estimated_savings_monthly", sa.Float, nullable=False),
        sa.Column("risk_level", sa.String(16), nullable=False),
        sa.Column("action_type", sa.String(32), nullable=False),
        sa.Column("resource_ids", sa.JSON, server_default="[]"),
        sa.Column("terraform_plan", sa.Text),
        sa.Column("approved", sa.Boolean),
        sa.Column("approved_by", sa.String(128)),
        sa.Column("approved_at", sa.DateTime),
        sa.Column("executed", sa.Boolean, server_default="false"),
        sa.Column("executed_at", sa.DateTime),
        sa.Column("execution_status", sa.String(32)),
    )

    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("started_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime),
        sa.Column("trigger", sa.String(32)),
        sa.Column("user_query", sa.Text),
        sa.Column("anomalies_found", sa.Integer, server_default="0"),
        sa.Column("recommendations_generated", sa.Integer, server_default="0"),
        sa.Column("actions_executed", sa.Integer, server_default="0"),
        sa.Column("total_savings_identified", sa.Float, server_default="0"),
        sa.Column("report", sa.Text),
        sa.Column("status", sa.String(16), server_default="running"),
    )


def downgrade() -> None:
    op.drop_table("agent_runs")
    op.drop_table("recommendations")
    op.drop_table("anomalies")
    op.drop_table("cost_records")
