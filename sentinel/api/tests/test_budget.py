"""Over-budget alert and healthy digest: deterministic, no database."""
from datetime import date, timedelta
from decimal import Decimal

from app import briefing
from app.graph import build_drafts

WE = date(2026, 10, 8)


def facts(growth=0.0):
    rows = []
    for n in range(40, 0, -1):
        d = WE - timedelta(days=n)
        rows.append({"usage_date": d, "service": "AmazonRDS", "workload": "ledger-db", "resource_id": "", "owner_tag": "payments-platform", "amount": Decimal("60.0")})
        g = Decimal(str(growth)) if n <= 10 else Decimal(0)
        rows.append({"usage_date": d, "service": "AmazonEC2", "workload": "batch-dev", "resource_id": "", "owner_tag": "payments-platform", "amount": Decimal("40.0") + g})
    return rows


def data(budget):
    return {"tenant": {"owner_group": "payments-eng", "monthly_budget_usd": budget}, "cost_facts": facts(growth=4), "resources": [], "utilization": {},
            "ref": {"owners": {"payments-platform"}, "account_map": {}, "deployments": {}, "naming": {"name": [], "vpc": {}}, "events": []}}


def test_spend_over_the_monthly_budget_raises_one_review_alert_naming_the_drivers():
    drafts = build_drafts("payments", WE, data(budget=2900))      # last 30 days = 3000 + 4*10 = 3040 > 2900 (growth too small to be a spike)
    assert [d["case_id"] for d in drafts] == ["case_payments_all-services-budget-overrun"]
    c = drafts[0]["content"]
    assert c["recommendation"]["route"] == "review" and c["recommendation"]["intent"] is None          # an alert, never an action
    assert c["assessment"]["classification"] == "needs_review" and c["attribution"]["owner"] == "payments-eng"
    f = c["signal"]["features"]
    assert f["spent_30d_usd"] == "3040.0000" and f["budget_usd"] == "2900.0000" and f["over_pct"] == 4.8
    assert f["top_drivers"][0]["workload"] == "batch-dev" and f["top_drivers"][0]["daily_increase"] == "4.0000"


def test_within_budget_raises_nothing():
    assert build_drafts("payments", WE, data(budget=4000)) == []
    assert build_drafts("payments", WE, {**data(budget=None), "tenant": {"owner_group": "x", "monthly_budget_usd": None}}) == []     # no budget set = no budget alert


def test_budget_briefing_has_the_numbers_and_proposes_no_technical_action():
    c = build_drafts("payments", WE, data(budget=2900))[0]["content"]
    p = {"kind": c["kind"], "workload": c["workload"], "service": c["service"], "state": "INVESTIGATED", "risk": "low", "classification": "needs_review",
         "spend": {"status": "budget_overrun", "actual": c["signal"]["actual"], "expected": c["signal"]["expected"], "window_days": 30}, "budget": c["signal"]["features"],
         "owner": c["attribution"], "waste_indicators": [], "candidates": [], "approval": None, "rollback": "n/a", "candidate_monthly": "0"}
    b = briefing.build(p, {"daily": [100.0] * 13 + [120.0], "cpu": {}})
    assert "$3,040.00" in b["issue"][0] and "$2,900.00" in b["issue"][0] and "+4.8%" in b["issue"][0]
    assert any("batch-dev" in i for i in b["issue"]) and "system changes nothing" in b["who_acts"] and "budget (month)" in b["stats"]


def test_healthy_digest_is_just_costs_no_recommendations():
    t = briefing.digest_text("payments", [99.0] * 14, 2990.0, 3700.0, 297.0, 298.0, [("AmazonRDS", 1090.0), ("AWSLambda", 600.0)])
    assert "Cost check: all clear" in t and "$2,990.00 of a $3,700.00 monthly budget (81%)" in t and "AmazonRDS $1,090.00" in t
    for forbidden in ("*Recommendation*", "*Issue*", "*Has to be fixed*", "approve"):
        assert forbidden not in t
    assert "budget (month)" in t and "30 days spent" in t and "```" in t
