"""Security tests. These are the cases that matter most in the whole suite."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import security  # noqa: E402
from security import (  # noqa: E402
    UnsafeURLError,
    content_disposition,
    content_type_for,
    safe_ext,
    sanitize_filename,
    validate_url,
)


# Real platform URLs must be accepted. This sandbox resolves them into
# 198.18.0.0/15, which Python calls private, so these double as a regression test
# against an over-strict guard silently breaking the product.
ALLOWED = [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://www.bilibili.com/video/BV1GJ411x7h7",
    "https://v.douyin.com/abcdef/",
    "https://x.com/i/status/1",
    "http://vimeo.com/1084537",
]

BLOCKED = [
    "file:///etc/passwd",
    "ftp://example.com/x",
    "data:text/html,<script>alert(1)</script>",
    "http://localhost/admin",
    "http://LOCALHOST:8000/admin",
    "http://foo.internal/",
    "http://box.local/",
    "http://127.0.0.1/",
    "http://127.1/",
    "http://169.254.169.254/latest/meta-data/",
    "http://192.168.1.1/",
    "http://10.0.0.5/",
    "http://172.16.0.1/",
    "http://0.0.0.0/",
    "http://[::1]/",
    "http://[::ffff:127.0.0.1]/",
    "http://metadata.google.internal/",
    "",
    "   ",
]


@pytest.fixture(autouse=True)
def _sandbox_override(monkeypatch):
    """Mirror the documented dev-network override so ALLOWED can resolve."""
    monkeypatch.setattr(security.config, "ALLOW_RESOLVED_NETS", "198.18.0.0/15")


@pytest.mark.parametrize("url", ALLOWED)
def test_real_platform_urls_allowed(url):
    assert validate_url(url) == url.strip()


@pytest.mark.parametrize("url", BLOCKED)
def test_ssrf_vectors_blocked(url):
    with pytest.raises(UnsafeURLError):
        validate_url(url)


def test_ipv4_mapped_ipv6_loopback_blocked():
    """::ffff:127.0.0.1 must not smuggle loopback past the guard."""
    with pytest.raises(UnsafeURLError):
        validate_url("http://[::ffff:127.0.0.1]/")


def test_public_literal_ip_allowed():
    assert validate_url("http://93.184.216.34/", resolve=False)


def test_reserved_literal_ip_blocked():
    # 198.18/15 is permitted for *hostnames* via the override, but a literal IP is
    # judged strictly -- the override must not weaken that path.
    with pytest.raises(UnsafeURLError):
        validate_url("http://198.18.0.109/")


def test_unresolvable_host_rejected(monkeypatch):
    """A host that genuinely fails to resolve must be rejected.

    Forcing the failure rather than relying on a bogus domain: this dev sandbox
    runs a wildcard resolver that answers *every* name (including .invalid) out of
    198.18.0.0/15, so no real hostname can exercise this path here.
    """
    def _boom(*_args, **_kwargs):
        raise OSError("nodename nor servname provided")

    monkeypatch.setattr(security.socket, "getaddrinfo", _boom)
    with pytest.raises(UnsafeURLError):
        validate_url("https://whatever.example/v")


# --- filenames ---------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    # Unsafe runs collapse to nothing rather than to a space, so "../../etc/passwd"
    # loses its separators entirely -- the traversal is gone either way.
    ("../../etc/passwd", "etcpasswd"),
    ("a/b\\c", "abc"),
    ("bad\x00null", "badnull"),
    ("con:trol*chars?", "controlchars"),
    ("", "video"),
    ("...", "video"),
    ("  spaced  out  ", "spaced out"),
])
def test_sanitize_filename(raw, expected):
    assert sanitize_filename(raw) == expected


def test_sanitize_filename_defeats_traversal():
    for raw in ["../../etc/passwd", "..\\..\\windows\\system32", "a/../../b"]:
        out = sanitize_filename(raw)
        assert "/" not in out and "\\" not in out and ".." not in out


def test_sanitize_filename_keeps_chinese():
    assert sanitize_filename("小明的视频") == "小明的视频"


def test_sanitize_filename_truncates():
    assert len(sanitize_filename("x" * 500)) <= 80


def test_sanitize_filename_strips_traversal_dots():
    assert ".." not in sanitize_filename("a..b..c")


@pytest.mark.parametrize("raw,expected", [
    ("mp4", "mp4"), (".MP4", "mp4"), ("mp3", "mp3"), ("srt", "srt"), ("md", "md"),
    ("exe", "mp4"), ("sh", "mp4"), ("conf", "mp4"), (None, "mp4"), ("", "mp4"),
])
def test_safe_ext_allowlist(raw, expected):
    """Remote metadata must never introduce an arbitrary extension (CVE-2024-38519)."""
    assert safe_ext(raw) == expected


# --- headers -----------------------------------------------------------------

def test_content_disposition_chinese_has_both_forms():
    cd = content_disposition("小明的视频.mp4")
    assert "filename=" in cd and "filename*=UTF-8''" in cd
    assert "%E5%B0%8F" in cd
    # ASCII fallback must stay plain: Safari mishandles percent-escapes there.
    fallback = cd.split('filename="')[1].split('"')[0]
    assert "%" not in fallback
    assert fallback.isascii()


def test_content_disposition_ascii_passthrough():
    cd = content_disposition("my video.mp4")
    assert 'filename="my video.mp4"' in cd


def test_content_disposition_pure_chinese_gets_usable_fallback():
    cd = content_disposition("视频.mp4")
    fallback = cd.split('filename="')[1].split('"')[0]
    assert fallback and fallback not in {".", ".."}
    assert fallback.isascii()


def test_content_disposition_no_header_injection():
    cd = content_disposition("evil\r\nX-Injected: 1.mp4")
    assert "\r" not in cd and "\n" not in cd


@pytest.mark.parametrize("ext,expected", [
    ("mp4", "video/mp4"), ("mp3", "audio/mpeg"), ("srt", "application/x-subrip"),
    ("webm", "video/webm"), ("zip", "application/zip"), ("md", "text/markdown"),
])
def test_content_type_is_specific(ext, expected):
    assert content_type_for(ext) == expected
