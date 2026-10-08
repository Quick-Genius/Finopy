"""Diagnoser Agent — detects anomalies and identifies root causes."""

from langchain_core.messages import AIMessage

from agents.state import FinOpsState, CostAnomaly
from analytics.anomaly import detect_anomalies_isolation_forest, detect_anomalies_prophet


async def diagnose_anomalies(state: FinOpsState) -> FinOpsState:
    """Analyze cost data for anomalies using multiple detection methods."""
    cost_data = state["cost_data"]
    if not cost_data:
        return state

    # Run dual detection for robustness
    iso_anomalies = detect_anomalies_isolation_forest(cost_data)
    prophet_anomalies = detect_anomalies_prophet(cost_data)

    # Merge: flag as anomaly if either method detects it
    all_anomalies: list[CostAnomaly] = []
    seen = set()

    for anomaly in iso_anomalies + prophet_anomalies:
        key = (anomaly["service"], anomaly["region"], anomaly["timestamp"])
        if key not in seen:
            seen.add(key)
            all_anomalies.append(anomaly)

    # Sort by severity
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    all_anomalies.sort(key=lambda a: severity_order.get(a["severity"], 99))

    messages = state.get("messages", [])
    if all_anomalies:
        messages.append(
            AIMessage(content=f"Detected {len(all_anomalies)} cost anomalies across services.")
        )

    return {
        **state,
        "anomalies": all_anomalies,
        "messages": messages,
        "current_phase": "diagnose",
    }
