"""Supervisor agent — orchestrates the Observe → Diagnose → Recommend → Act loop."""

from __future__ import annotations

from langgraph.graph import StateGraph, END
from langchain_core.messages import HumanMessage

from agents.state import FinOpsState
from agents.observer import observe_costs
from agents.diagnoser import diagnose_anomalies
from agents.recommender import generate_recommendations
from agents.executor import execute_approved_actions


def should_diagnose(state: FinOpsState) -> str:
    """Route: if cost data exists, proceed to diagnosis."""
    if state.get("cost_data"):
        return "diagnose"
    return END


def should_recommend(state: FinOpsState) -> str:
    """Route: if anomalies found, generate recommendations."""
    if state.get("anomalies"):
        return "recommend"
    return "report"


def should_execute(state: FinOpsState) -> str:
    """Route: if there are approved actions, execute them."""
    if state.get("approved_actions"):
        return "execute"
    return "report"


def generate_report(state: FinOpsState) -> FinOpsState:
    """Generate a summary report of findings and actions."""
    anomalies = state.get("anomalies", [])
    recommendations = state.get("recommendations", [])
    executions = state.get("execution_results", [])

    total_savings = sum(r["estimated_savings_monthly"] for r in recommendations)

    report = f"""## FinOps Agent Report

### Anomalies Detected: {len(anomalies)}
{chr(10).join(f"- **{a['service']}** ({a['region']}): {a['deviation_pct']:.1f}% over expected (${a['actual_cost']:.2f} vs ${a['expected_cost']:.2f})" for a in anomalies)}

### Recommendations: {len(recommendations)}
{chr(10).join(f"- [{r['risk_level'].upper()}] {r['title']}: ~${r['estimated_savings_monthly']:.2f}/mo" for r in recommendations)}

### Total Potential Monthly Savings: ${total_savings:.2f}

### Actions Executed: {len(executions)}
{chr(10).join(f"- {e.get('action', 'unknown')}: {e.get('status', 'unknown')}" for e in executions)}
"""
    return {**state, "report": report, "current_phase": "complete"}


def build_finops_graph() -> StateGraph:
    """Construct the FinOps agent graph."""
    graph = StateGraph(FinOpsState)

    # Add nodes
    graph.add_node("observe", observe_costs)
    graph.add_node("diagnose", diagnose_anomalies)
    graph.add_node("recommend", generate_recommendations)
    graph.add_node("execute", execute_approved_actions)
    graph.add_node("report", generate_report)

    # Set entry point
    graph.set_entry_point("observe")

    # Add edges with conditional routing
    graph.add_conditional_edges("observe", should_diagnose, {"diagnose": "diagnose", END: END})
    graph.add_conditional_edges("diagnose", should_recommend, {"recommend": "recommend", "report": "report"})
    graph.add_conditional_edges("recommend", should_execute, {"execute": "execute", "report": "report"})
    graph.add_edge("execute", "report")
    graph.add_edge("report", END)

    return graph.compile()


# Compiled graph
finops_agent = build_finops_graph()


async def run_finops_cycle(user_query: str | None = None):
    """Run a full FinOps optimization cycle."""
    initial_state: FinOpsState = {
        "messages": [HumanMessage(content=user_query)] if user_query else [],
        "cost_data": None,
        "anomalies": [],
        "recommendations": [],
        "approved_actions": [],
        "execution_results": [],
        "current_phase": "observe",
        "user_query": user_query,
        "report": None,
    }

    result = await finops_agent.ainvoke(initial_state)
    return result


if __name__ == "__main__":
    import asyncio

    result = asyncio.run(run_finops_cycle("Why did my AWS bill spike this week?"))
    print(result["report"])
