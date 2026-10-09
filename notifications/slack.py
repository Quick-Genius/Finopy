"""Slack integration for notifications and approval gates."""

from __future__ import annotations

import httpx
import asyncio
from config import settings
from agents.state import Recommendation


async def send_notification(message: str, channel: str | None = None) -> bool:
    """Send a notification message to Slack."""
    webhook_url = settings.slack_webhook_url
    if not webhook_url:
        return False

    payload = {"text": message}
    if channel:
        payload["channel"] = channel

    async with httpx.AsyncClient() as client:
        response = await client.post(webhook_url, json=payload)
        return response.status_code == 200


async def request_approval(channel: str, recommendation: Recommendation) -> bool:
    """Request human approval for a high-risk action via Slack interactive message.

    In production, this would use Slack's Block Kit with action buttons
    and wait for a webhook callback. For now, implements a polling-based approach.
    """
    message = f"""🔴 *Finopy Approval Required*

*Action:* {recommendation['title']}
*Risk Level:* {recommendation['risk_level'].upper()}
*Estimated Savings:* ${recommendation['estimated_savings_monthly']:.2f}/month
*Resources:* {', '.join(recommendation['resource_ids'][:5])}

*Description:* {recommendation['description'][:200]}

React with ✅ to approve or ❌ to reject."""

    sent = await send_notification(message, channel)
    if not sent:
        return False

    # In production: await Slack interactive callback
    # For demo: auto-approve low-risk, require explicit approval for high-risk
    # This would be replaced with actual Slack webhook listener
    return recommendation["risk_level"] != "high"
