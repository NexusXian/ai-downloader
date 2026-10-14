"""URL validation, filename safety, and HTTP header construction.

The URL guard is the highest-risk surface in this app: the core feature is "make my
server fetch a URL a stranger supplied", which is SSRF by design. Treat every
change here as security-relevant.
"""
from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import quote, urlsplit

import config


class UnsafeURLError(ValueError):
    """Raised when a submitted URL must not be fetched."""


ALLOWED_SCHEMES = frozenset({"http", "https"})

# Hostnames that must never be fetched regardless of what they resolve to.
BLOCKED_HOSTS = frozenset({
    "localhost",
    "metadata.google.internal",
    "metadata.goog",
    "instance-data",
})
BLOCKED_SUFFIXES = (".localhost", ".internal", ".local")


def _allow_nets() -> list[ipaddress._BaseNetwork]:
    nets = []
    for raw in config.ALLOW_RESOLVED_NETS.split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            nets.append(ipaddress.ip_network(raw, strict=False))
        except ValueError:
            continue
    return nets


def _normalize(ip: ipaddress._BaseAddress) -> ipaddress._BaseAddress:
    """Unwrap IPv4-mapped IPv6 so ::ffff:127.0.0.1 can't smuggle loopback past us."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        return ip.ipv4_mapped
    return ip


def _is_dangerous(ip: ipaddress._BaseAddress) -> bool:
    """Ranges that let a URL reach infrastructure instead of the public internet.

    Deliberately checks specific properties rather than `not ip.is_global`. Split
    horizon / sandboxed DNS can map legitimate public sites into ranges Python
    considers private -- this dev machine resolves youtube.com to 198.18.0.109
    (RFC 2544), where an is_global check would reject the product's own targets.
    """
    return (
        ip.is_loopback
        or ip.is_link_local     # includes 169.254.169.254 cloud metadata
        or ip.is_private
        or ip.is_multicast
        or ip.is_unspecified
    )


def validate_url(url: str, *, resolve: bool = True) -> str:
    """Return the URL if safe to hand to yt-dlp, else raise UnsafeURLError.

    Defense in depth only: yt-dlp performs its own DNS resolution and follows its
    own redirects, neither of which we can intercept. A redirect to an internal
    address, or a DNS answer that changes between this check and yt-dlp's fetch
    (TOCTOU / rebinding), is not caught here. The durable fix is network-level
    egress filtering on the box that runs the worker.
    """
    if not url or not url.strip():
        raise UnsafeURLError("请输入视频链接")

    url = url.strip()
    parts = urlsplit(url)

    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise UnsafeURLError("仅支持 http/https 链接")

    host = (parts.hostname or "").lower()
    if not host:
        raise UnsafeURLError("链接格式不正确")
    if host in BLOCKED_HOSTS or host.endswith(BLOCKED_SUFFIXES):
        raise UnsafeURLError("不允许访问内网地址")

    # A literal IP in the URL has no legitimate use here, so judge it strictly --
    # is_reserved included, which a hostname lookup can't afford (see _is_dangerous).
    try:
        literal = _normalize(ipaddress.ip_address(host))
    except ValueError:
        literal = None

    if literal is not None:
        if _is_dangerous(literal) or literal.is_reserved:
            raise UnsafeURLError("不允许访问内网或保留地址")
        return url

    if not resolve:
        return url

    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        raise UnsafeURLError("无法解析该域名，请检查链接是否正确") from None

    overrides = _allow_nets()
    for *_, sockaddr in infos:
        addr = _normalize(ipaddress.ip_address(sockaddr[0]))
        if _is_dangerous(addr) and not any(addr in net for net in overrides):
            raise UnsafeURLError("该域名指向内网地址，已阻止")

    return url


# --- filenames ---------------------------------------------------------------

# Extensions we are willing to write to disk. CVE-2024-38519 was yt-dlp deriving
# output extensions from remote metadata; an allowlist is the upstream fix and we
# keep our own rather than trusting the info dict.
SAFE_EXTS = frozenset({
    "mp4", "mkv", "webm", "mov", "m4v", "flv", "avi",
    "mp3", "m4a", "aac", "opus", "flac", "wav", "ogg",
    "srt", "vtt", "ass", "zip", "md", "json", "opml",
})

_UNSAFE_CHARS = re.compile(r'[\x00-\x1f<>:"/\\|?*]+')


def safe_ext(ext: str | None, fallback: str = "mp4") -> str:
    ext = (ext or "").lstrip(".").lower()
    return ext if ext in SAFE_EXTS else fallback


def sanitize_filename(name: str, *, max_length: int = 80) -> str:
    """Make a display filename safe. Never used to build an on-disk path.

    On-disk names are always `{job_id}.{safe_ext}`; this only produces the pretty
    name sent in Content-Disposition.
    """
    name = _UNSAFE_CHARS.sub("", name or "").strip().strip(".")
    name = re.sub(r"\s+", " ", name)
    # Loop: a single replace turns "a/../../b" into "a..b" (separators are stripped
    # above, so the dot runs collide) and leaves a traversal-looking name behind.
    while ".." in name:
        name = name.replace("..", ".")
    name = name.strip(".").strip()
    if not name:
        name = "video"
    if len(name) > max_length:
        name = name[:max_length].rstrip()
    return name


def content_disposition(filename: str, *, inline: bool = False) -> str:
    """Build a Content-Disposition value that survives Chinese filenames.

    Emits both forms per RFC 5987/6266: a plain-ASCII `filename` fallback and a
    percent-encoded `filename*`. The ASCII fallback stays genuinely plain --
    Safari mishandles percent-escapes there, so encoding it breaks the fallback.

    `inline` is for in-page playback: Chrome refuses to play a media element whose
    response is marked as an attachment.
    """
    ascii_name = filename.encode("ascii", "ignore").decode("ascii").strip()
    ascii_name = _UNSAFE_CHARS.sub("", ascii_name).strip()
    if not ascii_name or ascii_name in {".", ".."}:
        ext = filename.rsplit(".", 1)[-1] if "." in filename else "mp4"
        ascii_name = f"video.{safe_ext(ext)}"
    encoded = quote(filename, safe="")
    kind = "inline" if inline else "attachment"
    return f'{kind}; filename="{ascii_name}"; filename*=UTF-8\'\'{encoded}'


CONTENT_TYPES = {
    "mp4": "video/mp4", "mkv": "video/x-matroska", "webm": "video/webm",
    "mov": "video/quicktime", "m4v": "video/x-m4v", "flv": "video/x-flv",
    "avi": "video/x-msvideo",
    "mp3": "audio/mpeg", "m4a": "audio/mp4", "aac": "audio/aac",
    "opus": "audio/opus", "flac": "audio/flac", "wav": "audio/wav",
    "ogg": "audio/ogg",
    "srt": "application/x-subrip", "vtt": "text/vtt", "ass": "text/plain",
    "zip": "application/zip", "md": "text/markdown",
    "json": "application/json", "opml": "text/x-opml",
}


def content_type_for(ext: str) -> str:
    """Return a real MIME type, not octet-stream.

    Mobile Safari partly decides download-vs-inline-preview from Content-Type, so a
    correct type measurably improves the odds a video actually saves.
    """
    return CONTENT_TYPES.get(safe_ext(ext), "application/octet-stream")
