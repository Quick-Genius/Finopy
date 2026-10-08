"""The briefing is deterministic advice for humans. These pin its numbers and its boundaries."""
import copy

from app import briefing, config
from test_summarize import PACKET

ST = {"daily": [3.9, 4.1, 3.8, 4.0, 3.9, 4.2, 4.0, 3.9, 4.1, 18.0, 18.2, 18.1, 18.3, 18.19], "cpu": {"i-4b7317e4f6f9c0383": [2.0, 2.5, 2.2, 2.4, 2.3, 2.6, 2.1], "i-4cc33a9ae273babe4": [2.1, 2.0, 2.3, 2.2, 2.1, 2.0, 2.2]}}


def pk(**over):
    p = copy.deepcopy(PACKET); p["candidate_monthly"] = "420.0000"; p.update(over); return p


def test_idle_waste_briefing_has_all_sections_and_correct_numbers(monkeypatch):
    monkeypatch.setattr(config, "ACTION_MODE", "tag_after_approval")
    b = briefing.build(pk(), ST)
    assert b["issue"][0].startswith("AmazonEC2 spend for *batch-dev* was $54.00") or "$54.00" in b["issue"][0]
    assert "+350%" in b["issue"][0] and "$12.00" in b["issue"][0]                   # (54-12)/12 = 350%, computed in code
    assert "2 idle instance(s) cost about $420.00/month" in b["fix"][0]
    assert any("reversible tag" in r for r in b["recommendation"]) and any("stops them by hand" in r for r in b["recommendation"])
    assert "Nothing changes until a human decides" in b["who_acts"]
    t = b["text"]
    for h in ("*Issue*", "*Has to be fixed*", "*Recommendation*", "*Stats*", "*Who acts:*", "the system fixes nothing by itself"):
        assert h in t


def test_stats_are_text_charts_and_flag_the_window():
    s = briefing.stats_block(pk(), ST)
    assert "cost/day (14d)" in s and "^^^ analysis window" in s and "3 days expected" in s and "3 days actual" in s and "avg 2.3%" in s
    assert briefing.spark([1, 1, 1]) == "▄▄▄" and briefing.spark([0, 10])[0] == "▁" and briefing.spark([0, 10])[-1] == "█"
    assert briefing.bar(5, 10, 10) == "█████░░░░░" and briefing.bar(0, 10, 4) == "░░░░" and briefing.bar(0.01, 10, 4)[0] == "█"


def test_protected_resource_is_never_advised_to_be_stopped():
    b = briefing.build(pk(state="REFUSED", risk="high"), ST)
    t = b["text"].lower()
    assert "must not be changed automatically" in t and "do not stop it" in t and "approves the reversible tag" not in t
    assert "no automatic action is allowed" in b["who_acts"].lower()


def test_ambiguous_owner_asks_humans_to_fix_the_records_first():
    p = pk(classification="needs_review", state="INVESTIGATED")
    p["owner"] = {"name": None, "confidence": 0.0, "status": "needs_review", "reason": "conflicting owners: ['payments-platform', 'payments-risk']", "evidence_sources": []}
    b = briefing.build(p, ST)
    assert any("Owner could not be established" in i for i in b["issue"]) and "no approval is possible" in b["recommendation"][1].lower()
    assert "resolve ownership" in b["who_acts"].lower()


def test_unattached_volume_never_recommends_the_system_deleting():
    b = briefing.build(pk(kind="unattached-ebs", candidates=["vol-0123456789abcdef0"]), {"daily": [], "cpu": {}})
    t = " ".join(b["recommendation"]).lower()
    assert "never deletes anything" in t and "by hand" in t


def test_resource_names_cannot_inject_slack_markup():
    p = pk(workload="x<!channel>&evil"); b = briefing.build(p, ST)
    assert "<!channel>" not in b["text"] and "&lt;!channel&gt;&amp;evil" in b["text"]


def test_cpu_chart_uses_a_fixed_scale_so_idle_noise_is_not_exaggerated():
    flat_low = briefing.spark([2.0, 2.5, 2.2, 2.4, 2.3, 2.6, 2.1], 0, 10)
    assert set(flat_low) <= set("▂▃") and briefing.spark([9.0, 9.5], 0, 10)[0] in "▇█"
    p = pk(); p["candidates"] = ["i-4b7317e4f6f9c0383"]
    one = briefing.build(p, {"daily": ST["daily"], "cpu": {"i-4b7317e4f6f9c0383": [2.9] * 7}})
    assert "average CPU 2.9% over 7 days" in one["issue"][1] and "2.9%-2.9%" not in one["text"]


def test_default_mode_is_alert_only_and_the_briefing_promises_no_change(monkeypatch):
    monkeypatch.setattr(config, "ACTION_MODE", "alert_only")                    # the shipped default (docker-compose.yml: ${ACTION_MODE:-alert_only})
    b = briefing.build(pk(), ST); text = b["text"].lower()
    assert "system itself changes nothing" in text and "reversible tag" not in text and "owner fixes it by hand" in b["who_acts"].lower()
    done = briefing.build(pk(state="APPROVED", approval={"status": "approved", "decided_by": "payments-approver", "role": "approver", "note": None}), ST)
    assert "the system changed nothing" in done["who_acts"]
