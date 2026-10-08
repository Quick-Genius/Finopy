"""Investigation summaries for Slack.

The LLM (optional) is a phrasing tool for facts the backend already computed and verified. It cannot calculate costs, pick an
owner, approve, or execute: it has no tools, sees only a trimmed evidence packet (no PINs, hashes, policy text or tokens),
and its output is rejected unless every number, resource id and owner name in it appears in that packet. On any doubt
(no key, API error, validation failure) a deterministic template built from the same packet is used instead.
"""
import json
import re

import httpx

from . import config
from .db import tx

ID_RE = re.compile(r"\b(?:i|vol)-[0-9a-f]{8,17}\b")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?)?")
NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
OWNER_RE = re.compile(r"\b(?:payments|analytics)-[a-z0-9]+(?:-[a-z0-9]+)*\b")

SYSTEM = """You write the "In short" note of a FinOps alert in Slack, for a non-technical manager.
Strict rules:
- Use ONLY facts present in the JSON the user sends. Do not add, estimate, calculate, round or infer any number, cost, saving, date, owner, team or resource id.
- Copy numbers and owner names exactly as given. Never convert units (write confidence-like values as given, never as a percentage you computed). If something is missing, leave it out.
- Savings are only ESTIMATES, never realized. Never approve, reject, recommend approving, or promise any action.
- Write 2 or 3 short sentences, at most 60 words, in everyday words: what is happening, why it costs money, who owns it.
- Do not list resource ids, case ids or states (the alert shows them elsewhere). No jargon: never write attribution, classified, confidence, anomaly score, evidence.
- No opinions or filler: never write significant, concerning, worrying, likely, suggests, appears. Mention a budget only if the JSON has a budget field.
- Plain text only: no markdown, no bullet points, no links."""


def load_packet(tenant_id: str, case_id: str) -> dict:
    """Everything here was computed and stored by the backend. Deliberately excludes policy text, hashes, PINs and tokens."""
    with tx(tenant_id) as c:
        case = c.execute("select * from cases where case_id=%s and tenant_id=%s", (case_id, tenant_id)).fetchone()
        if not case:
            raise LookupError("case not found")
        k = c.execute("select content from case_versions where case_id=%s and version=%s", (case_id, case["current_version"])).fetchone()["content"]
        pol = c.execute("select status, target_ids, excluded, refusal_reasons, dryrun_exit_code, guards from policies where case_id=%s order by created_at desc limit 1", (case_id,)).fetchone()
        apr = c.execute("select status, actor_id, actor_role, note from approvals where case_id=%s order by requested_at desc limit 1", (case_id,)).fetchone()
        ex = c.execute("select status, exit_code, matched_ids, verification from executions where case_id=%s order by created_at desc limit 1", (case_id,)).fetchone()
        aud = c.execute("select event_type, from_state, to_state, actor from audit_events where case_id=%s order by audit_id desc limit 8", (case_id,)).fetchall()
    rec, sig, attr = k["recommendation"], k["signal"], k["attribution"]
    ver = (ex or {}).get("verification") or {}
    return {
        "case_id": case_id, "tenant": tenant_id, "state": case["state"], "workload": k["workload"], "kind": k["kind"], "service": k["service"],
        "classification": k["assessment"]["classification"], "assessment_reasons": [str(r)[:240] for r in k["assessment"]["reasons"]][:5],
        "spend": {"status": sig["status"], "actual": sig["actual"], "expected": sig["expected"], "currency": sig["currency"], "anomaly_score": sig["anomaly_score"],
                  "window_days": 3, "history_days": 28},
        "owner": {"name": attr["owner"], "confidence": attr["confidence"], "status": attr["status"], "reason": attr["reason"],
                  "evidence_sources": sorted({e["source"] for e in attr["evidence"]})},
        "waste_indicators": [w["summary"] for w in k["waste_indicators"]][:5], "candidates": k["candidates"],
        "risk": rec["risk"], "rollback": rec["rollback"], "policy_template": rec["template"],
        "projected_monthly": (pol or {}).get("guards", {}).get("savings", {}).get("monthly") or rec["projected_savings"]["monthly"], "currency": "USD",
        "candidate_monthly": rec["projected_savings"]["monthly"], "realized_savings": "not measured",
        "budget": (sig.get("features") or None) if k["kind"] == "budget-overrun" else None,
        "dry_run": None if not pol else {"status": pol["status"], "custodian_exit_code": pol["dryrun_exit_code"], "targets": pol["target_ids"],
                                         "excluded": [{"id": e["resource_id"], "reason": e["reason"]} for e in pol["excluded"]], "refusal_reasons": pol["refusal_reasons"][:3]},
        "approval": None if not apr else {"status": apr["status"], "decided_by": apr["actor_id"], "role": apr["actor_role"], "note": apr["note"]},
        "execution": None if not ex else {"status": ex["status"], "exit_code": ex["exit_code"], "matched_ids": ex["matched_ids"]},
        "verification": None if not ver else {"change_observed": bool(ver.get("ok")), "resources": [{"id": r["resource_id"], "tag": r["expected_tag"], "observed": r["observed"]} for r in ver.get("resources", [])]},
        "recent_events": [{"event": a["event_type"], "from": a["from_state"], "to": a["to_state"], "by": a["actor"]} for a in reversed(aud)],
    }


