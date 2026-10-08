"""Observer Agent — ingests cloud billing and usage data."""

import datetime
from config import settings
from agents.state import FinOpsState
from cloud.aws.cost_explorer import fetch_cost_and_usage


async def observe_costs(state: FinOpsState) -> FinOpsState:
    """Fetch recent cost data from cloud providers."""
    end_date = datetime.date.today()
    start_date = end_date - datetime.timedelta(days=30)

    cost_data = await fetch_cost_and_usage(
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
        granularity="DAILY",
        group_by=["SERVICE", "REGION"],
    )

    return {
        **state,
        "cost_data": cost_data,
        "current_phase": "observe",
    }
