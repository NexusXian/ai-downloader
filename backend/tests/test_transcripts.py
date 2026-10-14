from __future__ import annotations

import builtins
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
import transcripts  # noqa: E402


def test_parse_srt_preserves_multiline_cue_timing():
    raw = "1\n00:00:01,250 --> 00:00:03,500\nHello\nworld\n"
    assert transcripts.parse_caption(raw, "srt") == (
        transcripts.TranscriptSegment(1.25, 3.5, "Hello world"),
    )


def test_parse_vtt_removes_tags_and_cue_settings():
    raw = "WEBVTT\n\n00:00:02.000 --> 00:00:04.000 align:start\n<c.green>Hello</c>  world\n"
    assert transcripts.parse_caption(raw, "vtt")[0].text == "Hello world"


def test_parse_json3_keeps_event_times():
    raw = json.dumps({"events": [{"tStartMs": 1500, "dDurationMs": 900,
                                   "segs": [{"utf8": "Hi "}, {"utf8": "there"}]}]})
    assert transcripts.parse_caption(raw, "json3") == (
        transcripts.TranscriptSegment(1.5, 2.4, "Hi there"),
    )


def test_parse_srv1_decodes_entities():
    raw = '<transcript><text start="2.5" dur="1.25">A &amp; B</text></transcript>'
    assert transcripts.parse_caption(raw, "srv1") == (
        transcripts.TranscriptSegment(2.5, 3.75, "A & B"),
    )


def test_select_caption_prefers_manual_requested_language():
    info = {
        "subtitles": {"en": [{"ext": "vtt", "data": "manual"}]},
        "automatic_captions": {"en": [{"ext": "vtt", "data": "auto"}]},
    }
    selected = transcripts.select_caption(info, "en")
    assert selected is not None
    assert selected.language == "en"
    assert selected.source == "manual_caption"
    assert selected.track["data"] == "manual"


def test_select_caption_auto_uses_documented_language_order():
    info = {
        "subtitles": {},
        "automatic_captions": {
            "fr": [{"ext": "vtt", "data": "fr"}],
            "en": [{"ext": "vtt", "data": "en"}],
        },
    }
    assert transcripts.select_caption(info, "auto").language == "en"


def test_parse_caption_rejects_unknown_or_empty_data():
    with pytest.raises(transcripts.TranscriptError):
        transcripts.parse_caption("content", "ttml")
    with pytest.raises(transcripts.TranscriptError):
        transcripts.parse_caption("WEBVTT\n", "vtt")


@pytest.mark.parametrize(("ext", "raw"), [
    (
        "srt",
        "1\n-00:00:01,000 --> 00:00:02,000\nNegative\n",
    ),
    (
        "srt",
        "1\n00:00:02,000 --> 00:00:01,000\nReversed\n",
    ),
    (
        "srt",
        "1\nNaN --> 00:00:01,000\nNon-finite\n",
    ),
    (
        "vtt",
        "WEBVTT\n\n-00:00:01.000 --> 00:00:02.000\nNegative\n",
    ),
    (
        "vtt",
        "WEBVTT\n\n00:00:02.000 --> 00:00:01.000\nReversed\n",
    ),
    (
        "vtt",
        "WEBVTT\n\n00:00:00.000 --> Infinity\nNon-finite\n",
    ),
    (
        "json3",
        json.dumps({"events": [{
            "tStartMs": -1,
            "dDurationMs": 1,
            "segs": [{"utf8": "Negative"}],
        }]}),
    ),
    (
        "json3",
        json.dumps({"events": [{
            "tStartMs": 2,
            "dDurationMs": -1,
            "segs": [{"utf8": "Reversed"}],
        }]}),
    ),
    (
        "json3",
        json.dumps({"events": [{
            "tStartMs": float("nan"),
            "dDurationMs": 1,
            "segs": [{"utf8": "Non-finite"}],
        }]}),
    ),
    (
        "srv1",
        '<transcript><text start="-1" dur="1">Negative</text></transcript>',
    ),
    (
        "srv1",
        '<transcript><text start="2" dur="-1">Reversed</text></transcript>',
    ),
    (
        "srv1",
        '<transcript><text start="inf" dur="1">Non-finite</text></transcript>',
    ),
])
def test_parse_caption_rejects_invalid_timestamps(ext, raw):
    with pytest.raises(transcripts.TranscriptError, match="^转录时间戳无效$"):
        transcripts.parse_caption(raw, ext)