def template_summary(p: dict) -> str:
    """Deterministic plain-language note from the same packet: used when no LLM is configured or its output is rejected."""
    m = lambda x: f"${float(x):,.2f}"
    sp, own, s = p["spend"], p["owner"], []
    if sp["status"] == "budget_overrun" and p.get("budget"):
        b = p["budget"]
        s.append(f"{p['tenant']} spent {m(b['spent_30d_usd'])} in the last 30 days, {m(b['over_usd'])} over its {m(b['budget_usd'])} monthly budget.")
        if b["top_drivers"]:
            s.append("Fastest growth: " + ", ".join(f"{d['workload']} ({d['service']})" for d in b["top_drivers"]) + ".")
    elif sp["status"] == "anomaly":
        s.append(f"{p['service']} for {p['workload']} cost {m(sp['actual'])} in the last {sp['window_days']} days; about {m(sp['expected'])} was expected.")
    if p["waste_indicators"]:
        s.append(p["waste_indicators"][0].rstrip(".") + ".")
    s.append(f"Owner: {own['name']}." if own["name"] else "The owner could not be established.")
    d = p["dry_run"]
    if d and d["refusal_reasons"]:
        s.append("Refused: " + "; ".join(d["refusal_reasons"]) + ".")
    if float(p["projected_monthly"] or 0) > 0:
        s.append(f"Estimated cost: {m(p['projected_monthly'])} a month (an estimate, not measured).")
    v = p["verification"]
    if v:
        s.append("The change was seen in the live resource." if v["change_observed"] else "The change was NOT seen in the live resource.")
    return " ".join(s)


def _nums(text: str) -> list[float]:
    out = []
    for m in NUM_RE.findall(text):
        try:
            out.append(float(m.replace(",", "")))
        except ValueError:
            pass
    return out


def check_summary(text: str, packet: dict) -> list[str]:
    """Return reasons to reject. Empty list = every id, owner name and number in the text appears in the evidence."""
    blob, problems = json.dumps(packet, default=str), []
    if not text.strip():
        return ["empty"]
    if len(text) > 800 or len(text.split()) > 100:
        problems.append("too long (max 100 words)")
    if re.search(r"https?://|`", text):
        problems.append("contains a link or code")
    bad_ids = set(ID_RE.findall(text)) - set(ID_RE.findall(blob))
    if bad_ids:
        problems.append(f"resource id not in evidence: {sorted(bad_ids)}")
    bad_owners = {o for o in OWNER_RE.findall(text) if o not in blob}
    if bad_owners:
        problems.append(f"owner/team not in evidence: {sorted(bad_owners)}")
    strip = lambda s: DATE_RE.sub(" ", ID_RE.sub(" ", s))
    allowed = _nums(strip(blob))
    for n in _nums(strip(text)):
        if not any(abs(n - a) <= 0.5 for a in allowed):          # 0.5 only forgives rounding ("$420" for 420.0000)
            problems.append(f"number not in evidence: {n}")
    return problems


def _cohere(packet: dict) -> str:
    r = httpx.post((config.LLM_BASE_URL or "https://api.cohere.com") + "/v2/chat", timeout=config.LLM_TIMEOUT_SECONDS,
                   headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
                   json={"model": config.LLM_MODEL or "command-a-03-2025", "temperature": 0, "max_tokens": 350,
                         "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": json.dumps(packet, default=str)}]})
    r.raise_for_status()
    return "".join(p.get("text", "") for p in r.json()["message"]["content"] if p.get("type") == "text")


def _anthropic(packet: dict) -> str:
    r = httpx.post((config.LLM_BASE_URL or "https://api.anthropic.com") + "/v1/messages", timeout=config.LLM_TIMEOUT_SECONDS,
                   headers={"x-api-key": config.LLM_API_KEY, "anthropic-version": "2023-06-01"},
                   json={"model": config.LLM_MODEL or "claude-haiku-4-5-20251001", "temperature": 0, "max_tokens": 350, "system": SYSTEM,
                         "messages": [{"role": "user", "content": json.dumps(packet, default=str)}]})
    r.raise_for_status()
    return "".join(b.get("text", "") for b in r.json()["content"] if b.get("type") == "text")


def _alibaba(packet: dict) -> str:
    """Alibaba Cloud Model Studio (Qwen) via its OpenAI-compatible endpoint. Default region: international (Singapore)."""
    r = httpx.post((config.LLM_BASE_URL or "https://dashscope-intl.aliyuncs.com/compatible-mode/v1") + "/chat/completions", timeout=config.LLM_TIMEOUT_SECONDS,
                   headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
                   json={"model": config.LLM_MODEL or "qwen-plus", "temperature": 0, "max_tokens": 350,
                         "enable_thinking": False,   # Qwen3.x thinks by default: ~25x tokens and ~9x slower for no benefit when only phrasing verified facts
                         "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": json.dumps(packet, default=str)}]})
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"] or ""


CALLERS = {"cohere": _cohere, "anthropic": _anthropic, "alibaba": _alibaba}


def summarize(packet: dict) -> dict:
    base = {"summary": template_summary(packet), "source": "template", "model": None}
    if config.LLM_PROVIDER not in CALLERS or not config.LLM_API_KEY:
        return {**base, "reason": "no LLM configured"}
    try:
        text = CALLERS[config.LLM_PROVIDER](packet).strip()
    except Exception as e:                                      # timeout, 4xx/5xx, bad shape: never block a notification
        return {**base, "reason": f"LLM unavailable ({type(e).__name__})"}
    problems = check_summary(text, packet)
    if problems:
        return {**base, "reason": "LLM output rejected: " + "; ".join(problems[:3])}
    return {"summary": text, "source": "llm", "model": config.LLM_MODEL or config.LLM_PROVIDER, "reason": None}
