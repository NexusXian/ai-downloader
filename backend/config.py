"""Environment-driven settings and limits.

Everything tunable lives here so operators can adjust caps without touching logic.
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


def _bool(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


# --- storage -----------------------------------------------------------------
# Dedicated dir, not bare /tmp: the TTL sweeper deletes everything it owns, so it
# must not share a directory with other processes' files.
JOB_DIR = Path(os.getenv("JOB_DIR") or (Path(__file__).parent / "_jobs")).resolve()
SUMMARY_JOB_DIR = Path(
    os.getenv("SUMMARY_JOB_DIR") or (Path(__file__).parent / "_summary_jobs")
).resolve()

# How long a finished file stays downloadable before the sweeper reclaims it.
JOB_TTL_SECONDS = _int("JOB_TTL_SECONDS", 3600)
SUMMARY_JOB_TTL_SECONDS = _int("SUMMARY_JOB_TTL_SECONDS", 3600)
SWEEP_INTERVAL_SECONDS = _int("SWEEP_INTERVAL_SECONDS", 300)

# --- concurrency -------------------------------------------------------------
# Dedicated pool for blocking yt-dlp work. NOT FastAPI BackgroundTasks: sync work
# there blocks the event loop, and anyio's default thread limiter is 40 tokens
# shared with all other sync request handling.
MAX_WORKERS = _int("MAX_WORKERS", 4)
AI_MAX_WORKERS = max(1, _int("AI_MAX_WORKERS", 1))
AI_MAX_QUEUED_JOBS = max(1, _int("AI_MAX_QUEUED_JOBS", 10))

# --- limits ------------------------------------------------------------------
MAX_FILESIZE_BYTES = _int("MAX_FILESIZE_MB", 2048) * 1024 * 1024
MAX_DURATION_SECONDS = _int("MAX_DURATION_SECONDS", 4 * 3600)
MIN_FREE_DISK_BYTES = _int("MIN_FREE_DISK_MB", 3072) * 1024 * 1024
SOCKET_TIMEOUT = _int("SOCKET_TIMEOUT", 30)

FREE_MAX_HEIGHT = _int("FREE_MAX_HEIGHT", 720)
PRO_MAX_HEIGHT = _int("PRO_MAX_HEIGHT", 2160)
FREE_BATCH_LIMIT = _int("FREE_BATCH_LIMIT", 1)
PRO_BATCH_LIMIT = _int("PRO_BATCH_LIMIT", 20)

# Per-IP rate limit on job creation. Each job costs real bandwidth, CPU and disk,
# so this is load-bearing rather than polish.
RATE_LIMIT_MAX = _int("RATE_LIMIT_MAX", 20)
RATE_LIMIT_WINDOW_SECONDS = _int("RATE_LIMIT_WINDOW_SECONDS", 300)

# --- security ----------------------------------------------------------------
# Comma-separated CIDRs that may be used even though Python classifies them as
# private/reserved. Needed only in odd network setups: this dev sandbox resolves
# youtube.com into 198.18.0.0/15 (RFC 2544), which ipaddress reports as private,
# so a strict guard would reject the very sites the product targets.
# LEAVE THIS UNSET IN PRODUCTION.
ALLOW_RESOLVED_NETS = os.getenv("ALLOW_RESOLVED_NETS", "")

# Signing key for Pro tokens. Ephemeral by default, which means a restart
# invalidates issued tokens; set PRO_SECRET to keep them valid across restarts.
PRO_SECRET = os.getenv("PRO_SECRET") or secrets.token_urlsafe(32)
PRO_TOKEN_TTL_SECONDS = _int("PRO_TOKEN_TTL_DAYS", 30) * 86400

# Demo redeem codes. A real product validates against a payment provider; with no
# database this is the honest stand-in.
REDEEM_CODES = [
    c.strip().upper()
    for c in (os.getenv("REDEEM_CODES") or "VIDEO-PRO-2026,STUDENT-VIP,KIRO-UNLOCK").split(",")
    if c.strip()
]

# --- integrations ------------------------------------------------------------
ANTHROPIC_API_KEY = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
AI_MODEL = os.getenv("AI_MODEL", "claude-sonnet-5")
AI_BASE_URL = (os.getenv("AI_BASE_URL") or "").strip()
AI_ENABLED = bool(ANTHROPIC_API_KEY)
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small").strip() or "small"
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu").strip() or "cpu"
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8").strip() or "int8"

# Optional Netscape-format cookies.txt for sites that demand a login. JSON cookie
# exports are rejected by yt-dlp with a confusing error, so the format matters.
COOKIE_FILE = (os.getenv("COOKIE_FILE") or "").strip()

CORS_ORIGINS = [
    o.strip()
    for o in (os.getenv("CORS_ORIGINS") or "http://localhost:3000,http://127.0.0.1:3000").split(",")
    if o.strip()
]

DEBUG = _bool("DEBUG", False)
