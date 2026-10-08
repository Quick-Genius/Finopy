"""Tenant settings: what a workspace may tune (who gets told about which outcome), never what the system may do.

Notification rules are applied where the outbox event is written (states.transition), so a muted outcome is never
queued. Failures and refusals of the safety checks are not mutable: a human must always hear about those.
"""
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import config
from .auth import HUMAN, Principal, require_roles
from .db import J, tx
from .states import audit

router = APIRouter(tags=["settings"])

MUTABLE_NOTIFY = ("REFUSED", "APPROVED", "REJECTED", "EXPIRED", "VERIFIED")        # may be muted
ALWAYS_NOTIFY = ("POLICY_INVALID", "EXECUTION_FAILED", "VERIFICATION_FAILED")      # never muted, whatever the setting says
DEFAULTS = {"notify_states": list(MUTABLE_NOTIFY), "min_notify_savings_usd": 0}
EDITOR = require_roles("reviewer")


class SettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    notify_states: list[Literal["REFUSED", "APPROVED", "REJECTED", "EXPIRED", "VERIFIED"]] | None = None
    min_notify_savings_usd: float | None = Field(default=None, ge=0, le=1_000_000)

    @field_validator("notify_states")
    @classmethod
    def _unique(cls, v):
        return None if v is None else sorted(set(v), key=MUTABLE_NOTIFY.index)


def effective(stored: dict | None) -> dict:
    """Stored overrides on top of defaults; unknown or malformed stored keys are ignored, never trusted."""
    s = dict(DEFAULTS)
    stored = stored or {}
    if isinstance(stored.get("notify_states"), list):
        s["notify_states"] = [x for x in MUTABLE_NOTIFY if x in stored["notify_states"]]
    if isinstance(stored.get("min_notify_savings_usd"), (int, float)) and stored["min_notify_savings_usd"] >= 0:
        s["min_notify_savings_usd"] = stored["min_notify_savings_usd"]
    return s


def should_notify(settings: dict, to_state: str, projected_monthly) -> bool:
    if to_state in ALWAYS_NOTIFY:
        return True
    if to_state not in settings["notify_states"]:
        return False
    return Decimal(str(projected_monthly or 0)) >= Decimal(str(settings["min_notify_savings_usd"]))


def load(conn, tenant_id: str) -> dict:
    row = conn.execute("select settings from tenant_settings where tenant_id=%s", (tenant_id,)).fetchone()
    return effective(row["settings"] if row else None)


def server_view() -> dict:
    """Read-only server limits, shown so people can see the guardrails. Authenticated, and never includes secrets."""
    return {"action_mode": config.ACTION_MODE,
            "ai_summaries": {"provider": config.LLM_PROVIDER or None, "enabled": bool(config.LLM_PROVIDER and config.LLM_API_KEY)},
            "approval_ttl_minutes": config.APPROVAL_TTL_SECONDS // 60, "max_targets_per_action": config.MAX_TARGETS,
            "min_attribution_confidence": config.MIN_ATTRIBUTION_CONFIDENCE, "snapshot_max_age_minutes": config.SNAPSHOT_MAX_AGE_SECONDS // 60,
            "analysis_window_days": config.WINDOW_DAYS, "history_days": config.HISTORY_DAYS}


@router.get("/settings")
def get_settings(p: Principal = Depends(HUMAN)):
    with tx(p.tenant_id) as c:
        row = c.execute("select settings, updated_by, updated_at from tenant_settings where tenant_id=%s", (p.tenant_id,)).fetchone()
    return {"tenant_id": p.tenant_id, "settings": effective(row["settings"] if row else None), "defaults": DEFAULTS,
            "options": {"notify_states": list(MUTABLE_NOTIFY), "always_notified": list(ALWAYS_NOTIFY)},
            "can_edit": p.role == "reviewer", "updated_by": row["updated_by"] if row else None, "updated_at": row["updated_at"] if row else None,
            "server": server_view()}


@router.put("/settings")
def put_settings(body: SettingsIn, p: Principal = Depends(EDITOR)):
    patch = body.model_dump(exclude_none=True)
    with tx(p.tenant_id) as c:
        row = c.execute("select settings from tenant_settings where tenant_id=%s for update", (p.tenant_id,)).fetchone()
        before = effective(row["settings"] if row else None)
        after = effective({**before, **patch})
        c.execute("""insert into tenant_settings (tenant_id, settings, updated_by) values (%s,%s,%s)
                     on conflict (tenant_id) do update set settings=excluded.settings, updated_by=excluded.updated_by, updated_at=now()""",
                  (p.tenant_id, J(after), p.principal_id))
        audit(c, p.tenant_id, None, "settings.updated", p.principal_id, detail={"before": before, "after": after})
    return {"tenant_id": p.tenant_id, "settings": after, "updated_by": p.principal_id}