@pytest.mark.parametrize(("ext", "raw"), [
    (
        "srt",
        f"1\n{'9' * 400}:00:00,000 --> 00:00:01,000\nExtreme\n",
    ),
    (
        "vtt",
        f"WEBVTT\n\n00:00:00.000 --> {'9' * 400}:00:01.000\nExtreme\n",
    ),
])
def test_parse_timed_caption_maps_extreme_hour_overflow_to_safe_error(ext, raw):
    with pytest.raises(transcripts.TranscriptError, match="^转录时间戳无效$"):
        transcripts.parse_caption(raw, ext)


@pytest.mark.parametrize(("ext", "raw"), [
    (
        "srt",
        f"1\n{'9' * 304}:00:00,000 --> {'9' * 304}:00:01,000\nFinite extreme\n",
    ),
    (
        "vtt",
        f"WEBVTT\n\n00:00:00.000 --> {'9' * 304}:00:01.000\nFinite extreme\n",
    ),
])
def test_parse_timed_caption_rejects_finite_extreme_hours(ext, raw):
    with pytest.raises(transcripts.TranscriptError, match="^转录时间戳无效$"):
        transcripts.parse_caption(raw, ext)


@pytest.mark.parametrize(("start_ms", "duration_ms"), [
    (int("9" * 400), 1),
    (0, int("9" * 400)),
    (float("inf"), 1),
    (0, float("nan")),
])
def test_parse_json3_maps_extreme_and_non_finite_times_to_safe_error(
    start_ms, duration_ms,
):
    raw = json.dumps({"events": [{
        "tStartMs": start_ms,
        "dDurationMs": duration_ms,
        "segs": [{"utf8": "Invalid"}],
    }]})

    with pytest.raises(transcripts.TranscriptError, match="^转录时间戳无效$"):
        transcripts.parse_caption(raw, "json3")


@pytest.mark.parametrize(("start_ms", "duration_ms"), [
    (1e308, 0),
    (0, 1e308),
    (1e308, 1e308),
])
def test_parse_json3_rejects_finite_extreme_times(start_ms, duration_ms):
    raw = json.dumps({"events": [{
        "tStartMs": start_ms,
        "dDurationMs": duration_ms,
        "segs": [{"utf8": "Finite extreme"}],
    }]})

    with pytest.raises(transcripts.TranscriptError, match="^转录时间戳无效$"):
        transcripts.parse_caption(raw, "json3")


def test_parse_json3_accepts_end_at_configured_duration():
    start_seconds = max(0, config.MAX_DURATION_SECONDS - 1)
    raw = json.dumps({"events": [{
        "tStartMs": start_seconds * 1000,
        "dDurationMs": (config.MAX_DURATION_SECONDS - start_seconds) * 1000,
        "segs": [{"utf8": "Boundary"}],
    }]})

    segments = transcripts.parse_caption(raw, "json3")

    assert segments == (
        transcripts.TranscriptSegment(
            float(start_seconds),
            float(config.MAX_DURATION_SECONDS),
            "Boundary",
        ),
    )


@pytest.mark.parametrize("cue", [
    "[Music]",
    "(applause)",
    "[Laughter]",
    "(silence)",
    "【音乐】",
    "（掌声）",
    "【笑声】",
    "（静音）",
    "（无声）",
])
def test_parse_caption_removes_pure_non_dialogue_cues(cue):
    raw = f"WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n{cue}\n"
    with pytest.raises(transcripts.TranscriptError, match="字幕内容为空"):
        transcripts.parse_caption(raw, "vtt")


