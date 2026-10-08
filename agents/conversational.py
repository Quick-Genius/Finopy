"""Conversational Q&A agent — handles natural-language questions about cloud costs."""

from __future__ import annotations

from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_aws import ChatBedrock

from config import settings
from cloud.aws.cost_explorer import fetch_cost_and_usage, get_rightsizing_recommendations
from cloud.aws.ec2_advisor import (
    find_idle_instances,
    find_oversized_instances,
    find_unattached_ebs_volumes,
)
from analytics.anomaly import detect_anomalies_isolation_forest

import datetime
import json


SYSTEM_PROMPT = """You are a FinOps AI assistant with deep knowledge of cloud cost optimization.
You have access to real AWS/GCP billing data and resource metrics.

Guidelines:
- Answer questions concisely with specific dollar amounts when possible
- Reference actual services, regions, and resource IDs from the data
- Suggest actionable next steps
- If data is insufficient, say so and recommend what data to collect
- Always ground your answers in the provided context — do not hallucinate figures

Context data will be provided below."""


async def answer_cost_question(query: str) -> dict:
    """Answer a natural-language question about cloud costs.

    This agent gathers relevant context from cloud APIs based on the query,
    then uses an LLM to synthesize a grounded answer.
    """
    # Gather context based on query intent
    context_parts = []

    # Always fetch recent cost data
    end_date = datetime.date.today()
    start_date = end_date - datetime.timedelta(days=30)

    cost_data = await fetch_cost_and_usage(
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
        granularity="DAILY",
        group_by=["SERVICE", "REGION"],
    )
    context_parts.append(f"Last 30 days cost data: {_summarize_cost_data(cost_data)}")

    # Detect if the query is about anomalies/spikes
    spike_keywords = ["spike", "increase", "jump", "why", "anomaly", "unexpected", "surge"]
    if any(kw in query.lower() for kw in spike_keywords):
        anomalies = detect_anomalies_isolation_forest(cost_data)
        if anomalies:
            top_anomalies = anomalies[:5]
            context_parts.append(f"Detected anomalies: {json.dumps(top_anomalies, default=str)}")

    # Detect if asking about specific resources
    resource_keywords = ["idle", "unused", "oversized", "waste", "terminate", "ebs", "volume"]
    if any(kw in query.lower() for kw in resource_keywords):
        idle = await find_idle_instances()
        if idle:
            context_parts.append(f"Idle instances: {json.dumps(idle[:5], default=str)}")

        volumes = await find_unattached_ebs_volumes()
        if volumes:
            context_parts.append(f"Unattached EBS volumes: {json.dumps(volumes[:5], default=str)}")

    # Detect if asking about rightsizing
    rightsize_keywords = ["rightsize", "downsize", "resize", "instance type", "oversized"]
    if any(kw in query.lower() for kw in rightsize_keywords):
        recs = await get_rightsizing_recommendations()
        if recs:
            context_parts.append(f"Rightsizing recommendations: {json.dumps(recs[:5], default=str)}")

    # Build prompt and call LLM
    context = "\n\n".join(context_parts)

    llm = ChatBedrock(
        model_id=settings.bedrock_model_id,
        region_name=settings.aws_region,
    )

    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", """Here is the current cloud cost context:

{context}

User question: {query}

Provide a clear, data-grounded answer."""),
    ])

    chain = prompt | llm
    response = await chain.ainvoke({"context": context, "query": query})

    return {
        "answer": response.content,
        "sources": _extract_sources(context_parts),
        "context_used": len(context_parts),
    }


def _summarize_cost_data(cost_data: dict) -> str:
    """Create a concise summary of cost data."""
    results = cost_data.get("results_by_time", [])
    if not results:
        return "No data available"

    service_totals: dict[str, float] = {}
    daily_totals: dict[str, float] = {}

    for period in results:
        date = period["time_period"]["start"]
        day_total = 0.0
        for group in period.get("groups", []):
            cost = float(group["metrics"].get("UnblendedCost", {}).get("Amount", 0))
            service = group["keys"][0] if group["keys"] else "Unknown"
            service_totals[service] = service_totals.get(service, 0) + cost
            day_total += cost
        daily_totals[date] = day_total

    total = sum(service_totals.values())
    top_services = sorted(service_totals.items(), key=lambda x: x[1], reverse=True)[:5]

    summary = f"Total: ${total:.2f} over {len(results)} days. "
    summary += "Top services: " + ", ".join(
        f"{svc} (${amt:.2f})" for svc, amt in top_services
    )

    # Detect trend
    if daily_totals:
        days = sorted(daily_totals.keys())
        if len(days) >= 7:
            last_week = sum(daily_totals[d] for d in days[-7:])
            prev_week = sum(daily_totals[d] for d in days[-14:-7]) if len(days) >= 14 else last_week
            if prev_week > 0:
                change = ((last_week - prev_week) / prev_week) * 100
                summary += f" Week-over-week change: {change:+.1f}%"

    return summary


def _extract_sources(context_parts: list[str]) -> list[str]:
    """Extract source labels from context parts."""
    sources = []
    for part in context_parts:
        if "cost data" in part.lower():
            sources.append("AWS Cost Explorer (30-day)")
        elif "anomal" in part.lower():
            sources.append("Anomaly Detection (IsolationForest)")
        elif "idle" in part.lower():
            sources.append("EC2 Utilization (CloudWatch)")
        elif "ebs" in part.lower() or "volume" in part.lower():
            sources.append("EBS Volume Inventory")
        elif "rightsize" in part.lower() or "rightsiz" in part.lower():
            sources.append("AWS Rightsizing Recommendations")
    return sources
