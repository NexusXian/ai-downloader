from __future__ import annotations

import html
import json
import math
import re
import threading
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

import config
import downloader

TranscriptSource = Literal[
    "manual_caption",
    "automatic_caption",
    "local_whisper",
]

MAX_TRANSCRIPT_SEGMENTS = 50_000
MAX_TRANSCRIPT_TEXT_CHARS = 1_000_000
MAX_TRANSCRIPT_SEGMENT_CHARS = 12_000


class TranscriptError(RuntimeError):
    pass


@dataclass(frozen=True)
class TranscriptSegment:
    start_seconds: float
    end_seconds: float
    text: str

    def public(self) -> dict[str, float | str]:
        return {
            "start_seconds": round(self.start_seconds, 3),
            "end_seconds": round(self.end_seconds, 3),
            "text": self.text,
        }


@dataclass(frozen=True)
class Transcript:
    source: TranscriptSource
    language: str
    duration: float | None
    segments: tuple[TranscriptSegment, ...]


@dataclass(frozen=True)
class CaptionSelection:
    language: str
    source: Literal["manual_caption", "automatic_caption"]
    track: dict[str, Any]


_TIMESTAMP_VALUE = (
    r"[+-]?(?:(?:\d+:)?\d{2}:\d{2}[.,]\d{3}|nan|inf(?:inity)?)"
)
_TIMESTAMP_RE = re.compile(
    rf"^\s*(?P<start>{_TIMESTAMP_VALUE})\s*-->\s*"
    rf"(?P<end>{_TIMESTAMP_VALUE})(?:\s+.*)?$",
    re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")
_NON_DIALOGUE_RE = re.compile(
    r"^(?:[\[\(（【［]\s*(?:"
    r"music(?:\s+playing)?|applause|clapping|laughter|laughing|silence|silent|"
    r"音乐|背景音乐|掌声|鼓掌|笑声|大笑|沉默|静音|无声"
    r")\s*[\]\)）】］]\s*(?:[.!。！…]+\s*)?)+$",
    re.IGNORECASE,
)
_AUTO_LANGUAGES = ("zh-Hans", "zh-CN", "zh", "en", "en-US")
_CAPTION_FORMATS = ("vtt", "srt", "json3", "srv1")
_WHISPER_MODEL: Any | None = None
_WHISPER_MODEL_LOCK = threading.Lock()


def _clean_text(text: str) -> str:
    cleaned = " ".join(html.unescape(_TAG_RE.sub("", text)).split())
    return "" if _NON_DIALOGUE_RE.fullmatch(cleaned) else cleaned


def _timestamp_float(value: Any) -> float:
    try:
        converted = float(value)
    except (TypeError, ValueError, OverflowError):
        raise TranscriptError("转录时间戳无效") from None
    if not math.isfinite(converted):
        raise TranscriptError("转录时间戳无效")
    return converted


def _timestamp_seconds(timestamp: str) -> float:
    try:
        value = timestamp.strip().replace(",", ".")
        if ":" not in value:
            return _timestamp_float(value)
        sign = -1 if value.startswith("-") else 1
        if value[:1] in {"-", "+"}:
            value = value[1:]
        parts = value.split(":")
        seconds = _timestamp_float(parts[-1])
        minutes = int(parts[-2])
        hours = int(parts[-3]) if len(parts) == 3 else 0
        return _timestamp_float(sign * (hours * 3600 + minutes * 60 + seconds))
    except TranscriptError:
        raise
    except (TypeError, ValueError, OverflowError):
        raise TranscriptError("转录时间戳无效") from None


def _normalize_segments(
    segments: Any,
) -> tuple[TranscriptSegment, ...]:
    normalized: list[TranscriptSegment] = []
    total_chars = 0
    for segment in segments:
        try:
            start = _timestamp_float(segment.start_seconds)
            end = _timestamp_float(segment.end_seconds)
        except AttributeError:
            raise TranscriptError("转录时间戳无效") from None
        if (
            start < 0
            or end < start
            or start > config.MAX_DURATION_SECONDS
            or end > config.MAX_DURATION_SECONDS
        ):
            raise TranscriptError("转录时间戳无效")
        text = _clean_text(str(segment.text))
        if not text:
            continue
        if len(text) > MAX_TRANSCRIPT_SEGMENT_CHARS:
            raise TranscriptError("转录片段文本超过限制")
        if len(normalized) >= MAX_TRANSCRIPT_SEGMENTS:
            raise TranscriptError("转录片段数量超过限制")
        total_chars += len(text)
        if total_chars > MAX_TRANSCRIPT_TEXT_CHARS:
            raise TranscriptError("转录文本超过限制")
        normalized.append(TranscriptSegment(
            start,
            end,
            text,
        ))
    return tuple(normalized)


def serialize(transcript: Transcript) -> dict[str, Any]:
    return {
        "source": transcript.source,
        "language": transcript.language,
        "duration": transcript.duration,
        "segments": [segment.public() for segment in transcript.segments],
    }


def deserialize(payload: Any) -> Transcript:
    """Rebuild a Transcript from a persisted artifact, revalidating every field."""
    if not isinstance(payload, dict):
        raise TranscriptError("转录内容无法解析")

    source = payload.get("source")
    language = payload.get("language")
    raw_segments = payload.get("segments")
    if source not in ("manual_caption", "automatic_caption", "local_whisper"):
        raise TranscriptError("转录内容无法解析")
    if not isinstance(language, str) or not language:
        raise TranscriptError("转录内容无法解析")
    if not isinstance(raw_segments, list):
        raise TranscriptError("转录内容无法解析")

    duration = payload.get("duration")
    if duration is not None:
        if isinstance(duration, bool) or not isinstance(duration, (int, float)):
            raise TranscriptError("转录内容无法解析")
        duration = _timestamp_float(duration)

    parsed: list[TranscriptSegment] = []
    for item in raw_segments:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            raise TranscriptError("转录内容无法解析")
        parsed.append(TranscriptSegment(
            _timestamp_float(item.get("start_seconds")),
            _timestamp_float(item.get("end_seconds")),
            item["text"],
        ))

    segments = _normalize_segments(parsed)
    if not segments:
        raise TranscriptError("转录内容为空")
    return Transcript(
        source=source,
        language=language,
        duration=duration,
        segments=segments,
    )


def _parse_timed_text(raw: str) -> tuple[TranscriptSegment, ...]:
    segments: list[TranscriptSegment] = []
    start: float | None = None
    end: float | None = None
    lines: list[str] = []

    def append_segment() -> None:
        if start is None or end is None:
            return
        text = " ".join(lines)
        if text.strip():
            segments.append(TranscriptSegment(start, end, text))

    for line in raw.splitlines():
        match = _TIMESTAMP_RE.match(line)
        if match:
            append_segment()
            start = _timestamp_seconds(match.group("start"))
            end = _timestamp_seconds(match.group("end"))
            lines = []
        elif start is not None:
            if line.strip():
                lines.append(line)
            else:
                append_segment()
                start = None
                end = None
                lines = []

    append_segment()
    return tuple(segments)


def _parse_json3(raw: str) -> tuple[TranscriptSegment, ...]:
    payload = json.loads(raw)
    segments: list[TranscriptSegment] = []
    for event in payload.get("events", []):
        parts = event.get("segs")
        if not parts:
            continue
        text = "".join(part.get("utf8", "") for part in parts)
        if not text.strip():
            continue
        start_ms = _timestamp_float(event["tStartMs"])
        duration_ms = _timestamp_float(event["dDurationMs"])
        segments.append(
            TranscriptSegment(
                start_ms / 1000,
                (start_ms + duration_ms) / 1000,
                text,
            )
        )
    return tuple(segments)


def _parse_srv1(raw: str) -> tuple[TranscriptSegment, ...]:
    root = ET.fromstring(raw)
    segments: list[TranscriptSegment] = []
    for cue in root.iter("text"):
        text = "".join(cue.itertext())
        if not text.strip():
            continue
        start = _timestamp_float(cue.attrib["start"])
        duration = _timestamp_float(cue.attrib["dur"])
        segments.append(TranscriptSegment(start, start + duration, text))
    return tuple(segments)


def parse_caption(raw: str, ext: str) -> tuple[TranscriptSegment, ...]:
    parsers = {
        "srt": _parse_timed_text,
        "vtt": _parse_timed_text,
        "json3": _parse_json3,
        "srv1": _parse_srv1,
    }
    parser = parsers.get(ext)
    if parser is None:
        raise TranscriptError("不支持的字幕格式")

    try:
        segments = _normalize_segments(parser(raw))
    except TranscriptError:
        raise
    except (AttributeError, ET.ParseError, KeyError, TypeError, ValueError):
        raise TranscriptError("字幕内容无法解析") from None
    if not segments:
        raise TranscriptError("字幕内容为空或无法解析")
    return segments


def _select_track(tracks: list[dict[str, Any]]) -> dict[str, Any]:
    for ext in _CAPTION_FORMATS:
        for track in tracks:
            if track.get("ext") == ext:
                return track
    return tracks[0]


def select_caption(info: dict[str, Any], preferred: str) -> CaptionSelection | None:
    subtitles = info.get("subtitles") or {}
    automatic = info.get("automatic_captions") or {}

    def has_tracks(language: str) -> bool:
        return bool(subtitles.get(language) or automatic.get(language))

    if preferred == "auto":
        languages = list(_AUTO_LANGUAGES)
        for catalog in (subtitles, automatic):
            languages.extend(language for language in catalog if language not in languages)
        language = next((item for item in languages if has_tracks(item)), None)
    else:
        language = preferred if has_tracks(preferred) else None

    if language is None:
        return None

    for catalog, source in (
        (subtitles, "manual_caption"),
        (automatic, "automatic_caption"),
    ):
        tracks = catalog.get(language) or []
        if tracks:
            return CaptionSelection(language, source, _select_track(tracks))
    return None


def _whisper_model() -> Any:
    global _WHISPER_MODEL
    if _WHISPER_MODEL is None:
        with _WHISPER_MODEL_LOCK:
            if _WHISPER_MODEL is None:
                try:
                    from faster_whisper import WhisperModel
                except ImportError:
                    raise TranscriptError("本地语音识别依赖未安装") from None
                except Exception:
                    raise TranscriptError("Whisper 模型初始化失败") from None
                try:
                    model = WhisperModel(
                        config.WHISPER_MODEL,
                        device=config.WHISPER_DEVICE,
                        compute_type=config.WHISPER_COMPUTE_TYPE,
                    )
                except Exception:
                    raise TranscriptError("Whisper 模型初始化失败") from None
                _WHISPER_MODEL = model
    return _WHISPER_MODEL


def transcribe_audio(path: Path) -> tuple[str, tuple[TranscriptSegment, ...]]:
    model = _whisper_model()
    try:
        segments, info = model.transcribe(str(path), vad_filter=True, beam_size=5)
        normalized = _normalize_segments(
            TranscriptSegment(item.start, item.end, item.text)
            for item in segments
        )
        language = info.language or "unknown"
    except TranscriptError:
        raise
    except Exception:
        raise TranscriptError("语音识别失败") from None
    if not normalized:
        raise TranscriptError("语音识别未返回有效文本")
    return language, normalized


def build_transcript(
    url: str,
    info: dict[str, Any],
    preferred: str,
    job_dir: Path,
    on_phase: Callable[[str, float], None],
) -> Transcript:
    selection = select_caption(info, preferred)
    candidates = [selection] if selection is not None else []
    if selection is not None and selection.source == "manual_caption":
        automatic = (info.get("automatic_captions") or {}).get(selection.language) or []
        if automatic:
            candidates.append(CaptionSelection(
                selection.language,
                "automatic_caption",
                _select_track(automatic),
            ))

    for selection in candidates:
        on_phase("reading_caption", 0.0)
        try:
            ext, raw = downloader.read_caption_track(selection.track)
            segments = parse_caption(raw, ext)
        except (downloader.ProbeError, TranscriptError):
            pass
        else:
            on_phase("reading_caption", 100.0)
            return Transcript(
                source=selection.source,
                language=selection.language,
                duration=info.get("duration"),
                segments=segments,
            )

    def report_audio_progress(progress: dict[str, Any]) -> None:
        total = progress.get("total_bytes")
        downloaded = progress.get("downloaded_bytes") or 0
        if progress.get("phase") == "downloaded":
            percent = 100.0
        elif total:
            percent = min(100.0, downloaded / total * 100)
        else:
            percent = 0.0
        on_phase("downloading_audio", percent)

    audio: Path | None = None
    completed = False
    on_phase("downloading_audio", 0.0)
    try:
        try:
            audio = downloader.download_source_audio(
                url, job_dir / "source", report_audio_progress,
            )
        except Exception:
            raise TranscriptError("音频下载失败") from None

        on_phase("transcribing", 0.0)
        try:
            language, segments = transcribe_audio(audio)
            segments = _normalize_segments(segments)
        except TranscriptError:
            raise
        except Exception:
            raise TranscriptError("语音识别失败") from None
        on_phase("transcribing", 100.0)
        transcript = Transcript(
            source="local_whisper",
            language=language,
            duration=info.get("duration"),
            segments=segments,
        )
        completed = True
        return transcript
    finally:
        if audio is not None:
            try:
                audio.unlink(missing_ok=True)
            except OSError:
                if completed:
                    raise TranscriptError("临时音频清理失败") from None