def test_parse_caption_keeps_dialogue_that_mentions_a_cue():
    raw = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHello (laughter)\n"
    assert transcripts.parse_caption(raw, "vtt")[0].text == "Hello (laughter)"


def test_build_transcript_uses_caption_without_audio(tmp_path, monkeypatch):
    info = {
        "duration": 20,
        "subtitles": {"en": [{"ext": "vtt", "data":
            "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHello\n"}]},
        "automatic_captions": {},
    }
    monkeypatch.setattr(transcripts.downloader, "download_source_audio",
                        lambda *_args, **_kwargs: pytest.fail("audio fallback used"))
    got = transcripts.build_transcript("https://example.com/v", info, "auto", tmp_path,
                                       lambda _phase, _percent: None)
    assert got.source == "manual_caption"
    assert got.language == "en"
    assert got.segments[0].start_seconds == 1.0


def test_build_transcript_uses_automatic_caption_when_manual_is_unusable(
    tmp_path, monkeypatch,
):
    info = {
        "duration": 20,
        "subtitles": {"en": [{"ext": "vtt", "data": "WEBVTT\n"}]},
        "automatic_captions": {"en": [{"ext": "vtt", "data":
            "WEBVTT\n\n00:00:03.000 --> 00:00:04.000\nAutomatic\n"}]},
    }
    monkeypatch.setattr(transcripts.downloader, "download_source_audio",
                        lambda *_args, **_kwargs: pytest.fail("audio fallback used"))

    got = transcripts.build_transcript(
        "https://example.com/v", info, "en", tmp_path,
        lambda _phase, _percent: None,
    )

    assert got.source == "automatic_caption"
    assert got.language == "en"
    assert got.segments[0].text == "Automatic"


def test_build_transcript_falls_back_after_extreme_manual_timestamp(
    tmp_path, monkeypatch,
):
    info = {
        "duration": 20,
        "subtitles": {"en": [{"ext": "srt", "data":
            f"1\n{'9' * 400}:00:00,000 --> 00:00:01,000\nExtreme\n"}]},
        "automatic_captions": {"en": [{"ext": "vtt", "data":
            "WEBVTT\n\n00:00:03.000 --> 00:00:04.000\nAutomatic\n"}]},
    }
    monkeypatch.setattr(
        transcripts.downloader,
        "download_source_audio",
        lambda *_args, **_kwargs: pytest.fail("audio fallback used"),
    )

    got = transcripts.build_transcript(
        "https://example.com/v", info, "en", tmp_path,
        lambda _phase, _percent: None,
    )

    assert got.source == "automatic_caption"
    assert got.segments[0].text == "Automatic"


def test_build_transcript_falls_back_after_finite_extreme_manual_timestamp(
    tmp_path, monkeypatch,
):
    info = {
        "duration": 20,
        "subtitles": {"en": [{"ext": "srt", "data":
            f"1\n{'9' * 304}:00:00,000 --> {'9' * 304}:00:01,000\nExtreme\n"}]},
        "automatic_captions": {"en": [{"ext": "vtt", "data":
            "WEBVTT\n\n00:00:03.000 --> 00:00:04.000\nAutomatic\n"}]},
    }
    monkeypatch.setattr(
        transcripts.downloader,
        "download_source_audio",
        lambda *_args, **_kwargs: pytest.fail("audio fallback used"),
    )

    got = transcripts.build_transcript(
        "https://example.com/v", info, "en", tmp_path,
        lambda _phase, _percent: None,
    )

    assert got.source == "automatic_caption"
    assert got.segments[0].text == "Automatic"


