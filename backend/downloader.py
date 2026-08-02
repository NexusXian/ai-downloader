"""The only module that touches yt_dlp.

Uses yt-dlp as a Python library rather than shelling out to the CLI. Every known
yt-dlp CVE (CVE-2023-40581, CVE-2024-22423, CVE-2025-54072) lives in shell-out
options like --exec/--netrc-cmd; passing an options dict means no shell string
exists for a hostile URL to escape, and we never set those options.

We wrap upstream rather than forking it: extractors change weekly, so a fork would
mean permanent merge conflicts. Upgrades are just `pip install -U yt-dlp`.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Callable

import yt_dlp

import config
from security import safe_ext

log = logging.getLogger(__name__)

# Resolutions we offer in the picker, coarse enough to keep the UI legible.
QUALITY_LADDER = [2160, 1440, 1080, 720, 480, 360]


class ProbeError(RuntimeError):
    """Metadata extraction failed. Message is safe to show the user."""


def _base_opts() -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        # quiet alone does NOT silence the progress bar -- verified: it keeps
        # writing "[download] 12.3% of ..." to stderr. noprogress is required.
        "noprogress": True,
        # A pasted playlist/channel URL must download one video, not hundreds.
        "noplaylist": True,
        "socket_timeout": config.SOCKET_TIMEOUT,
        "retries": 3,
        "fragment_retries": 5,
        "ignoreerrors": False,
    }
    if config.COOKIE_FILE and Path(config.COOKIE_FILE).is_file():
        # Netscape format only; yt-dlp rejects JSON cookie exports.
        opts["cookiefile"] = config.COOKIE_FILE
    return opts


def build_format_selector(max_height: int, prefer_compatible: bool = True) -> str:
    """Build a format selector, preferring H.264/AAC for playability.

    This matters more than it looks. yt-dlp's default (bv*+ba/b) selected AV1 video
    + Opus audio on YouTube -- verified with ffprobe. That muxes into a valid .mp4
    that iOS Safari cannot reliably play, so the download "succeeds" and the user
    gets a file that won't open on their phone. The cascade below asks for
    avc1+mp4a first and only then falls back, which yields h264+aac (verified).

    The `+` merge requires ffmpeg. Without it yt-dlp silently falls through to the
    single progressive format, which on YouTube is 360p -- of 31 formats up to
    2160p on a test video, exactly one was pre-muxed, at 360p.
    """
    h = max_height
    if not prefer_compatible:
        return f"bv*[height<={h}]+ba/b[height<={h}]/bv*+ba/b"
    return (
        # 1. ideal: H.264 video + AAC audio, within the height cap
        f"bv*[vcodec^=avc1][height<={h}]+ba[acodec^=mp4a]/"
        # 2. H.264 video + any audio
        f"bv*[vcodec^=avc1][height<={h}]+ba/"
        # 3. pre-muxed H.264
        f"b[vcodec^=avc1][height<={h}]/"
        # 4. any codec within the cap (may be AV1 -- acceptable last resort)
        f"bv*[height<={h}]+ba/b[height<={h}]/"
        # 5. absolute fallback so a download never fails purely on selection
        f"bv*+ba/b"
    )


# --- probe -------------------------------------------------------------------

def _shape_formats(info: dict[str, Any], max_height: int) -> list[dict[str, Any]]:
    """Collapse yt-dlp's format list into distinct quality options for the UI.

    yt-dlp reports dozens of formats per video (codec x container x bitrate). Users
    want to choose a resolution, so we keep the best candidate per height and
    surface whether a compatible H.264 path exists.
    """
    raw = info.get("formats") or []
    by_height: dict[int, dict[str, Any]] = {}

    for f in raw:
        height = f.get("height")
        if not height or f.get("vcodec") in (None, "none"):
            continue
        if height > max_height:
            continue
        vcodec = (f.get("vcodec") or "").lower()
        entry = by_height.setdefault(height, {
            "height": height,
            "has_h264": False,
            "ext": "mp4",
            "filesize": None,
            "fps": None,
            "drm": False,
        })
        if vcodec.startswith("avc1") or vcodec.startswith("h264"):
            entry["has_h264"] = True
        if f.get("has_drm"):
            entry["drm"] = True
        size = f.get("filesize") or f.get("filesize_approx")
        if size and (entry["filesize"] is None or size > entry["filesize"]):
            entry["filesize"] = size
        if f.get("fps") and (entry["fps"] is None or f["fps"] > entry["fps"]):
            entry["fps"] = f["fps"]

    options = []
    for height in sorted(by_height, reverse=True):
        e = by_height[height]
        label = f"{height}p"
        if e["fps"] and e["fps"] >= 50:
            label += f"{int(e['fps'])}"
        options.append({
            "height": height,
            "label": label,
            "ext": e["ext"],
            # Audio is merged separately, so a video-only size understates the
            # result. Marked estimated so the UI can hedge the wording.
            "filesize": e["filesize"],
            "filesize_estimated": True,
            "compatible": e["has_h264"],
            "drm": e["drm"],
        })
    return options


def probe(url: str) -> dict[str, Any]:
    """Fetch metadata without downloading. Raises ProbeError with a usable message."""
    opts = _base_opts() | {"skip_download": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.sanitize_info(ydl.extract_info(url, download=False))
    except yt_dlp.utils.DownloadError as exc:
        raise ProbeError(_friendly_error(str(exc))) from exc
    except Exception as exc:  # noqa: BLE001 - extractor internals vary widely
        log.exception("probe failed")
        raise ProbeError(_friendly_error(str(exc))) from exc

    if not info:
        raise ProbeError("未能解析该链接")

    # A playlist URL with noplaylist still yields a playlist dict on some sites.
    if info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if e]
        if not entries:
            raise ProbeError("该链接是播放列表，未找到可下载视频")
        info = entries[0]

    duration = info.get("duration")
    if duration and duration > config.MAX_DURATION_SECONDS:
        raise ProbeError(
            f"视频时长超过限制（{int(duration // 60)} 分钟，"
            f"上限 {config.MAX_DURATION_SECONDS // 60} 分钟）"
        )

    subs = info.get("subtitles") or {}
    autos = info.get("automatic_captions") or {}

    # Some extractors (Bilibili among them) return http:// thumbnail URLs, which a
    # browser blocks as mixed content once the site itself is served over HTTPS.
    thumbnail = info.get("thumbnail")
    if thumbnail and thumbnail.startswith("http://"):
        thumbnail = "https://" + thumbnail[len("http://"):]

    all_formats = info.get("formats") or []
    # 'maybe' is a real value here, not just True/False.
    drm_only = bool(all_formats) and all(f.get("has_drm") for f in all_formats)
    if drm_only:
        raise ProbeError("该视频受 DRM 保护，无法下载")

    return {
        "url": info.get("webpage_url") or url,
        "title": info.get("title") or "未命名视频",
        "duration": duration,
        "thumbnail": thumbnail,
        "uploader": info.get("uploader") or info.get("channel"),
        "extractor": info.get("extractor_key") or info.get("extractor"),
        "is_live": bool(info.get("is_live")),
        "live_status": info.get("live_status"),
        "formats": _shape_formats(info, config.PRO_MAX_HEIGHT),
        "subtitles": sorted(subs.keys()),
        "auto_subtitles": sorted(autos.keys()),
        "has_subtitles": bool(subs or autos),
    }


def _friendly_error(raw: str) -> str:
    """Translate yt-dlp's error text into something a student can act on.

    DRM and "unavailable" are surfaced rather than papered over -- yt-dlp refuses
    DRM by design and there is no workaround to attempt.
    """
    low = raw.lower()
    if "drm" in low:
        return "该视频受 DRM 保护，无法下载"
    if "sign in to confirm" in low or "not a bot" in low:
        return "平台要求登录验证，请稍后重试或配置 cookies 文件"
    if "private video" in low or "login required" in low or "members-only" in low:
        return "该视频需要登录或为私密视频，无法下载"
    if "video unavailable" in low or "removed" in low:
        return "视频不存在或已被删除"
    if "unsupported url" in low or "no suitable" in low:
        return "暂不支持该网站的链接"
    if "geo" in low and "restrict" in low:
        return "该视频有地区限制"
    if "timed out" in low or "timeout" in low:
        return "连接超时，请检查网络后重试"
    # Strip yt-dlp's "ERROR: [extractor] id:" prefix noise for readability.
    cleaned = raw.split(";")[0].replace("ERROR: ", "").strip()
    return cleaned[:200] or "解析失败"


# --- download ----------------------------------------------------------------

ProgressFn = Callable[[dict[str, Any]], None]


def _hooks(on_progress: ProgressFn) -> dict[str, list[Callable]]:
    """Wire yt-dlp's hooks into a single callback with normalized keys.

    Only raw numeric keys are forwarded. The _percent_str/_speed_str keys are
    terminal-formatted strings, and hook info_dict is unsanitized (it can hold
    non-serializable objects), so neither is passed through.
    """
    def progress_hook(d: dict[str, Any]) -> None:
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            on_progress({
                "phase": "downloading",
                "downloaded_bytes": d.get("downloaded_bytes") or 0,
                "total_bytes": total,
                "speed": d.get("speed"),
                "eta": d.get("eta"),
                "fragment_index": d.get("fragment_index"),
                "fragment_count": d.get("fragment_count"),
            })
        elif status == "finished":
            on_progress({"phase": "downloaded"})
        elif status == "error":
            on_progress({"phase": "error"})

    def pp_hook(d: dict[str, Any]) -> None:
        # Verified sequence on a merged download: Merger started/finished, then
        # MoveFiles. Merging a 4K stream is slow enough that showing a real
        # "merging" state beats a progress bar that appears stuck at 100%.
        name = (d.get("postprocessor") or "").lower()
        if d.get("status") == "started" and "merger" in name:
            on_progress({"phase": "merging"})
        elif d.get("status") == "started" and "extractaudio" in name:
            on_progress({"phase": "converting"})

    return {"progress_hooks": [progress_hook], "postprocessor_hooks": [pp_hook]}


def download_video(
    url: str, out_stem: Path, max_height: int, on_progress: ProgressFn
) -> Path:
    """Download and mux a video. Returns the produced file path."""
    opts = _base_opts() | _hooks(on_progress) | {
        "format": build_format_selector(max_height),
        "merge_output_format": "mp4",
        "outtmpl": f"{out_stem}.%(ext)s",
        "max_filesize": config.MAX_FILESIZE_BYTES,
    }
    return _run(url, opts, out_stem, default_ext="mp4")


def download_audio(url: str, out_stem: Path, on_progress: ProgressFn) -> Path:
    """Extract audio as MP3 via ffmpeg. Verified to produce a real mp3 stream."""
    opts = _base_opts() | _hooks(on_progress) | {
        "format": "bestaudio/best",
        "outtmpl": f"{out_stem}.%(ext)s",
        "max_filesize": config.MAX_FILESIZE_BYTES,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
    }
    return _run(url, opts, out_stem, default_ext="mp3")


def download_subtitle(
    url: str, out_stem: Path, lang: str, on_progress: ProgressFn
) -> Path:
    """Download one subtitle track as SRT (falling back to the site's format)."""
    opts = _base_opts() | _hooks(on_progress) | {
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": [lang],
        "subtitlesformat": "srt/vtt/best",
        "outtmpl": f"{out_stem}.%(ext)s",
    }
    return _run(url, opts, out_stem, default_ext="srt", subtitle=True)


# Transient signals worth one more attempt. Observed in testing: YouTube returned
# "HTTP Error 403: Forbidden" mid-download and the immediate retry succeeded, so
# surfacing the first failure to the user would report a dead end that isn't one.
_RETRYABLE = ("403", "forbidden", "timed out", "timeout", "connection reset",
              "temporarily unavailable", "read operation")


def _is_retryable(message: str) -> bool:
    low = message.lower()
    # Permanent conditions: retrying cannot help and just delays the error.
    if "drm" in low or "private" in low:
        return False
    if "unavailable" in low and "temporarily" not in low:
        return False
    return any(token in low for token in _RETRYABLE)


def _run(
    url: str, opts: dict[str, Any], out_stem: Path, *, default_ext: str,
    subtitle: bool = False,
) -> Path:
    attempts = 2
    for attempt in range(1, attempts + 1):
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.extract_info(url, download=True)
            break
        except yt_dlp.utils.DownloadError as exc:
            message = str(exc)
            if attempt < attempts and _is_retryable(message):
                log.warning("retrying after transient error: %s", message[:120])
                # Clear partial artifacts so the retry starts clean.
                for leftover in out_stem.parent.glob(f"{out_stem.name}*"):
                    leftover.unlink(missing_ok=True)
                time.sleep(2)
                continue
            raise ProbeError(_friendly_error(message)) from exc
        except Exception as exc:  # noqa: BLE001
            log.exception("download failed")
            raise ProbeError(_friendly_error(str(exc))) from exc

    produced = _find_output(out_stem, default_ext, subtitle=subtitle)
    if produced is None:
        raise ProbeError("下载已完成但未找到输出文件")
    return produced


def _find_output(out_stem: Path, default_ext: str, *, subtitle: bool) -> Path | None:
    """Locate what yt-dlp actually wrote.

    The output name is derived from our own job-scoped stem, never from remote
    metadata (CVE-2024-38519 was remote-metadata-derived filenames), but the
    extension is chosen by yt-dlp -- and subtitles get an extra .<lang> infix.
    """
    exact = out_stem.with_suffix(f".{default_ext}")
    if exact.is_file():
        return exact

    parent, stem = out_stem.parent, out_stem.name
    if not parent.is_dir():
        return None

    candidates = [
        p for p in parent.iterdir()
        if p.is_file() and p.name.startswith(stem) and not p.name.endswith(".part")
    ]
    if not candidates:
        return None

    if subtitle:
        for ext in ("srt", "vtt", "ass"):
            for p in candidates:
                if p.suffix.lstrip(".").lower() == ext:
                    return p

    # Prefer an allowlisted extension, then the largest file (the muxed result
    # outweighs leftover single-stream artifacts).
    allowed = [p for p in candidates if safe_ext(p.suffix, "") ] or candidates
    return max(allowed, key=lambda p: p.stat().st_size)
