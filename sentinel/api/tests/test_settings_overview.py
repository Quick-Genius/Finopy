"""Settings, overview and integrations. The first half is pure logic (no database); the second half needs the running
stack (STACK_TESTS=1, same convention as test_stack.py) and uses the seeded demo principals."""
import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
from pydantic import ValidationError

from app import integrations, overview, tenant_settings as ts
from app.states import transition

TODAY = date(2026, 10, 8)


# ---------------------------------------------------------------- settings rules
def test_defaults_and_malformed_stored_values_are_ignored():
    assert ts.effective(None) == ts.DEFAULTS
    bad = ts.effective({"notify_states": "APPROVED", "min_notify_savings_usd": -5, "surprise": 1})
    assert bad == ts.DEFAULTS and "surprise" not in bad


def test_failures_are_never_muted_but_other_outcomes_can_be():
    muted = ts.effective({"notify_states": [], "min_notify_savings_usd": 10_000})
    assert all(ts.should_notify(muted, s, "0") for s in ts.ALWAYS_NOTIFY)
    assert not ts.should_notify(muted, "APPROVED", "999999")


def test_savings_threshold_applies_to_mutable_outcomes():
    s = ts.effective({"min_notify_savings_usd": 100})
    assert not ts.should_notify(s, "APPROVED", "99.99")
    assert ts.should_notify(s, "APPROVED", "100")
    assert not ts.should_notify(s, "APPROVED", None)


def test_settings_input_is_strict_and_deduplicated():
    assert ts.SettingsIn(notify_states=["VERIFIED", "APPROVED", "APPROVED"]).notify_states == ["APPROVED", "VERIFIED"]
    for bad in ({"notify_states": ["EXECUTED"]}, {"min_notify_savings_usd": -1}, {"action_mode": "tag_after_approval"}):
        with pytest.raises(ValidationError):
            ts.SettingsIn(**bad)


class FakeConn:
    """Just enough of a connection to watch whether transition() queues a notification."""
    def __init__(self, settings=None, monthly="500"):
        self.settings, self.monthly, self.outbox = settings, monthly, 0

    def execute(self, sql, params=()):
        q = " ".join(sql.split())
        res = type("R", (), {"fetchone": lambda _: None})()
        if q.startswith("update cases"):
            res.fetchone = lambda: {"current_version": 1}
        elif q.startswith("select content from case_versions"):
            res.fetchone = lambda: {"content": {"recommendation": {"projected_savings": {"monthly": self.monthly}}}}
        elif q.startswith("select settings from tenant_settings"):
            res.fetchone = lambda: {"settings": self.settings} if self.settings is not None else None
        elif q.startswith("insert into outbox"):
            self.outbox += 1
        return res


def test_transition_queues_or_skips_notification_by_workspace_setting():
    c = FakeConn(); transition(c, "t", "c", "PENDING", "APPROVED", "u"); assert c.outbox == 1
    c = FakeConn({"notify_states": ["REJECTED"]}); transition(c, "t", "c", "PENDING", "APPROVED", "u"); assert c.outbox == 0
    c = FakeConn({"notify_states": []}); transition(c, "t", "c", "APPROVED", "EXECUTION_FAILED", "u"); assert c.outbox == 1
    c = FakeConn({"min_notify_savings_usd": 1000}, monthly="500"); transition(c, "t", "c", "PENDING", "REJECTED", "u"); assert c.outbox == 0


# ---------------------------------------------------------------- overview transformer
def _cases():
    k = lambda i, st, wl, ow, m: {"case_id": f"case_{i}", "state": st, "workload": wl, "owner": ow, "monthly": Decimal(m)}
    return [k(1, "PENDING", "api", "team-a", "100.5"), k(2, "VERIFIED", "api", None, "50"), k(3, "INVESTIGATED", "etl", "team-b", "30"), k(4, "REFUSED", "etl", "team-b", "20")]


def test_overview_totals_and_rankings():
    spend = [{"usage_date": TODAY - timedelta(days=n), "amount": Decimal("10") if n < 7 else Decimal("5")} for n in range(14)]
    o = overview.build(_cases(), spend, [{"approval_id": "apr_1", "case_id": "case_1", "required_role": "approver", "expires_at": "x"}], [], TODAY)
    t = o["totals"]
    assert (t["cases"], t["needs_action"], t["pending_approvals"], t["verified"], t["refused"]) == (4, 2, 1, 1, 1)
    assert t["identified_monthly"] == "200.50" and t["in_flight_monthly"] == "150.50"      # PENDING + VERIFIED only
    assert t["realized"] == {"status": "not_measured", "amount": None}
    assert o["savings_by_workload"][0] == {"workload": "api", "monthly": "150.50", "cases": 2}
    assert {r["owner"] for r in o["savings_by_owner"]} == {"team-a", "team-b", "unassigned"}
    assert (o["spend"]["last_7d"], o["spend"]["previous_7d"], o["spend"]["change_pct"]) == ("70.00", "35.00", 100.0)
    assert o["pending_approvals"][0]["workload"] == "api" and o["pending_approvals"][0]["projected_monthly"] == "100.50"