def test_build_transcript_uses_automatic_caption_after_non_dialogue_manual_cue(
    tmp_path, monkeypatch,
):
    info = {
        "duration": 20,
        "subtitles": {"en": [{"ext": "vtt", "data":
            "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n[Music]\n"}]},
        "automatic_captions": {"en": [{"ext": "vtt", "data":
            "WEBVTT\n\n00:00:03.000 --> 00:00:04.000\nSpoken words\n"}]},
    }
    monkeypatch.setattr(
        transcripts.downloader,
        "download_source_audio",
        lambda *_args, **_kwargs: pytest.fail("audio fallback used"),
    )

    got = transcripts.build_transcript(
        "https://example.com/v", info, "en", tmp_path,
        lambda _phase, _percent: None,
    )

    assert got.source == "automatic_caption"
    assert got.segments[0].text == "Spoken words"


def test_build_transcript_falls_back_to_whisper_and_deletes_audio(tmp_path, monkeypatch):
    audio = tmp_path / "source.m4a"
    audio.write_bytes(b"audio")
    monkeypatch.setattr(transcripts.downloader, "download_source_audio",
                        lambda *_args, **_kwargs: audio)
    monkeypatch.setattr(transcripts, "transcribe_audio", lambda _path:
        ("en", (transcripts.TranscriptSegment(0.0, 1.0, "spoken"),)))
    phases = []
    got = transcripts.build_transcript(
        "https://example.com/v",
        {"duration": 12, "subtitles": {}, "automatic_captions": {}},
        "auto", tmp_path, lambda phase, percent: phases.append((phase, percent)),
    )
    assert got.source == "local_whisper"
    assert got.segments[0].text == "spoken"
    assert not audio.exists()
    assert any(phase == "transcribing" for phase, _ in phases)


def test_build_transcript_deletes_audio_when_whisper_fails(tmp_path, monkeypatch):
    audio = tmp_path / "source.webm"
    audio.write_bytes(b"audio")
    monkeypatch.setattr(transcripts.downloader, "download_source_audio",
                        lambda *_args, **_kwargs: audio)
    monkeypatch.setattr(transcripts, "transcribe_audio",
                        lambda _path: (_ for _ in ()).throw(RuntimeError("secret")))
    with pytest.raises(transcripts.TranscriptError, match="语音识别失败") as exc_info:
        transcripts.build_transcript(
            "https://example.com/v",
            {"duration": 12, "subtitles": {}, "automatic_captions": {}},
            "auto", tmp_path, lambda _phase, _percent: None,
        )
    assert "secret" not in str(exc_info.value)
    assert not audio.exists()


def test_build_transcript_preserves_error_when_audio_cleanup_fails(tmp_path, monkeypatch):
    audio = tmp_path / "source.webm"
    audio.write_bytes(b"audio")
    original_unlink = Path.unlink

    def failing_unlink(path, *args, **kwargs):
        if path == audio:
            raise OSError(f"secret path: {path}")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", failing_unlink)
    monkeypatch.setattr(transcripts.downloader, "download_source_audio",
                        lambda *_args, **_kwargs: audio)
    monkeypatch.setattr(transcripts, "transcribe_audio",
                        lambda _path: (_ for _ in ()).throw(RuntimeError("secret")))

    with pytest.raises(transcripts.TranscriptError) as exc_info:
        transcripts.build_transcript(
            "https://example.com/v",
            {"duration": 12, "subtitles": {}, "automatic_captions": {}},
            "auto", tmp_path, lambda _phase, _percent: None,
        )

    assert str(exc_info.value) == "语音识别失败"
    assert audio.exists()


def test_build_transcript_fails_when_successful_audio_cleanup_fails(
    tmp_path, monkeypatch,
):
    audio = tmp_path / "source.webm"
    audio.write_bytes(b"audio")
    original_unlink = Path.unlink

    def failing_unlink(path, *args, **kwargs):
        if path == audio:
            raise OSError(f"secret path: {path}")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", failing_unlink)
    monkeypatch.setattr(
        transcripts.downloader, "download_source_audio",
        lambda *_args, **_kwargs: audio,
    )
    monkeypatch.setattr(
        transcripts, "transcribe_audio",
        lambda _path: (
            "en",
            (transcripts.TranscriptSegment(0.0, 1.0, "spoken"),),
        ),
    )

    with pytest.raises(
        transcripts.TranscriptError, match="^临时音频清理失败$",
    ) as exc_info:
        transcripts.build_transcript(
            "https://example.com/v",
            {"duration": 12, "subtitles": {}, "automatic_captions": {}},
            "auto", tmp_path, lambda _phase, _percent: None,
        )

    assert "secret" not in str(exc_info.value)
    assert audio.exists()


