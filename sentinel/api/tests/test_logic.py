"""Pure-logic tests: no database, no network, no Custodian. They prove the deterministic rules, not the integrations."""
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app import policy
from app.assess import ActionIntent, assess
from app.attribute import attribute_group, attribute_resource
from app.detect import analyze_series, idle_ec2, unattached_ebs
from app.security import canonical, sha256_hex, sign, target_set_hash, verify
from app.states import TRANSITIONS, InvalidTransition, transition

WE = date(2026, 10, 8)


def series(fn, days=60):
    return {WE - timedelta(days=n): Decimal(str(round(fn(WE - timedelta(days=n), n), 4))) for n in range(days, 0, -1)}


def wk(d):
    return 1.0 if d.weekday() < 5 else 0.8


# ---------------------------------------------------------------- detection
def test_stable_series_with_weekly_seasonality_is_not_anomalous():
    s = series(lambda d, n: 40 * wk(d) + (n % 3) * 0.2)
    assert analyze_series(s, WE)["status"] == "normal"


def test_material_spike_is_flagged_with_deterministic_money():
    s = series(lambda d, n: 20 * wk(d) * (2.5 if n <= 3 else 1.0))
    r = analyze_series(s, WE)
    assert r["status"] == "anomaly" and r["actual"] > r["expected"] and 0 < r["anomaly_score"] < 1
    assert isinstance(r["actual"], Decimal)


def test_small_absolute_increase_is_below_materiality():
    s = series(lambda d, n: (1.0 if n > 3 else 1.6))      # +60% but only $0.60/day
    assert analyze_series(s, WE)["status"] == "normal"


def test_cost_drop_is_not_an_anomaly():
    s = series(lambda d, n: 100.0 if n > 3 else 10.0)
    assert analyze_series(s, WE)["status"] == "normal"


def test_insufficient_history_and_zero_median_do_not_explode():
    assert analyze_series(series(lambda d, n: 5.0, days=10), WE)["status"] == "insufficient_data"
    flat_zero = series(lambda d, n: 0.0 if n > 3 else 30.0)
    assert analyze_series(flat_zero, WE)["status"] in ("anomaly", "normal")       # no inf / ZeroDivision


def cpu(vals):
    return [Decimal(str(v)) for v in vals]


def test_idle_requires_data_and_running_state():
    res = {"state": "running", "launch_time": datetime(2026, 8, 1, tzinfo=timezone.utc)}
    assert idle_ec2(res, cpu([2] * 7), WE)["idle"] is True
    assert idle_ec2(res, [], WE) == {"idle": False, "reason": "insufficient_data", "points": 0}      # no data is never "idle"
    assert idle_ec2({**res, "state": "stopped"}, cpu([2] * 7), WE)["idle"] is False
    assert idle_ec2(res, cpu([2, 2, 2, 60, 70, 80, 90]), WE)["idle"] is False


def test_unattached_ebs_rule():
    old = datetime(2026, 9, 1, tzinfo=timezone.utc)
    assert unattached_ebs({"state": "available", "attached": False, "launch_time": old}, WE)["unattached"] is True
    assert unattached_ebs({"state": "in-use", "attached": True, "launch_time": old}, WE)["unattached"] is False


# ---------------------------------------------------------------- attribution
CTX = {"owners": {"payments-platform", "payments-risk"}, "account_map": {}, "deployments": {"i-aaaaaaaa": "payments-platform"},
       "naming": {"name": [("payments-batch-*", "payments-platform")], "vpc": {}}}


def r(rid="i-aaaaaaaa", **tags):
    return {"resource_id": rid, "account_id": "1", "tags": tags, "name": tags.get("Name")}


def test_consistent_tag_and_deployment_gives_high_confidence():
    a = attribute_resource("payments", r(TenantId="payments", Owner="payments-platform"), CTX)
    assert a["status"] == "attributed" and a["owner"] == "payments-platform" and a["confidence"] == 0.95


def test_conflicting_tag_and_deployment_needs_review_and_never_guesses():
    a = attribute_resource("payments", r(TenantId="payments", Owner="payments-risk"), CTX)
    assert a["status"] == "needs_review" and a["owner"] is None and a["confidence"] == 0.0


def test_foreign_tenant_tag_and_unknown_owner_and_no_evidence():
    assert attribute_resource("payments", r(TenantId="analytics", Owner="x"), CTX)["status"] == "needs_review"
    assert attribute_resource("payments", r("i-bbbbbbbb", TenantId="payments", Owner="made-up-owner"), CTX)["status"] == "needs_review"
    assert attribute_resource("payments", r("i-cccccccc"), CTX)["owner"] is None


def test_naming_only_is_weak_evidence_below_action_threshold():
    a = attribute_resource("payments", r("i-dddddddd", Name="payments-batch-7"), CTX)
    assert a["status"] == "attributed" and a["confidence"] == 0.6      # < MIN_ATTRIBUTION_CONFIDENCE (0.75)


def test_group_with_one_bad_member_needs_review():
    g = attribute_group("payments", [r(TenantId="payments", Owner="payments-platform"), r("i-eeeeeeee")], CTX, set())
    assert g["status"] == "needs_review"


# ---------------------------------------------------------------- assessment / intent
def test_known_event_explains_spike_as_legitimate():
    sp = {"status": "anomaly", "actual": Decimal(120), "expected": Decimal(60), "anomaly_score": 0.9, "features": {}}
    ev = [{"event_id": "e", "start_date": WE - timedelta(days=5), "end_date": WE + timedelta(days=3), "description": "launch", "expected_uplift_pct": Decimal(150)}]
    a = assess(kind="spend-anomaly", spend=sp, wastes=[], attribution={"status": "attributed", "reason": ""}, events=ev, window_end=WE, window_days=3)
    assert a["classification"] == "legitimate"


