"""Scheduled runner — triggers FinOps agent cycles on a cron schedule."""

import asyncio
import structlog
from datetime import datetime

from agents.supervisor import run_finops_cycle
from notifications.slack import send_notification
from notifications.email import send_anomaly_alert, send_savings_report
from config import settings

logger = structlog.get_logger()


async def scheduled_analysis():
    """Run a full analysis cycle — intended to be triggered by cron/scheduler."""
    logger.info("scheduled_analysis_started", timestamp=datetime.utcnow().isoformat())

    try:
        result = await run_finops_cycle(user_query="Scheduled daily cost analysis")

        anomalies = result.get("anomalies", [])
        recommendations = result.get("recommendations", [])
        total_savings = sum(r["estimated_savings_monthly"] for r in recommendations)

        # Send Slack summary
        summary = (
            f"📊 *Daily FinOps Report*\n"
            f"• Anomalies: {len(anomalies)}\n"
            f"• Recommendations: {len(recommendations)}\n"
            f"• Potential savings: ${total_savings:.2f}/mo"
        )
        await send_notification(summary)

        # Send email alerts for critical anomalies
        critical = [a for a in anomalies if a["severity"] in ("critical", "high")]
        if critical:
            email_recipients = getattr(settings, "alert_email_recipients", [])
            if email_recipients:
                await send_anomaly_alert(critical, email_recipients)

        logger.info(
            "scheduled_analysis_completed",
            anomalies=len(anomalies),
            recommendations=len(recommendations),
            savings=total_savings,
        )

    except Exception as e:
        logger.error("scheduled_analysis_failed", error=str(e))
        await send_notification(f"❌ FinOps scheduled analysis failed: {e}")


async def weekly_report():
    """Generate and send weekly savings summary report."""
    result = await run_finops_cycle(user_query="Weekly comprehensive cost review")
    recommendations = result.get("recommendations", [])
    total_savings = sum(r["estimated_savings_monthly"] for r in recommendations)

    email_recipients = getattr(settings, "report_email_recipients", [])
    if email_recipients:
        await send_savings_report(recommendations, total_savings, email_recipients)

    await send_notification(
        f"📬 Weekly FinOps report sent — ${total_savings:.2f}/mo in savings identified"
    )


if __name__ == "__main__":
    # Direct invocation for testing or cron
    asyncio.run(scheduled_analysis())