def test_overview_with_no_data_is_well_formed():
    o = overview.build([], [], [], [], TODAY)
    assert o["totals"]["cases"] == 0 and o["spend"]["points"] == [] and o["spend"]["change_pct"] is None and o["savings_by_workload"] == []


def test_catalog_is_consistent():
    keys = [i["key"] for i in integrations.CATALOG]
    assert len(keys) == len(set(keys)) and integrations.PLANNED <= set(keys)
    assert {i["availability"] for i in integrations.CATALOG} == {"live", "planned"}


# ---------------------------------------------------------------- running stack
stack = pytest.mark.skipif(os.environ.get("STACK_TESTS") != "1", reason="needs the running stack (STACK_TESTS=1)")
API = os.environ.get("API_URL", "http://localhost:8000")
H = lambda pid: {"authorization": f"Bearer tok_{pid}_demo"}


def req(method, path, pid=None, headers=None, **kw):
    return httpx.request(method, API + path, headers={**(H(pid) if pid else {}), **(headers or {})}, timeout=20, **kw)


@stack
def test_endpoints_require_a_token_and_responses_carry_hardening_headers():
    for path in ("/overview", "/integrations", "/settings"):
        assert req("GET", path).status_code == 401
    r = req("GET", "/overview", "payments-analyst", headers={"x-request-id": "test-request-0001"})
    assert r.headers["x-request-id"] == "test-request-0001" and r.headers["cache-control"] == "no-store"
    assert req("GET", "/overview", "payments-analyst").headers["x-request-id"] != "test-request-0001"


@stack
def test_overview_matches_the_case_list_and_is_tenant_scoped():
    for pid in ("payments-analyst", "analytics-analyst"):
        o, cs = req("GET", "/overview", pid).json(), req("GET", "/cases", pid).json()
        assert o["totals"]["cases"] == len(cs) and sum(o["by_state"].values()) == len(cs)
    if os.environ.get("N8N_API_TOKEN"):                                                # automation is not a human reader
        assert req("GET", "/overview", headers={"authorization": f"Bearer {os.environ['N8N_API_TOKEN']}"}).status_code == 403


@stack
def test_integrations_catalog_and_interest_flow():
    d = req("GET", "/integrations", "payments-analyst").json()
    by = {i["key"]: i for i in d["integrations"]}
    assert by["database"]["state"] == "connected" and by["jira"]["state"] == "planned" and by["slack"]["state"] == "managed_externally"
    assert req("PUT", "/integrations/jira/interest", "payments-analyst", json={"interested": True}).json()["interested"] is True
    assert {i["key"]: i for i in req("GET", "/integrations", "payments-analyst").json()["integrations"]}["jira"]["interested"] is True
    other = {i["key"]: i for i in req("GET", "/integrations", "analytics-analyst").json()["integrations"]}
    assert other["jira"]["interested"] is False                                       # interest is per tenant
    assert req("PUT", "/integrations/aws/interest", "payments-analyst", json={"interested": True}).status_code == 409
    assert req("PUT", "/integrations/nope/interest", "payments-analyst", json={"interested": True}).status_code == 404
    assert req("PUT", "/integrations/jira/interest", "payments-analyst", json={"interested": "x"}).status_code == 422
    assert req("PUT", "/integrations/jira/interest", "payments-analyst", json={"interested": False}).json()["interested"] is False
    assert b"integration.interest" in req("GET", "/audit/export", "payments-analyst").content


@stack
def test_settings_are_role_gated_validated_audited_and_tenant_isolated():
    assert req("GET", "/settings", "payments-analyst").json()["can_edit"] is False
    assert req("PUT", "/settings", "payments-analyst", json={"min_notify_savings_usd": 5}).status_code == 403
    assert req("PUT", "/settings", "payments-reviewer", json={"notify_states": ["EXECUTED"]}).status_code == 422
    assert req("PUT", "/settings", "payments-reviewer", json={"action_mode": "tag_after_approval"}).status_code == 422
    try:
        r = req("PUT", "/settings", "payments-reviewer", json={"notify_states": ["APPROVED"], "min_notify_savings_usd": 25})
        assert r.status_code == 200 and r.json()["settings"] == {"notify_states": ["APPROVED"], "min_notify_savings_usd": 25}
        got = req("GET", "/settings", "payments-reviewer").json()
        assert got["settings"]["notify_states"] == ["APPROVED"] and got["can_edit"] is True and got["server"]["action_mode"] in ("alert_only", "tag_after_approval")
        assert req("GET", "/settings", "analytics-reviewer").json()["settings"] == ts.DEFAULTS      # other tenant untouched
        assert b"settings.updated" in req("GET", "/audit/export", "payments-reviewer").content
    finally:
        req("PUT", "/settings", "payments-reviewer", json={"notify_states": list(ts.MUTABLE_NOTIFY), "min_notify_savings_usd": 0})
