"""Environment-driven settings. Nothing here has a cloud default: demo mode points only at LocalStack."""
import os


def _e(key: str, default: str | None = None) -> str:
    v = os.environ.get(key, default)
    if v is None:
        raise RuntimeError(f"missing required env var {key}")
    return v


DATABASE_URL = _e("DATABASE_URL")                       # sentinel_app role (RLS applies)
OWNER_DATABASE_URL = os.environ.get("OWNER_DATABASE_URL")  # migrations + seed only
N8N_HMAC_SECRET = _e("N8N_HMAC_SECRET")                 # API <-> n8n signatures
RUNNER_HMAC_SECRET = _e("RUNNER_HMAC_SECRET")           # API -> custodian runner grants
N8N_WEBHOOK_BASE = _e("N8N_WEBHOOK_BASE", "http://n8n:5678/webhook")
RUNNER_URL = _e("RUNNER_URL", "http://runner:8080")
DEMO_MODE = os.environ.get("DEMO_MODE", "false").lower() == "true"
N8N_FORM_BASE = _e("N8N_FORM_BASE", "http://localhost:5678")

APPROVAL_TTL_SECONDS = int(_e("APPROVAL_TTL_SECONDS", "1800"))   # blueprint: 30 minutes in demo mode
SNAPSHOT_MAX_AGE_SECONDS = int(_e("SNAPSHOT_MAX_AGE_SECONDS", "3600"))
CALLBACK_REPLAY_WINDOW_SECONDS = 300
MAX_TARGETS = int(_e("MAX_TARGETS", "5"))
MIN_ATTRIBUTION_CONFIDENCE = 0.75
HISTORY_DAYS = 28
WINDOW_DAYS = 3
IDLE_DAYS = 7
IDLE_CPU_PCT = 5.0
MIN_DAILY_EXCESS_USD = 5.0
# LocalStack cannot back-date launch/create times, so the demo sets this to 0; production default is the idle window.
RESOURCE_MIN_AGE_DAYS = int(_e("RESOURCE_MIN_AGE_DAYS", "7"))

# Optional LLM used ONLY to phrase verified evidence for Slack. Empty provider = deterministic template summaries.
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "").strip().lower()      # "" | alibaba | cohere | anthropic
LLM_MODEL = os.environ.get("LLM_MODEL", "").strip()
LLM_API_KEY = os.environ.get("LLM_API_KEY", "").strip()
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "").strip().rstrip("/")  # tests point this at a local stand-in
LLM_TIMEOUT_SECONDS = 20

# alert_only (default): the system investigates and alerts; people decide AND fix. An approval is only recorded.
# tag_after_approval: after a named human approves, the isolated Custodian runner may apply ONE reversible tag.
ACTION_MODE = os.environ.get("ACTION_MODE", "alert_only").strip().lower()
assert ACTION_MODE in ("alert_only", "tag_after_approval"), "ACTION_MODE must be alert_only or tag_after_approval"
