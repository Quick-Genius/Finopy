"""Authentication: opaque bearer token -> principal row. Tenant and role come from that row only.

A tenant_id that appears in a request body or query string is never consulted for authorization.
The one exception is the automation principal (n8n), whose *server-side* scope lists the tenants it may name,
and which can only reach a case by the tenant stored on the case row itself.
"""
from dataclasses import dataclass, field

from fastapi import Depends, Header, HTTPException

from .db import tx
from .security import hash_token


@dataclass(frozen=True)
class Principal:
    principal_id: str
    tenant_id: str | None
    role: str
    display_name: str
    scope: tuple = field(default_factory=tuple)

    @property
    def is_automation(self) -> bool:
        return self.role == "automation"


def current_principal(authorization: str | None = Header(default=None)) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    with tx(system=True) as c:
        row = c.execute("select * from principals where token_sha256=%s", (hash_token(token),)).fetchone()
    if not row:
        raise HTTPException(401, "invalid token")
    return Principal(row["principal_id"], row["tenant_id"], row["role"], row["display_name"], tuple(row["scope"]))


def require_roles(*roles: str):
    def dep(p: Principal = Depends(current_principal)) -> Principal:
        if p.role not in roles:
            raise HTTPException(403, f"role {p.role} may not do this")
        return p
    return dep


ANY_ROLE = require_roles("analyst", "approver", "reviewer", "finance", "automation")
HUMAN = require_roles("analyst", "approver", "reviewer", "finance")


def tenant_for_case(p: Principal, case_id: str) -> str:
    """Resolve which tenant a case-addressed request runs in.

    Human principals: always their own tenant -- a foreign case_id simply does not exist for them (404, no oracle).
    Automation: the tenant recorded on the case, accepted only if inside its configured scope.
    """
    if not p.is_automation:
        return p.tenant_id
    with tx(system=True) as c:
        row = c.execute("select tenant_id from cases where case_id=%s", (case_id,)).fetchone()
    if not row or row["tenant_id"] not in p.scope:
        raise HTTPException(404, "case not found")
    return row["tenant_id"]
