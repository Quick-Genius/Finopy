"""Assessment (legitimate / probable_waste / needs_review) and the allow-listed action intent.

The intent schema is the only thing a model could ever propose. Unknown actions, extra fields, or anything that
is not a plain resource ID are rejected by validation, so model-authored YAML/shell/Terraform/SQL has no entry point.
"""
import re
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

RID = re.compile(r"^(i|vol)-[0-9a-f]{8,17}$")
SAFETY = {"low": 1.0, "medium": 0.7, "high": 0.3}


class IntentParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    days: int = Field(default=2, ge=1, le=7)


class ActionIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template: Literal["idle-ec2-v1", "unattached-ebs-v1"]
    action: Literal["mark-for-stop", "tag-for-review"]
    target_ids: list[str] = Field(min_length=1, max_length=50)
    params: IntentParams = IntentParams()

    @field_validator("target_ids")
    @classmethod
    def _ids(cls, v):
        for i in v:
            if not RID.match(i):
                raise ValueError(f"not a resource id: {i!r}")
        return sorted(set(v))

    def model_post_init(self, _):
        if (self.template, self.action) not in {("idle-ec2-v1", "mark-for-stop"), ("unattached-ebs-v1", "tag-for-review")}:
            raise ValueError("template/action pair is not allow-listed")


def assess(*, kind: str, spend: dict | None, wastes: list[dict], attribution: dict, events: list[dict],
           window_end: date, window_days: int) -> dict:
    reasons: list[str] = []
    ws = window_end - timedelta(days=window_days)

    # 1. a known event that explains the spend -> legitimate
    covering = [e for e in events if e["start_date"] < window_end and e["end_date"] >= ws]
    if spend and spend["status"] == "anomaly" and covering and not wastes:
        ev = covering[0]
        allowed = spend["expected"] * (Decimal(1) + Decimal(ev["expected_uplift_pct"]) / 100) * Decimal("1.25")
        if spend["actual"] <= allowed:
            return {"classification": "legitimate", "confidence": 0.9,
                    "reasons": [f"spend increase is within the uplift expected for '{ev['description']}' "
                                f"({ev['expected_uplift_pct']}% planned, {ev['start_date']}..{ev['end_date']})"]}
        return {"classification": "needs_review", "confidence": 0.5,
                "reasons": [f"spend exceeds what event '{ev['description']}' explains (allowed up to {allowed:.2f})"]}

    if attribution["status"] != "attributed":
        return {"classification": "needs_review", "confidence": 0.3,
                "reasons": [f"owner not established: {attribution['reason']}"] + ([f"{len(wastes)} waste indicator(s) present"] if wastes else [])}

    if wastes:
        for w in wastes:
            reasons.append(w["summary"])
        if spend and spend["status"] == "anomaly":
            reasons.append("spend deviation is material and no known event covers it")
        return {"classification": "probable_waste", "confidence": 0.85 if spend and spend["status"] == "anomaly" else 0.75,
                "reasons": reasons}

    if spend and spend["status"] == "anomaly":
        return {"classification": "needs_review", "confidence": 0.4,
                "reasons": ["material spend increase with no known event and no waste indicator"]}
    return {"classification": "needs_review", "confidence": 0.0, "reasons": ["no usable signal"]}


def propose_intent(kind: str, candidate_ids: list[str]) -> ActionIntent:
    if kind == "idle-ec2":
        return ActionIntent(template="idle-ec2-v1", action="mark-for-stop", target_ids=candidate_ids)
    if kind == "unattached-ebs":
        return ActionIntent(template="unattached-ebs-v1", action="tag-for-review", target_ids=candidate_ids)
    raise ValueError(f"no allow-listed intent for kind {kind}")


def priority(saving: Decimal, anomaly_conf: float, attribution_conf: float, risk: str) -> float:
    """blueprint: expected saving x anomaly confidence x attribution confidence x safety factor"""
    return round(float(saving) * anomaly_conf * attribution_conf * SAFETY[risk], 2)
