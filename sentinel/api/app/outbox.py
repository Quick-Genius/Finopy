"""Outbox dispatcher: at-least-once, signed, retried with backoff. n8n deduplicates on event_id."""
import logging
import time

import httpx

from . import config
from .db import tx
from .security import canonical, sign

log = logging.getLogger("sentinel.outbox")
ROUTES = {"case.created": "sentinel-case", "case.updated": "sentinel-case", "approval.requested": "sentinel-approval",
          "case.state_changed": "sentinel-notify"}


def deliver_due(limit: int = 10) -> int:
    n = 0
    with tx(system=True) as c:
        rows = c.execute("select * from outbox where delivered_at is null and next_attempt_at <= now() "
                         "order by created_at limit %s for update skip locked", (limit,)).fetchall()
        for r in rows:
            body = canonical(r["payload"]).encode()
            ts = str(int(time.time()))
            try:
                resp = httpx.post(f"{config.N8N_WEBHOOK_BASE}/{ROUTES[r['event_type']]}", content=body, timeout=15,
                                  headers={"content-type": "application/json", "x-sentinel-timestamp": ts, "x-sentinel-event-id": r["event_id"],
                                           "x-sentinel-signature": sign(config.N8N_HMAC_SECRET, ts, body)})
                resp.raise_for_status()
                c.execute("update outbox set delivered_at=now(), attempts=attempts+1, last_error=null where event_id=%s", (r["event_id"],))
                n += 1
            except Exception as e:
                delay = min(2 ** (r["attempts"] + 1), 120)
                c.execute("update outbox set attempts=attempts+1, last_error=%s, next_attempt_at=now() + make_interval(secs => %s) where event_id=%s",
                          (f"{type(e).__name__}: {str(e)[:200]}", delay, r["event_id"]))
    return n


def loop() -> None:
    while True:
        try:
            deliver_due()
        except Exception:
            log.exception("outbox loop error")
        time.sleep(2)