def test_build_transcript_falls_back_when_caption_is_unusable(tmp_path, monkeypatch):
    audio = tmp_path / "source.m4a"
    audio.write_bytes(b"audio")
    info = {
        "duration": 8,
        "subtitles": {"en": [{"ext": "vtt", "data": "WEBVTT\n"}]},
        "automatic_captions": {},
    }
    monkeypatch.setattr(transcripts.downloader, "download_source_audio",
                        lambda *_args, **_kwargs: audio)
    monkeypatch.setattr(transcripts, "transcribe_audio", lambda _path:
        ("en", (transcripts.TranscriptSegment(0.0, 1.0, "spoken"),)))

    got = transcripts.build_transcript(
        "https://example.com/v", info, "auto", tmp_path,
        lambda _phase, _percent: None,
    )

    assert got.source == "local_whisper"
    assert not audio.exists()


def test_transcribe_audio_normalizes_model_segments(tmp_path, monkeypatch):
    class Model:
        def transcribe(self, path, **kwargs):
            assert path == str(tmp_path / "source.m4a")
            assert kwargs == {"vad_filter": True, "beam_size": 5}
            return (
                iter([
                    SimpleNamespace(start=0, end=1.25, text="  Hello   world  "),
                    SimpleNamespace(start=1.25, end=2, text="   "),
                ]),
                SimpleNamespace(language="en"),
            )

    monkeypatch.setattr(transcripts, "_whisper_model", lambda: Model())
    language, segments = transcripts.transcribe_audio(tmp_path / "source.m4a")

    assert language == "en"
    assert segments == (transcripts.TranscriptSegment(0.0, 1.25, "Hello world"),)


def test_whisper_model_reports_missing_dependency_safely(monkeypatch):
    original_import = builtins.__import__

    def missing_dependency(name, *args, **kwargs):
        if name == "faster_whisper":
            raise ModuleNotFoundError("secret dependency detail")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(transcripts, "_WHISPER_MODEL", None)
    monkeypatch.setattr(builtins, "__import__", missing_dependency)

    with pytest.raises(
        transcripts.TranscriptError,
        match="^本地语音识别依赖未安装$",
    ) as exc_info:
        transcripts._whisper_model()

    assert "secret" not in str(exc_info.value)


def test_whisper_model_reports_initialization_failure_safely(monkeypatch):
    class WhisperModel:
        def __init__(self, *_args, **_kwargs):
            raise RuntimeError("secret model path")

    monkeypatch.setattr(transcripts, "_WHISPER_MODEL", None)
    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        SimpleNamespace(WhisperModel=WhisperModel),
    )

    with pytest.raises(
        transcripts.TranscriptError,
        match="^Whisper 模型初始化失败$",
    ) as exc_info:
        transcripts._whisper_model()

    assert "secret" not in str(exc_info.value)


def test_whisper_model_reports_import_initialization_failure_safely(monkeypatch):
    original_import = builtins.__import__

    def failed_import(name, *args, **kwargs):
        if name == "faster_whisper":
            raise RuntimeError("secret native library detail")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(transcripts, "_WHISPER_MODEL", None)
    monkeypatch.setattr(builtins, "__import__", failed_import)

    with pytest.raises(
        transcripts.TranscriptError,
        match="^Whisper 模型初始化失败$",
    ) as exc_info:
        transcripts._whisper_model()

    assert "secret" not in str(exc_info.value)


