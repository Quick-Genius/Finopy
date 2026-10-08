"""The LLM is only a phrasing tool. These prove the fact check rejects anything the evidence does not contain."""
import json

import pytest

from app import config, summarize
from app.summarize import check_summary, summarize as run_summarize, template_summary

PACKET = {
    "case_id": "case_payments_batch-dev-idle-ec2", "tenant": "payments", "state": "PENDING", "workload": "batch-dev", "kind": "idle-ec2",
    "service": "AmazonEC2", "classification": "probable_waste", "assessment_reasons": ["i-4b7317e4f6f9c0383 averaged 2.2% CPU over 7 days"],
    "spend": {"status": "anomaly", "actual": "54.0000", "expected": "12.0000", "currency": "USD", "anomaly_score": 0.99, "window_days": 3, "history_days": 28},
    "owner": {"name": "payments-platform", "confidence": 0.95, "status": "attributed", "reason": "consistent evidence", "evidence_sources": ["deployment", "naming", "tag"]},
    "waste_indicators": ["i-4b7317e4f6f9c0383 averaged 2.2% CPU over 7 days (7 days under 5.0%)"], "candidates": ["i-4b7317e4f6f9c0383", "i-4cc33a9ae273babe4"],
    "risk": "low", "rollback": "Remove the finops_mark tag.", "policy_template": "idle-ec2-v1", "projected_monthly": "420.0000", "currency": "USD",
    "realized_savings": "not measured", "dry_run": {"status": "valid", "custodian_exit_code": 0, "targets": ["i-4b7317e4f6f9c0383", "i-4cc33a9ae273babe4"], "excluded": [], "refusal_reasons": []},
    "approval": {"status": "requested", "decided_by": None, "role": None, "note": None}, "execution": None, "verification": None, "recent_events": [],
}

GOOD = ("batch-dev in payments looks like probable waste: spend on AmazonEC2 was 54 USD over the last 3 days against an expected 12. "
        "The owner is payments-platform (confidence 0.95). Two targets were found by the dry-run, i-4b7317e4f6f9c0383 and i-4cc33a9ae273babe4. "
        "Projected saving is about 420 USD per month; realized savings are not measured.")


def test_faithful_summary_passes():
    assert check_summary(GOOD, PACKET) == []


@pytest.mark.parametrize("bad,why", [
    (GOOD.replace("420 USD", "9,999 USD"), "number"),                              # invented saving
    (GOOD.replace("12.", "13.").replace("against an expected 12", "against an expected 15"), "number"),
    (GOOD + " Also i-deadbeefdeadbeef is affected.", "resource id"),               # id from another tenant / hallucinated
    (GOOD.replace("payments-platform", "payments-risk"), "owner"),                 # guessed owner
    (GOOD + " See https://evil.example/x", "link"),
    (GOOD + " Run `aws ec2 stop-instances`.", "code"),
    ("x" * 1300, "long"),
    (" ".join(["word"] * 101), "over 100 words"),
    ("   ", "empty"),
])
def test_anything_not_in_the_evidence_is_rejected(bad, why):
    assert check_summary(bad, PACKET), why


def test_packet_never_carries_secrets_or_policy_text():
    s = json.dumps(PACKET)
    for forbidden in ("policy_yaml", "sha256", "pin", "token", "secret", "HMAC", "Bearer"):
        assert forbidden.lower() not in s.lower().replace("projected", "")


def test_template_summary_is_deterministic_and_passes_its_own_check():
    t = template_summary(PACKET)
    assert t == template_summary(PACKET) and "$420.00" in t and "not measured" in t
    assert check_summary(t, PACKET) == []


def test_no_llm_configured_uses_template(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", ""); monkeypatch.setattr(config, "LLM_API_KEY", "")
    r = run_summarize(PACKET)
    assert r["source"] == "template" and r["reason"] == "no LLM configured"


def test_llm_failure_or_bad_output_falls_back_to_template(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "cohere"); monkeypatch.setattr(config, "LLM_API_KEY", "k")
    monkeypatch.setitem(summarize.CALLERS, "cohere", lambda p: (_ for _ in ()).throw(TimeoutError()))
    assert run_summarize(PACKET)["source"] == "template"
    monkeypatch.setitem(summarize.CALLERS, "cohere", lambda p: GOOD + " Savings may reach 9,999 USD.")
    r = run_summarize(PACKET)
    assert r["source"] == "template" and "rejected" in r["reason"]
    monkeypatch.setitem(summarize.CALLERS, "cohere", lambda p: GOOD)
    assert run_summarize(PACKET)["source"] == "llm"


def test_alibaba_provider_is_registered_and_never_trusted_blindly(monkeypatch):
    assert "alibaba" in summarize.CALLERS
    monkeypatch.setattr(config, "LLM_PROVIDER", "alibaba"); monkeypatch.setattr(config, "LLM_API_KEY", "k")
    monkeypatch.setitem(summarize.CALLERS, "alibaba", lambda p: GOOD + " It will save 1,234 USD.")
    assert run_summarize(PACKET)["source"] == "template"          # same fact check applies to every provider
