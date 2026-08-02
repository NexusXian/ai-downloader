"""Pro token and rate-limit tests. No network."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
import pro  # noqa: E402


def test_valid_code_yields_verifiable_token():
    result = pro.redeem(config.REDEEM_CODES[0])
    assert result["tier"] == "pro"
    assert pro.verify(result["token"]) is True


def test_code_is_case_and_space_insensitive():
    code = config.REDEEM_CODES[0]
    assert pro.verify(pro.redeem(f"  {code.lower()}  ")["token"]) is True


@pytest.mark.parametrize("bad", ["", "   ", "NOPE", "VIDEO-PRO-2025", "'; DROP TABLE--"])
def test_invalid_codes_rejected(bad):
    with pytest.raises(pro.RedeemError):
        pro.redeem(bad)


@pytest.mark.parametrize("bad", [None, "", "garbage", "a.b.c"])
def test_junk_tokens_rejected(bad):
    assert pro.verify(bad) is False


def test_tampered_token_rejected():
    """The signature is the whole point: an edited payload must not verify."""
    token = pro.redeem(config.REDEEM_CODES[0])["token"]
    assert pro.verify(token + "x") is False
    assert pro.verify("x" + token) is False
    head, _, tail = token.rpartition(".")
    assert pro.verify(f"{head}.{tail[::-1]}") is False


def test_token_from_a_different_secret_rejected():
    """A token minted with another key must not be accepted."""
    from itsdangerous import URLSafeTimedSerializer

    forged = URLSafeTimedSerializer("attacker-key", salt="video-downloader-pro").dumps(
        {"code": "X", "tier": "pro"}
    )
    assert pro.verify(forged) is False


def test_expired_token_rejected(monkeypatch):
    token = pro.redeem(config.REDEEM_CODES[0])["token"]
    monkeypatch.setattr(config, "PRO_TOKEN_TTL_SECONDS", -1)
    assert pro.verify(token) is False


def test_wrong_tier_rejected():
    """A validly-signed token without pro tier must not grant Pro."""
    from itsdangerous import URLSafeTimedSerializer

    token = URLSafeTimedSerializer(config.PRO_SECRET, salt="video-downloader-pro").dumps(
        {"code": "X", "tier": "free"}
    )
    assert pro.verify(token) is False


# --- limits ------------------------------------------------------------------

def test_limits_differ_by_tier():
    assert pro.limits_for(True)["max_height"] > pro.limits_for(False)["max_height"]
    assert pro.limits_for(True)["batch_limit"] > pro.limits_for(False)["batch_limit"]


@pytest.mark.parametrize("requested,is_pro,expected", [
    (2160, False, config.FREE_MAX_HEIGHT),   # free user asking for 4K gets clamped
    (2160, True, 2160),
    (480, False, 480),                        # below the cap passes through
    (480, True, 480),
    (None, False, config.FREE_MAX_HEIGHT),
    (None, True, config.PRO_MAX_HEIGHT),
    (0, False, config.FREE_MAX_HEIGHT),
    (-5, False, config.FREE_MAX_HEIGHT),
    (99999, False, config.FREE_MAX_HEIGHT),
])
def test_clamp_height(requested, is_pro, expected):
    assert pro.clamp_height(requested, is_pro) == expected


# --- rate limiter ------------------------------------------------------------

def test_rate_limiter_blocks_past_the_cap():
    rl = pro.RateLimiter(max_events=3, window_seconds=60)
    assert all(rl.check("1.2.3.4")[0] for _ in range(3))
    allowed, retry_after = rl.check("1.2.3.4")
    assert allowed is False
    assert retry_after > 0


def test_rate_limiter_is_per_key():
    rl = pro.RateLimiter(max_events=1, window_seconds=60)
    assert rl.check("a")[0] is True
    assert rl.check("b")[0] is True   # different IP unaffected
    assert rl.check("a")[0] is False


def test_rate_limiter_window_expires():
    rl = pro.RateLimiter(max_events=1, window_seconds=1)
    assert rl.check("ip")[0] is True
    assert rl.check("ip")[0] is False
    time.sleep(1.1)
    assert rl.check("ip")[0] is True
