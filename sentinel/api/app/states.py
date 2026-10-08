"""Case state machine. The API (not the UI) enforces it; each transition is compare-and-set plus an audit event."""
from .db import J

TRANSITIONS: dict[str, set[str]] = {
    "NEW": {"INVESTIGATED"},
    "INVESTIGATED": {"DRY_RUN", "REFUSED", "POLICY_INVALID", "DISMISSED"},
    "DRY_RUN": {"PENDING", "REFUSED"},
    "PENDING": {"APPROVED", "REJECTED", "EXPIRED", "INVESTIGATED"},  # INVESTIGATED = approval invalidated by a new case version
    "APPROVED": {"EXECUTED", "EXECUTION_FAILED"},
    "EXECUTED": {"VERIFIED", "VERIFICATION_FAILED"},
}
TERMINAL = {"VERIFIED", "VERIFICATION_FAILED", "REJECTED", "EXPIRED", "DISMISSED", "REFUSED", "POLICY_INVALID",
            "EXECUTION_FAILED"}


class StateConflict(Exception):
    """The case was not in the state the caller expected (concurrent change or replay)."""


class InvalidTransition(Exception):
    pass


def audit(conn, tenant_id, case_id, event_type, actor, *, from_state=None, to_state=None, version=None,
          correlation_id=None, detail=None):
    conn.execute(
        """insert into audit_events (tenant_id, case_id, event_type, actor, from_state, to_state, case_version,
                                     correlation_id, detail) values (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (tenant_id, case_id, event_type, actor, from_state, to_state, version, correlation_id, J(detail or {})),
    )


def transition(conn, tenant_id, case_id, from_state, to_state, actor, *, correlation_id=None, detail=None):
    if to_state not in TRANSITIONS.get(from_state, set()):
        raise InvalidTransition(f"{from_state} -> {to_state} is not allowed")
    row = conn.execute(
        "update cases set state=%s, updated_at=now() where case_id=%s and tenant_id=%s and state=%s "
        "returning current_version",
        (to_state, case_id, tenant_id, from_state),
    ).fetchone()
    if row is None:
        raise StateConflict(f"case {case_id} is no longer {from_state}")
    audit(conn, tenant_id, case_id, f"state.{to_state.lower()}", actor, from_state=from_state, to_state=to_state,
          version=row["current_version"], correlation_id=correlation_id, detail=detail)
    return row["current_version"]
