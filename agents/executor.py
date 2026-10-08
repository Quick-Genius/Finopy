"""Executor Agent — applies approved optimizations via IaC with safety checks."""

from __future__ import annotations

import structlog
from langchain_core.messages import AIMessage

from agents.state import FinOpsState
from config import settings
from iac.actions import apply_terraform_plan
from notifications.slack import request_approval

logger = structlog.get_logger()


async def execute_approved_actions(state: FinOpsState) -> FinOpsState:
    """Execute approved recommendations. Requires human approval for high-risk actions."""
    recommendations = state.get("recommendations", [])
    approved_ids = set(state.get("approved_actions", []))
    execution_results = []

    for rec in recommendations:
        if rec["id"] not in approved_ids:
            continue

        # Safety gate: high-risk actions require explicit Slack approval
        if rec["risk_level"] == "high" and settings.approval_required:
            approved = await request_approval(
                channel=settings.slack_approval_channel,
                recommendation=rec,
            )
            if not approved:
                execution_results.append({
                    "action": rec["title"],
                    "status": "rejected",
                    "reason": "Human rejected in approval gate",
                })
                continue

        # Execute the terraform plan
        if rec.get("terraform_plan"):
            try:
                result = await apply_terraform_plan(rec["terraform_plan"])
                execution_results.append({
                    "action": rec["title"],
                    "status": "success",
                    "details": result,
                    "savings": rec["estimated_savings_monthly"],
                })
                logger.info(
                    "executed_optimization",
                    action=rec["title"],
                    savings=rec["estimated_savings_monthly"],
                )
            except Exception as e:
                execution_results.append({
                    "action": rec["title"],
                    "status": "failed",
                    "error": str(e),
                })
                logger.error("execution_failed", action=rec["title"], error=str(e))
        else:
            execution_results.append({
                "action": rec["title"],
                "status": "skipped",
                "reason": "No terraform plan available",
            })

    messages = state.get("messages", [])
    successful = [r for r in execution_results if r["status"] == "success"]
    messages.append(
        AIMessage(content=f"Executed {len(successful)}/{len(execution_results)} approved actions.")
    )

    return {
        **state,
        "execution_results": execution_results,
        "messages": messages,
        "current_phase": "execute",
    }