def test_spike_far_beyond_event_is_needs_review():
    sp = {"status": "anomaly", "actual": Decimal(900), "expected": Decimal(60), "anomaly_score": 0.9, "features": {}}
    ev = [{"event_id": "e", "start_date": WE - timedelta(days=5), "end_date": WE + timedelta(days=3), "description": "launch", "expected_uplift_pct": Decimal(50)}]
    a = assess(kind="spend-anomaly", spend=sp, wastes=[], attribution={"status": "attributed", "reason": ""}, events=ev, window_end=WE, window_days=3)
    assert a["classification"] == "needs_review"


def test_unattributed_waste_is_needs_review_not_probable_waste():
    a = assess(kind="idle-ec2", spend=None, wastes=[{"summary": "idle"}], attribution={"status": "needs_review", "reason": "conflict"}, events=[], window_end=WE, window_days=3)
    assert a["classification"] == "needs_review"


@pytest.mark.parametrize("bad", [
    {"template": "idle-ec2-v1", "action": "terminate", "target_ids": ["i-aaaaaaaa"]},                       # destructive action
    {"template": "idle-ec2-v1", "action": "mark-for-stop", "target_ids": ["i-aaaaaaaa"], "yaml": "x: y"},   # extra field
    {"template": "idle-ec2-v1", "action": "mark-for-stop", "target_ids": ["i-aaaaaaaa; rm -rf /"]},         # shell in id
    {"template": "idle-ec2-v1", "action": "mark-for-stop", "target_ids": ["i-aaaaaaaa\n  - type: terminate"]},  # yaml injection
    {"template": "idle-ec2-v1", "action": "tag-for-review", "target_ids": ["i-aaaaaaaa"]},                  # pair not allow-listed
    {"template": "custom", "action": "mark-for-stop", "target_ids": ["i-aaaaaaaa"]},
    {"template": "idle-ec2-v1", "action": "mark-for-stop", "target_ids": ["i-aaaaaaaa"], "params": {"days": 90}},
    {"template": "idle-ec2-v1", "action": "mark-for-stop", "target_ids": []},
])
def test_model_authored_intents_outside_the_allow_list_are_rejected(bad):
    with pytest.raises(ValidationError):
        ActionIntent(**bad)


# ---------------------------------------------------------------- policy rendering and hashes
def test_policy_render_is_deterministic_and_hashes_are_separate():
    i = ActionIntent(template="idle-ec2-v1", action="mark-for-stop", target_ids=["i-bbbbbbbb", "i-aaaaaaaa"])
    n1, t1 = policy.render(i, "payments", "case_payments_x", 3)
    n2, t2 = policy.render(ActionIntent(template="idle-ec2-v1", action="mark-for-stop", target_ids=["i-aaaaaaaa", "i-bbbbbbbb"]), "payments", "case_payments_x", 3)
    assert (n1, t1) == (n2, t2) and n1 == "case_payments_x-v3"
    assert "mark-for-op" in t1 and "terminate" not in t1 and "delete" not in t1
    assert "tag:Protected: absent" in t1 and "tag:TenantId: payments" in t1
    assert sha256_hex(t1) != target_set_hash(["i-aaaaaaaa", "i-bbbbbbbb"])
    assert target_set_hash(["i-bbbbbbbb", "i-aaaaaaaa"]) == target_set_hash(["i-aaaaaaaa", "i-bbbbbbbb"])     # order-independent
    assert target_set_hash(["i-aaaaaaaa"]) != target_set_hash(["i-aaaaaaaa", "i-bbbbbbbb"])                     # any change changes it
    assert sha256_hex(t1) != sha256_hex(policy.render(i, "analytics", "case_payments_x", 3)[1])               # tenant is part of the policy


def test_ebs_template_only_tags():
    i = ActionIntent(template="unattached-ebs-v1", action="tag-for-review", target_ids=["vol-0123456789abcdef0"])
    _, t = policy.render(i, "analytics", "case_analytics_x", 1)
    assert "type: tag" in t and "delete" not in t and "snapshot" not in t


# ---------------------------------------------------------------- signatures and state machine
def test_hmac_signature_replay_window_and_tamper():
    body = b'{"a":1}'
    ts = str(int(time.time()))
    sig = sign("s", ts, body)
    assert verify("s", ts, body, sig, 300)
    assert not verify("s", ts, b'{"a":2}', sig, 300)
    assert not verify("other", ts, body, sig, 300)
    old = str(int(time.time()) - 1000)
    assert not verify("s", old, body, sign("s", old, body), 300)
    assert not verify("s", "garbage", body, sig, 300)


def test_state_machine_has_no_jump_to_execution():
    happy = ["NEW", "INVESTIGATED", "DRY_RUN", "PENDING", "APPROVED", "EXECUTED", "VERIFIED"]
    for a, b in zip(happy, happy[1:]):
        assert b in TRANSITIONS[a]
    for s in TRANSITIONS:
        assert "EXECUTED" not in TRANSITIONS[s] or s == "APPROVED"
        assert "APPROVED" not in TRANSITIONS[s] or s == "PENDING"
    assert "REJECTED" in TRANSITIONS["PENDING"] and "EXPIRED" in TRANSITIONS["PENDING"]
    with pytest.raises(InvalidTransition):
        transition(None, "t", "c", "NEW", "EXECUTED", "x")
    with pytest.raises(InvalidTransition):
        transition(None, "t", "c", "INVESTIGATED", "APPROVED", "x")