@pytest.mark.parametrize("fail_during_iteration", [False, True])
def test_transcribe_audio_reports_provider_failure_safely(
    tmp_path, monkeypatch, fail_during_iteration,
):
    class Model:
        def transcribe(self, _path, **_kwargs):
            if not fail_during_iteration:
                raise RuntimeError("secret transcription detail")

            def failed_segments():
                raise RuntimeError("secret generator detail")
                yield

            return failed_segments(), SimpleNamespace(language="en")

    monkeypatch.setattr(transcripts, "_whisper_model", lambda: Model())

    with pytest.raises(
        transcripts.TranscriptError, match="^语音识别失败$",
    ) as exc_info:
        transcripts.transcribe_audio(tmp_path / "source.m4a")

    assert "secret" not in str(exc_info.value)


def test_transcript_limits_match_the_public_contract():
    assert transcripts.MAX_TRANSCRIPT_SEGMENTS == 50_000
    assert transcripts.MAX_TRANSCRIPT_TEXT_CHARS == 1_000_000
    assert transcripts.MAX_TRANSCRIPT_SEGMENT_CHARS == 12_000


def test_parse_caption_rejects_too_many_normalized_segments(monkeypatch):
    monkeypatch.setattr(transcripts, "MAX_TRANSCRIPT_SEGMENTS", 2)
    raw = json.dumps({
        "events": [
            {"tStartMs": index * 1000, "dDurationMs": 500,
             "segs": [{"utf8": str(index)}]}
            for index in range(3)
        ],
    })

    with pytest.raises(transcripts.TranscriptError, match="^转录片段数量超过限制$"):
        transcripts.parse_caption(raw, "json3")


def test_parse_caption_rejects_total_normalized_text_limit(monkeypatch):
    monkeypatch.setattr(transcripts, "MAX_TRANSCRIPT_TEXT_CHARS", 5)
    raw = json.dumps({
        "events": [
            {"tStartMs": 0, "dDurationMs": 500, "segs": [{"utf8": "abc"}]},
            {"tStartMs": 500, "dDurationMs": 500, "segs": [{"utf8": "def"}]},
        ],
    })

    with pytest.raises(transcripts.TranscriptError, match="^转录文本超过限制$"):
        transcripts.parse_caption(raw, "json3")


def test_parse_caption_rejects_one_oversized_segment(monkeypatch):
    monkeypatch.setattr(transcripts, "MAX_TRANSCRIPT_SEGMENT_CHARS", 3)
    raw = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nabcd\n"

    with pytest.raises(transcripts.TranscriptError, match="^转录片段文本超过限制$"):
        transcripts.parse_caption(raw, "vtt")


def test_transcribe_audio_rejects_oversized_whisper_output(
    tmp_path, monkeypatch,
):
    class Model:
        def transcribe(self, _path, **_kwargs):
            return (
                iter([SimpleNamespace(start=0, end=1, text="abcd")]),
                SimpleNamespace(language="en"),
            )

    monkeypatch.setattr(transcripts, "MAX_TRANSCRIPT_SEGMENT_CHARS", 3)
    monkeypatch.setattr(transcripts, "_whisper_model", lambda: Model())

    with pytest.raises(
        transcripts.TranscriptError, match="^转录片段文本超过限制$",
    ):
        transcripts.transcribe_audio(tmp_path / "source.m4a")


@pytest.mark.parametrize(("start", "end"), [
    (-1, 1),
    (2, 1),
    (0, float("inf")),
    (float("nan"), 1),
])
def test_transcribe_audio_rejects_invalid_whisper_timestamps(
    tmp_path, monkeypatch, start, end,
):
    class Model:
        def transcribe(self, _path, **_kwargs):
            return (
                iter([SimpleNamespace(start=start, end=end, text="spoken")]),
                SimpleNamespace(language="en"),
            )

    monkeypatch.setattr(transcripts, "_whisper_model", lambda: Model())

    with pytest.raises(transcripts.TranscriptError, match="^转录时间戳无效$"):
        transcripts.transcribe_audio(tmp_path / "source.m4a")
