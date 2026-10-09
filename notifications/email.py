"""Email notification handler for Finopy alerts and reports."""

from __future__ import annotations

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from config import settings


async def send_email_notification(
    to_addresses: list[str],
    subject: str,
    body_html: str,
    body_text: str | None = None,
) -> bool:
    """Send an email notification.

    Uses SMTP settings from config. Returns True on success.
    """
    smtp_host = getattr(settings, "smtp_host", None)
    smtp_port = getattr(settings, "smtp_port", 587)
    smtp_user = getattr(settings, "smtp_user", None)
    smtp_password = getattr(settings, "smtp_password", None)
    from_address = getattr(settings, "email_from", "finops@example.com")

    if not smtp_host or not to_addresses:
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_address
    msg["To"] = ", ".join(to_addresses)

    if body_text:
        msg.attach(MIMEText(body_text, "plain"))
    msg.attach(MIMEText(body_html, "html"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            if smtp_user and smtp_password:
                server.login(smtp_user, smtp_password)
            server.sendmail(from_address, to_addresses, msg.as_string())
        return True
    except Exception:
        return False


async def send_anomaly_alert(anomalies: list[dict], to_addresses: list[str]) -> bool:
    """Send an email alert for detected anomalies."""
    if not anomalies:
        return False

    total_excess = sum(a["actual_cost"] - a["expected_cost"] for a in anomalies)
    subject = f"🚨 Finopy Alert: {len(anomalies)} cost anomalies detected (${total_excess:.2f} excess)"

    rows = ""
    for a in anomalies[:10]:
        color = {"critical": "#d32f2f", "high": "#f57c00", "medium": "#fbc02d", "low": "#388e3c"}.get(
            a["severity"], "#666"
        )
        rows += f"""<tr>
            <td>{a['service']}</td>
            <td>{a['region']}</td>
            <td style="color:{color};font-weight:bold">{a['severity'].upper()}</td>
            <td>${a['actual_cost']:.2f}</td>
            <td>${a['expected_cost']:.2f}</td>
            <td>{a['deviation_pct']:.1f}%</td>
        </tr>"""

    body_html = f"""<html><body>
    <h2>Finopy Cost Anomaly Alert</h2>
    <p><strong>{len(anomalies)} anomalies</strong> detected with
    <strong>${total_excess:.2f}</strong> total excess spend.</p>
    <table border="1" cellpadding="8" cellspacing="0">
        <tr><th>Service</th><th>Region</th><th>Severity</th>
        <th>Actual</th><th>Expected</th><th>Deviation</th></tr>
        {rows}
    </table>
    <p><a href="http://localhost:8501">Open Dashboard →</a></p>
    </body></html>"""

    return await send_email_notification(to_addresses, subject, body_html)


async def send_savings_report(
    recommendations: list[dict],
    total_savings: float,
    to_addresses: list[str],
) -> bool:
    """Send a weekly savings opportunity report."""
    subject = f"💰 Finopy Weekly: ${total_savings:.2f}/mo savings identified"

    rows = ""
    for r in recommendations[:10]:
        rows += f"""<tr>
            <td>{r['title']}</td>
            <td>{r['risk_level'].upper()}</td>
            <td>${r['estimated_savings_monthly']:.2f}</td>
            <td>{r['action_type']}</td>
        </tr>"""

    body_html = f"""<html><body>
    <h2>Weekly Finopy Savings Report</h2>
    <p>Total potential monthly savings: <strong>${total_savings:.2f}</strong></p>
    <table border="1" cellpadding="8" cellspacing="0">
        <tr><th>Recommendation</th><th>Risk</th><th>Savings/mo</th><th>Action</th></tr>
        {rows}
    </table>
    <p><a href="http://localhost:8501">Review & Approve →</a></p>
    </body></html>"""

    return await send_email_notification(to_addresses, subject, body_html)
