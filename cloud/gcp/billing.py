"""GCP Billing BigQuery export integration."""

from google.cloud import bigquery
from config import settings


async def fetch_gcp_billing(days: int = 30) -> dict:
    """Fetch GCP billing data from BigQuery export.

    Requires the billing export to be configured in BigQuery.
    """
    if not settings.gcp_project_id:
        return {"results_by_time": []}

    client = bigquery.Client(project=settings.gcp_project_id)

    query = f"""
    SELECT
        DATE(usage_start_time) as date,
        service.description as service,
        location.region as region,
        SUM(cost) as cost
    FROM `{settings.gcp_project_id}.billing_export.gcp_billing_export_v1*`
    WHERE DATE(usage_start_time) >= DATE_SUB(CURRENT_DATE(), INTERVAL {days} DAY)
    GROUP BY date, service, region
    ORDER BY date, service
    """

    results = client.query(query).result()

    # Convert to normalized format matching AWS structure
    by_date: dict[str, list] = {}
    for row in results:
        date_str = str(row.date)
        if date_str not in by_date:
            by_date[date_str] = []
        by_date[date_str].append({
            "keys": [row.service, row.region or "global"],
            "metrics": {"UnblendedCost": {"Amount": str(row.cost), "Unit": "USD"}},
        })

    return {
        "results_by_time": [
            {"time_period": {"start": date, "end": date}, "groups": groups}
            for date, groups in sorted(by_date.items())
        ]
    }
