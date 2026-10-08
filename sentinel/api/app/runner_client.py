"""Signed client for the isolated Custodian runner. The API holds no cloud credentials; the runner does."""
import time

import httpx

from . import config
from .security import canonical, sign


def call(path: str, body: dict, timeout: float = 180.0) -> dict:
    raw = canonical(body).encode()
    ts = str(int(time.time()))
    r = httpx.post(config.RUNNER_URL + path, content=raw, timeout=timeout,
                   headers={"content-type": "application/json", "x-sentinel-timestamp": ts,
                            "x-sentinel-signature": sign(config.RUNNER_HMAC_SECRET, ts, raw)})
    r.raise_for_status()
    return r.json()


def health() -> dict:
    return httpx.get(config.RUNNER_URL + "/health", timeout=5).json()
