"""Canonicalisation, hashing and HMAC helpers shared by outbox signing, callback verification and runner grants."""
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256_hex(data: str | bytes) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def target_set_hash(target_ids: list[str]) -> str:
    return sha256_hex(canonical(sorted(set(target_ids))))


def sign(secret: str, ts: int | str, body: bytes) -> str:
    return hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()


def verify(secret: str, ts: str, body: bytes, signature: str, window: int) -> bool:
    try:
        if abs(time.time() - int(ts)) > window:
            return False
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(sign(secret, ts, body), signature or "")


def hash_pin(pin: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), b"sentinel-demo-salt", 20_000).hex()


def hash_token(token: str) -> str:
    return sha256_hex(token)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
