"""AI video summary and subtitle translation via the Claude API.

Key-optional by design: with no ANTHROPIC_API_KEY the endpoints report an
unconfigured state and the UI shows the feature as locked rather than broken. The
key is read from backend/.env and belongs to the operator.
"""
from __future__ import annotations

import logging
import re
from typing import Any

import yt_dlp

import config
from downloader import _base_opts, _friendly_error

log = logging.getLogger(__name__)

# Guard against sending a feature-length transcript in one request.
MAX_TRANSCRIPT_CHARS = 60_000


class AIUnavailable(RuntimeError):
    """AI is not configured. Message is user-facing."""


class AIError(RuntimeError):
    """The AI call failed. Message is user-facing."""


def available() -> bool:
    return config.AI_ENABLED


def _client() -> Any:
    if not config.AI_ENABLED:
        raise AIUnavailable("未配置 AI 密钥，该功能暂不可用")
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover
        raise AIUnavailable("未安装 anthropic SDK") from exc
    return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


# --- transcript --------------------------------------------------------------

_VTT_TAG = re.compile(r"<[^>]+>")
_TIMECODE = re.compile(r"^\d{1,2}:\d{2}:\d{2}[.,]\d{3}\s*-->")


def strip_subtitle_markup(raw: str) -> str:
    """Reduce SRT/VTT to plain prose, dropping indices, timecodes and dupes."""
    lines: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line in {"WEBVTT"} or line.isdigit():
            continue
        if _TIMECODE.match(line) or "-->" in line:
            continue
        if line.startswith(("Kind:", "Language:", "NOTE")):
            continue
        line = _VTT_TAG.sub("", line).strip()
        # YouTube auto-captions repeat each line as the rolling window advances.
        if line and (not lines or lines[-1] != line):
            lines.append(line)
    return "\n".join(lines)


def fetch_transcript(url: str, lang: str | None = None) -> tuple[str, str]:
    """Return (language, plain transcript) without writing files to disk.

    Reads subtitle URLs straight off a download=False probe, so nothing lands on
    disk for a summary request.
    """
    opts = _base_opts() | {"skip_download": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.sanitize_info(ydl.extract_info(url, download=False))
    except Exception as exc:  # noqa: BLE001
        raise AIError(_friendly_error(str(exc))) from exc

    if not info:
        raise AIError("未能解析该链接")

    subs = info.get("subtitles") or {}
    autos = info.get("automatic_captions") or {}
    pool = {**autos, **subs}  # human-authored wins over ASR
    if not pool:
        raise AIError("该视频没有字幕，无法生成总结")

    if lang and lang in pool:
        chosen = lang
    else:
        chosen = next(
            (c for c in ("zh-Hans", "zh-CN", "zh", "en", "en-US") if c in pool),
            next(iter(pool)),
        )

    tracks = pool.get(chosen) or []
    track = next(
        (t for t in tracks if (t.get("ext") or "").lower() in {"vtt", "srt", "srv1", "json3"}),
        tracks[0] if tracks else None,
    )
    if not track:
        raise AIError("字幕轨道不可用")

    if track.get("data"):
        raw = track["data"]
    else:
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                raw = ydl.urlopen(track["url"]).read().decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001
            raise AIError("字幕下载失败") from exc

    text = strip_subtitle_markup(raw)
    if not text.strip():
        raise AIError("字幕内容为空")
    return chosen, text[:MAX_TRANSCRIPT_CHARS]


# --- features ----------------------------------------------------------------

def summarize(url: str, title: str | None = None) -> dict[str, Any]:
    client = _client()
    lang, transcript = fetch_transcript(url)

    prompt = (
        f"以下是视频《{title or '未命名'}》的字幕文本。请用简体中文输出：\n"
        "1. 一句话概括（30 字以内）\n"
        "2. 3-5 个核心要点，每点一行，以 - 开头\n"
        "3. 适合谁观看（一句话）\n\n"
        f"字幕：\n{transcript}"
    )
    try:
        resp = client.messages.create(
            model=config.AI_MODEL,
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("summary failed")
        raise AIError(f"AI 调用失败：{exc}"[:200]) from exc

    return {
        "summary": "".join(b.text for b in resp.content if b.type == "text").strip(),
        "source_lang": lang,
        "model": config.AI_MODEL,
    }


def translate_subtitles(url: str, target_lang: str = "简体中文",
                        source_lang: str | None = None) -> dict[str, Any]:
    """Translate a subtitle track, preserving SRT timing.

    Timecodes are kept verbatim so the result stays usable in a player; only the
    text lines are translated.
    """
    client = _client()
    opts = _base_opts() | {"skip_download": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.sanitize_info(ydl.extract_info(url, download=False))
    except Exception as exc:  # noqa: BLE001
        raise AIError(_friendly_error(str(exc))) from exc

    pool = {**(info.get("automatic_captions") or {}), **(info.get("subtitles") or {})}
    if not pool:
        raise AIError("该视频没有字幕可翻译")

    chosen = source_lang if source_lang in pool else next(
        (c for c in ("en", "en-US", "zh-Hans", "zh") if c in pool), next(iter(pool))
    )
    tracks = pool[chosen]
    track = next((t for t in tracks if (t.get("ext") or "").lower() in {"srt", "vtt"}), tracks[0])

    if track.get("data"):
        raw = track["data"]
    else:
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                raw = ydl.urlopen(track["url"]).read().decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001
            raise AIError("字幕下载失败") from exc

    prompt = (
        f"把下面的字幕翻译成{target_lang}。严格要求：\n"
        "- 保留原有的时间轴行（含 --> 的行）和序号，一字不改\n"
        "- 只翻译字幕文本行\n"
        "- 输出标准 SRT 格式，不要添加任何解释\n\n"
        f"{raw[:MAX_TRANSCRIPT_CHARS]}"
    )
    try:
        resp = client.messages.create(
            model=config.AI_MODEL,
            max_tokens=8000,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("translation failed")
        raise AIError(f"AI 调用失败：{exc}"[:200]) from exc

    return {
        "srt": "".join(b.text for b in resp.content if b.type == "text").strip(),
        "source_lang": chosen,
        "target_lang": target_lang,
        "model": config.AI_MODEL,
    }
