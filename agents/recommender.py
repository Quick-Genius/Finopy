"""Recommender Agent — generates cost optimization recommendations using LLM."""

from __future__ import annotations

import uuid
from langchain_core.messages import AIMessage, SystemMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate

from agents.state import FinOpsState, Recommendation
from config import settings


SYSTEM_PROMPT = """You are an expert FinOps advisor. Given detected cost anomalies and usage data,
generate specific, actionable cost optimization recommendations.

For each recommendation, provide:
1. A clear title
2. Detailed description of the action
3. Estimated monthly savings in USD
4. Risk level (low/medium/high)
5. Action type (rightsize/terminate/reserve/schedule/migrate)
6. Affected resource IDs

Be conservative with savings estimates. Only recommend actions with clear justification.
Format your response as structured JSON."""


async def generate_recommendations(state: FinOpsState) -> FinOpsState:
    """Use LLM to generate optimization recommendations based on anomalies."""
    from langchain_aws import ChatBedrock

    anomalies = state["anomalies"]
    cost_data = state["cost_data"]

    llm = ChatBedrock(
        model_id=settings.bedrock_model_id,
        region_name=settings.aws_region,
    )

    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", """Anomalies detected:
{anomalies}

Recent cost data summary:
{cost_summary}

User query (if any): {user_query}

Generate optimization recommendations."""),
    ])

    chain = prompt | llm

    response = await chain.ainvoke({
        "anomalies": str(anomalies),
        "cost_summary": _summarize_costs(cost_data),
        "user_query": state.get("user_query", "General optimization"),
    })

    recommendations = _parse_recommendations(response.content)

    messages = state.get("messages", [])
    total_savings = sum(r["estimated_savings_monthly"] for r in recommendations)
    messages.append(
        AIMessage(content=f"Generated {len(recommendations)} recommendations with ~${total_savings:.2f}/mo potential savings.")
    )

    return {
        **state,
        "recommendations": recommendations,
        "messages": messages,
        "current_phase": "recommend",
    }


def _summarize_costs(cost_data: dict) -> str:
    """Create a brief text summary of cost data for the LLM."""
    if not cost_data:
        return "No cost data available."

    results = cost_data.get("results_by_time", [])
    total = sum(
        float(group.get("metrics", {}).get("UnblendedCost", {}).get("Amount", 0))
        for period in results
        for group in period.get("groups", [])
    )
    return f"Total spend over period: ${total:.2f} across {len(results)} days."


def _parse_recommendations(llm_output: str) -> list[Recommendation]:
    """Parse LLM output into structured recommendations."""
    import json

    try:
        # Try to extract JSON from the response
        start = llm_output.find("[")
        end = llm_output.rfind("]") + 1
        if start >= 0 and end > start:
            raw = json.loads(llm_output[start:end])
            return [
                Recommendation(
                    id=str(uuid.uuid4()),
                    title=r.get("title", "Unnamed"),
                    description=r.get("description", ""),
                    estimated_savings_monthly=float(r.get("estimated_savings_monthly", 0)),
                    risk_level=r.get("risk_level", "medium"),
                    action_type=r.get("action_type", "rightsize"),
                    resource_ids=r.get("resource_ids", []),
                    terraform_plan=r.get("terraform_plan"),
                    approved=None,
                )
                for r in raw
            ]
    except (json.JSONDecodeError, ValueError):
        pass

    return []
