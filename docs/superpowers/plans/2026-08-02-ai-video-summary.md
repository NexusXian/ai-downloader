# AI Video Summary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the caption-only synchronous summary with a Pro-only asynchronous workflow that uses timestamped captions or local Whisper, summarizes long videos completely, and exports temporary Markdown notes.

**Architecture:** A transcript module normalizes platform captions and local Whisper output. A structured Anthropic service summarizes bounded transcript chunks, and a separate in-memory AI job store runs that work outside the download pool and writes temporary JSON/Markdown artifacts. FastAPI exposes authenticated job endpoints; the existing Next.js AI panel polls them and renders grounded, timestamped results.

**Tech Stack:** Python 3.13, FastAPI 0.141.1, Pydantic 2, yt-dlp 2026.7.4, faster-whisper 1.2.1, Anthropic SDK 0.120.2, pytest 9.1.1, Next.js 16.2.12, React 19.2.4, TypeScript 5.9, Tailwind CSS 4.3.

## Global Constraints

- Keep summary and local speech recognition Pro-only.
- Prefer creator captions, then automatic captions, then local Whisper.
- Summary output languages are exactly `zh-CN` and `en`.
- Keep transcript text in its source language.
- Default Whisper settings are model `small`, device `cpu`, compute type `int8`.
- Store AI results temporarily under `backend/_summary_jobs`; add no database or account history.
- Keep download and AI worker pools separate; default `AI_MAX_WORKERS=1`.
- Keep `POST /api/ai/translate` working.
- Remove the old `POST /api/ai/summary` route and synchronous summary client.
- Tests must not access media sites, download Whisper models, or call Anthropic.
- Do not log transcripts, prompts, keys, signed caption URLs, or raw model responses.
- Do not add chat, mind maps, slides, flashcards, local uploads, batch summaries, or cancellation.
- Before frontend edits, read `frontend/node_modules/next/dist/docs/01-app/01-getting-started/05-server-and-client-components.md` as required by `frontend/AGENTS.md`.
- Do not create a Git commit unless the user separately requests one.

## File Structure

### Create

- `backend/transcripts.py` — transcript models, caption selection/parsing, local Whisper adapter, and captions-first orchestration.
- `backend/summary_jobs.py` — AI job model/store, worker phases, artifact writing, cleanup, and Markdown rendering.
- `backend/tests/test_transcripts.py` — caption parsers, selection, Whisper fallback, and cleanup tests.
- `backend/tests/test_ai_summary.py` — chunking, structured Anthropic output, evidence, repair, and translation regression tests.
- `backend/tests/test_summary_jobs.py` — AI job state, worker, artifacts, queue, and sweeper tests.
- `frontend/components/summary-result.tsx` — structured result and transcript rendering.
- `frontend/lib/video.ts` — timestamp formatting and supported source URL generation.

### Modify

- `backend/config.py` — AI job and Whisper settings.
- `backend/downloader.py` — public metadata extraction, safe caption retrieval, and temporary source-audio download.
- `backend/ai.py` — structured hierarchical summarization and translation through shared caption utilities.
- `backend/main.py` — AI job routes, lifecycle cleanup, auth, health, and removal of the synchronous route.
- `backend/security.py` — allow safe Markdown attachments and return their MIME type.
- `backend/requirements.txt` — pin `faster-whisper==1.2.1`.
- `backend/.env.example` — expose AI job and Whisper settings.
- `backend/tests/test_api.py` — new route contract, auth, result/export, and old-route removal tests.
- `backend/tests/test_jobs.py` — downloader source-helper coverage.
- `backend/tests/test_security.py` — Markdown extension and content-type coverage.
- `frontend/lib/api.ts` — AI job/result types, polling, result fetch, and authenticated Markdown export.
- `frontend/components/ai-panel.tsx` — language controls, progress, result loading, privacy copy, and translation preservation.
- `frontend/components/downloader.tsx` — clear parent probe state when input changes.
- `frontend/app/page.tsx` — preserve the current probe wiring; only adjust props if required by the AI panel contract.
- `.gitignore` — ignore `backend/_summary_jobs/`.
- `README.md` — document the implemented workflow, routes, dependencies, configuration, privacy behavior, and limits.

---

### Task 1: Timestamped Caption Domain and Parsers

**Files:**
- Create: `backend/transcripts.py`
- Create: `backend/tests/test_transcripts.py`

**Interfaces:**
- Produces: `TranscriptSegment`, `Transcript`, `CaptionSelection`, `TranscriptError`.
- Produces: `parse_caption(raw: str, ext: str) -> tuple[TranscriptSegment, ...]`.
- Produces: `select_caption(info: dict[str, Any], preferred: str) -> CaptionSelection | None`.
- Later tasks consume these names exactly.

- [ ] **Step 1: Write failing parser and selection tests**

Create `backend/tests/test_transcripts.py` with path setup matching the current tests and these cases:

```python
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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
```

- [ ] **Step 2: Run the new tests and confirm the missing module failure**

Run: `./.venv/bin/python -m pytest tests/test_transcripts.py -q`

Expected: collection fails with `ModuleNotFoundError: No module named 'transcripts'`.

- [ ] **Step 3: Implement transcript models, normalization, parsers, and selection**

Create `backend/transcripts.py` with these public definitions:

```python
TranscriptSource = Literal["manual_caption", "automatic_caption", "local_whisper"]


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
```

Implement SRT/VTT parsing by collecting text until the next timestamp line, JSON3 parsing from `events[].tStartMs/dDurationMs/segs[].utf8`, and SRV1 parsing with `xml.etree.ElementTree`. Normalize HTML entities, remove tags, collapse whitespace, skip empty cues, and reject unsupported formats or an empty parsed result with safe Chinese `TranscriptError` messages.

Implement `select_caption` with this order:

1. Exact preferred language when it exists.
2. For `auto`: `zh-Hans`, `zh-CN`, `zh`, `en`, `en-US`, then the first available language.
3. For a given language, use `subtitles` before `automatic_captions`.
4. Select track formats in order `vtt`, `srt`, `json3`, `srv1`, then the first track.

- [ ] **Step 4: Run parser tests**

Run: `./.venv/bin/python -m pytest tests/test_transcripts.py -q`

Expected: all Task 1 tests pass.

---

### Task 2: Downloader Integration and Local Whisper Fallback

**Files:**
- Modify: `backend/downloader.py:140-197,269-309`
- Modify: `backend/transcripts.py`
- Modify: `backend/config.py:30-49,82-89`
- Modify: `backend/requirements.txt:16-22`
- Modify: `backend/tests/test_jobs.py`
- Modify: `backend/tests/test_transcripts.py`

**Interfaces:**
- Produces: `downloader.extract_info(url: str) -> dict[str, Any]`.
- Produces: `downloader.read_caption_track(track: dict[str, Any]) -> tuple[str, str]`.
- Produces: `downloader.download_source_audio(url: str, out_stem: Path, on_progress: ProgressFn) -> Path`.
- Produces: `transcripts.build_transcript(url, info, preferred, job_dir, on_phase) -> Transcript`.
- Consumes: Task 1 transcript models and parsers.

- [ ] **Step 1: Add failing downloader and transcript-orchestration tests**

Append tests that verify:

```python
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
    with pytest.raises(transcripts.TranscriptError, match="语音识别失败"):
        transcripts.build_transcript(
            "https://example.com/v",
            {"duration": 12, "subtitles": {}, "automatic_captions": {}},
            "auto", tmp_path, lambda _phase, _percent: None,
        )
    assert not audio.exists()
```

In `backend/tests/test_jobs.py`, add a stubbed yt-dlp test proving `probe()` calls the new shared `extract_info()` result shaping instead of maintaining a second extraction path, and a test proving `download_source_audio()` uses `bestaudio/best` without an MP3 postprocessor.

- [ ] **Step 2: Run focused tests and confirm missing interfaces**

Run: `./.venv/bin/python -m pytest tests/test_transcripts.py tests/test_jobs.py -q`

Expected: failures name `build_transcript`, `extract_info`, or `download_source_audio` as missing.

- [ ] **Step 3: Add configuration and install the pinned ASR dependency**

Add to `backend/config.py`:

```python
SUMMARY_JOB_DIR = Path(
    os.getenv("SUMMARY_JOB_DIR") or (Path(__file__).parent / "_summary_jobs")
).resolve()
SUMMARY_JOB_TTL_SECONDS = _int("SUMMARY_JOB_TTL_SECONDS", 3600)
AI_MAX_WORKERS = max(1, _int("AI_MAX_WORKERS", 1))
AI_MAX_QUEUED_JOBS = max(1, _int("AI_MAX_QUEUED_JOBS", 10))
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small").strip() or "small"
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu").strip() or "cpu"
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8").strip() or "int8"
```

Add `faster-whisper==1.2.1` to `backend/requirements.txt`, then run:

`./.venv/bin/python -m pip install -r requirements.txt`

Expected: installation succeeds under Python 3.13 without changing existing pinned versions.

- [ ] **Step 4: Centralize yt-dlp metadata, caption reads, and source audio**

Refactor `downloader.probe()` so extraction and shared safety checks live in:

```python
def extract_info(url: str) -> dict[str, Any]:
    """Return normalized, sanitized yt-dlp metadata after shared media checks."""
```

This function performs the current extraction, playlist-first-entry handling, duration enforcement, and DRM-only rejection. `probe()` calls it and only shapes public fields.

Add:

```python
def read_caption_track(track: dict[str, Any]) -> tuple[str, str]:
    """Return a supported caption extension and UTF-8 text."""


def download_source_audio(
    url: str, out_stem: Path, on_progress: ProgressFn
) -> Path:
    opts = _base_opts() | _hooks(on_progress) | {
        "format": "bestaudio/best",
        "outtmpl": f"{out_stem}.%(ext)s",
        "max_filesize": config.MAX_FILESIZE_BYTES,
    }
    return _run(url, opts, out_stem, default_ext="m4a")
```

`read_caption_track` accepts embedded `data`; otherwise it validates an HTTP(S) track URL through `security.validate_url`, reads it through yt-dlp, decodes with replacement, and maps any failure to `ProbeError("字幕下载失败")` without including the signed URL.

- [ ] **Step 5: Implement lazy local Whisper and captions-first orchestration**

In `backend/transcripts.py`, add a process-wide lock and cached model. Import `faster_whisper.WhisperModel` inside the loader so tests can patch transcription without model initialization.

```python
def transcribe_audio(path: Path) -> tuple[str, tuple[TranscriptSegment, ...]]:
    model = _whisper_model()
    segments, info = model.transcribe(str(path), vad_filter=True, beam_size=5)
    normalized = tuple(
        TranscriptSegment(float(item.start), float(item.end), _clean_text(item.text))
        for item in segments
        if _clean_text(item.text)
    )
    if not normalized:
        raise TranscriptError("语音识别未返回有效文本")
    return (info.language or "unknown", normalized)
```

Implement `build_transcript` with caption-first selection, `downloader.read_caption_track`, parser fallback to local audio when caption content is unusable, phase callbacks, and audio deletion in `finally`. Wrap model and media errors in bounded Chinese `TranscriptError` messages without the original exception text.

- [ ] **Step 6: Run transcript/downloader tests**

Run: `./.venv/bin/python -m pytest tests/test_transcripts.py tests/test_jobs.py -q`

Expected: all tests pass and no model is downloaded.

---

### Task 3: Structured Long-Video Summarization

**Files:**
- Modify: `backend/ai.py`
- Create: `backend/tests/test_ai_summary.py`

**Interfaces:**
- Consumes: `Transcript` and `TranscriptSegment` from Task 1.
- Produces: `chunk_segments(segments, max_chars=24_000)`.
- Produces: `summarize_transcript(title, source_url, transcript, output_lang, on_progress) -> dict[str, Any]`.
- Preserves: `available()` and `translate_subtitles()`.

- [ ] **Step 1: Write failing chunking, evidence, validation, and safety tests**

Create `backend/tests/test_ai_summary.py` covering these exact behaviors:

```python
def test_chunk_segments_covers_every_segment_once():
    segments = tuple(
        transcripts.TranscriptSegment(i, i + 1, f"segment-{i}-" + "x" * 20)
        for i in range(10)
    )
    chunks = ai.chunk_segments(segments, max_chars=90)
    assert [segment.text for chunk in chunks for segment in chunk] == [
        segment.text for segment in segments
    ]
    assert len(chunks) > 1


def test_summary_attaches_evidence_from_original_transcript(monkeypatch):
    transcript = transcripts.Transcript(
        source="manual_caption", language="en", duration=30,
        segments=(
            transcripts.TranscriptSegment(4, 8, "Grounded source sentence."),
            transcripts.TranscriptSegment(12, 15, "Another source sentence."),
        ),
    )
    monkeypatch.setattr(ai, "_request_structured", lambda **_kwargs:
        ai.GeneratedSummary.model_validate({
        "overview": "Overview",
        "key_points": [{"text": "Point", "start_seconds": 5, "end_seconds": 7}],
        "chapters": [{"title": "Start", "start_seconds": 4, "summary": "Chapter"}],
        "core_knowledge": ["Knowledge"],
        "target_audience": "Students",
    }))
    result = ai.summarize_transcript("Title", "https://example.com/v", transcript, "en")
    assert result["key_points"][0]["evidence"] == "Grounded source sentence."
    assert result["transcript"]["source"] == "manual_caption"


def test_invalid_structured_output_gets_one_repair(monkeypatch):
    calls = []
    responses = iter([
        {"overview": "missing fields"},
        {"overview": "Fixed", "key_points": [], "chapters": [],
         "core_knowledge": ["K"], "target_audience": "Everyone"},
    ])
    monkeypatch.setattr(ai, "_anthropic_tool_call",
                        lambda **kwargs: calls.append(kwargs) or next(responses))
    got = ai._request_structured(prompt="prompt", output_lang="en")
    assert got.overview == "Fixed"
    assert len(calls) == 2


def test_provider_error_is_safe(monkeypatch):
    monkeypatch.setattr(ai, "_client", lambda: object())
    monkeypatch.setattr(ai, "_anthropic_tool_call",
                        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("sk-secret")))
    with pytest.raises(ai.AIError, match="AI 总结服务暂时不可用") as caught:
        ai._request_structured(prompt="source", output_lang="zh-CN")
    assert "sk-secret" not in str(caught.value)
```

Also test:

- `zh-CN` and `en` map to explicit output instructions.
- A provider timestamp beyond duration is clamped to the transcript range.
- Multi-chunk input calls partial summarization once per chunk and a final synthesis once.
- One-chunk input skips partial summarization.
- Transcript text appears only inside source delimiters and the system instruction calls it untrusted data.
- `translate_subtitles()` uses shared downloader/transcript caption utilities and still returns SRT fields.

- [ ] **Step 2: Run the AI tests and confirm missing structured interfaces**

Run: `./.venv/bin/python -m pytest tests/test_ai_summary.py -q`

Expected: failures identify missing `chunk_segments`, `summarize_transcript`, or structured validation helpers.

- [ ] **Step 3: Replace plain-text summary logic with schema-validated tool output**

Keep `AIUnavailable`, `AIError`, `available`, and `_client`. Remove `MAX_TRANSCRIPT_CHARS`, `strip_subtitle_markup`, `fetch_transcript`, and `summarize` after their consumers are migrated.

Define Pydantic generated-output models with bounded fields:

```python
class GeneratedKeyPoint(BaseModel):
    text: str = Field(min_length=1, max_length=800)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)


class GeneratedChapter(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    start_seconds: float = Field(ge=0)
    summary: str = Field(min_length=1, max_length=1200)


class GeneratedSummary(BaseModel):
    overview: str = Field(min_length=1, max_length=2000)
    key_points: list[GeneratedKeyPoint] = Field(max_length=12)
    chapters: list[GeneratedChapter] = Field(max_length=24)
    core_knowledge: list[str] = Field(min_length=1, max_length=12)
    target_audience: str = Field(min_length=1, max_length=800)
```

Implement `_request_structured(prompt: str, output_lang: Literal["zh-CN", "en"]) -> GeneratedSummary`. It calls Anthropic with one forced `submit_summary` tool whose `input_schema` is `GeneratedSummary.model_json_schema()`. Extract only that tool block. Validate it; after one validation failure, make one repair request containing the validation messages and malformed object. Do not log malformed content.

- [ ] **Step 4: Implement bounded chunking and hierarchical synthesis**

Format each source line as `[HH:MM:SS.mmm-HH:MM:SS.mmm] text`. Split only between segments. If one segment exceeds the character bound, retain it as one chunk rather than truncating it.

For multiple chunks:

1. Request one compact partial summary per chunk with original timestamps.
2. Serialize only validated partial objects into the final synthesis input.
3. Report summarization progress from 55 through 95 using `on_progress(percent)`.

For one chunk, request the final schema directly from that transcript chunk.

Resolve each key point's evidence from original segments overlapping its returned range, capped at 400 characters. Clamp generated times to `[0, duration]` and ensure `end_seconds >= start_seconds`.

Return the complete result model from the specification, including transcript metadata/segments and `model=config.AI_MODEL`.

- [ ] **Step 5: Migrate subtitle translation to shared extraction**

Have `translate_subtitles()` call `downloader.extract_info()`, `transcripts.select_caption()`, and `downloader.read_caption_track()`. Keep its current response contract and fixed translation behavior. Keep translation caption-only; do not invoke Whisper.

- [ ] **Step 6: Run AI and transcript tests**

Run: `./.venv/bin/python -m pytest tests/test_ai_summary.py tests/test_transcripts.py -q`

Expected: all tests pass with mocked Anthropic and Whisper.

---

### Task 4: Asynchronous Summary Job Store and Artifacts

**Files:**
- Create: `backend/summary_jobs.py`
- Create: `backend/tests/test_summary_jobs.py`
- Modify: `.gitignore:12-14`

**Interfaces:**
- Consumes: `transcripts.build_transcript()` and `ai.summarize_transcript()`.
- Produces: `SummaryJob`, `SummaryJobStore`, `store`, and `disk_has_room()`.
- Produces store methods `create`, `get`, `submit`, `count_active`, `can_accept`, `read_result`, `sweep`, and `shutdown`.

- [ ] **Step 1: Write failing job lifecycle and artifact tests**

Create `backend/tests/test_summary_jobs.py` with an isolated `SUMMARY_JOB_DIR` fixture and tests for:

```python
def test_public_job_hides_url_metadata_and_paths(store):
    job = store.create(
        url="https://example.com/private", title="Title", source_lang="auto",
        output_lang="en", source_info={"duration": 10},
    )
    payload = job.public()
    assert payload["status"] == "queued"
    assert "url" not in payload
    assert "source_info" not in payload
    assert "result_path" not in payload


def test_worker_writes_result_and_markdown_atomically(store, monkeypatch):
    transcript = transcripts.Transcript(
        source="manual_caption", language="en", duration=10,
        segments=(transcripts.TranscriptSegment(1, 2, "Evidence"),),
    )
    monkeypatch.setattr(summary_jobs.transcripts, "build_transcript",
                        lambda *_args, **_kwargs: transcript)
    monkeypatch.setattr(summary_jobs.ai, "summarize_transcript",
                        lambda *_args, **_kwargs: _complete_result())
    job = store.create(url="https://example.com/v", title="Title", source_lang="auto",
                       output_lang="en", source_info={"duration": 10})
    store._run(job.id)
    finished = store.get(job.id)
    assert finished.status == "done"
    assert finished.percent == 100
    assert store.read_result(job.id)["overview"] == "Overview"
    assert finished.export_path.read_text(encoding="utf-8").startswith("# Title")
    assert not list(finished.export_path.parent.glob("*.tmp"))


def test_worker_failure_exposes_safe_error_and_removes_partial_files(store, monkeypatch):
    monkeypatch.setattr(summary_jobs.transcripts, "build_transcript",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(
                            transcripts.TranscriptError("语音识别失败")))
    job = store.create(url="https://example.com/v", title="Title", source_lang="auto",
                       output_lang="zh-CN", source_info={"duration": 10})
    store._run(job.id)
    assert store.get(job.id).error == "语音识别失败"
    assert not (config.SUMMARY_JOB_DIR / job.id).exists()


def test_queue_capacity_counts_only_active_jobs(store, monkeypatch):
    monkeypatch.setattr(config, "AI_MAX_QUEUED_JOBS", 1)
    first = store.create(url="https://example.com/a", title="A", source_lang="auto",
                         output_lang="en", source_info={})
    assert store.can_accept() is False
    store.update(first.id, status="done")
    assert store.can_accept() is True
```

Also test monotonic progress, owner-only directory mode, completed-job TTL removal, in-flight preservation, orphan cleanup, result-before-ready failure, and Markdown sections/timestamps.

- [ ] **Step 2: Run job tests and confirm the missing module failure**

Run: `./.venv/bin/python -m pytest tests/test_summary_jobs.py -q`

Expected: collection fails with `ModuleNotFoundError: No module named 'summary_jobs'`.

- [ ] **Step 3: Implement the job model and thread-safe registry**

Define:

```python
SummaryStatus = Literal[
    "queued", "extracting", "transcribing", "summarizing", "done", "error", "expired"
]


@dataclass
class SummaryJob:
    id: str
    url: str
    title: str
    source_lang: str
    output_lang: Literal["zh-CN", "en"]
    source_info: dict[str, Any]
    status: SummaryStatus = "queued"
    percent: float = 0.0
    error: str | None = None
    transcript_source: str | None = None
    transcript_lang: str | None = None
    result_path: Path | None = None
    export_path: Path | None = None
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
```

`public()` returns only job ID, status, title, rounded percent, safe error, transcript source/language, ready flag, and `expires_at = created_at + SUMMARY_JOB_TTL_SECONDS`.

`SummaryJobStore` uses a lock and `ThreadPoolExecutor(max_workers=config.AI_MAX_WORKERS, thread_name_prefix="ai")`. `update()` prevents progress from decreasing. `can_accept()` compares active count with `AI_MAX_QUEUED_JOBS`.

- [ ] **Step 4: Implement worker execution and atomic artifacts**

The worker:

1. Creates an owner-only job directory.
2. Sets `extracting` and progress 5.
3. Calls `build_transcript`, mapping its phase callback to `extracting` or `transcribing` and progress 5–50.
4. Stores transcript source/language.
5. Sets `summarizing` and progress 55.
6. Calls `summarize_transcript` and maps progress through 95.
7. Writes `result.json.tmp` and `summary.md.tmp`, flushes them, then replaces final paths.
8. Sets `done`, 100, paths, and finish time.

Catch only user-safe domain errors for their exact message. For unexpected exceptions, log a stack trace without request content and expose `AI 总结失败，请稍后重试`. Remove the entire job directory on failure.

`read_result()` loads only a completed result file. Markdown rendering includes metadata, overview, key points with evidence, chapters, core knowledge, target audience, and every transcript segment with formatted timestamps.

- [ ] **Step 5: Implement cleanup and ignore temporary artifacts**

Mirror the proven download sweeper behavior using `SUMMARY_JOB_TTL_SECONDS`, preserving active phases and deleting orphan directories by mtime. Add `backend/_summary_jobs/` to `.gitignore`.

- [ ] **Step 6: Run summary job tests**

Run: `./.venv/bin/python -m pytest tests/test_summary_jobs.py -q`

Expected: all tests pass.

---

### Task 5: FastAPI AI Job Contract and Lifecycle

**Files:**
- Modify: `backend/main.py:9-56,102-129,133-158,288-329`
- Modify: `backend/security.py:128-132,178-187`
- Modify: `backend/tests/test_api.py:12-30,271-314`
- Modify: `backend/tests/test_security.py:141-146,179-184`

**Interfaces:**
- Consumes: `summary_jobs.store` and `downloader.extract_info()`.
- Produces: `POST /api/ai/jobs`.
- Produces: `GET /api/ai/jobs/{job_id}`.
- Produces: `GET /api/ai/jobs/{job_id}/result`.
- Produces: `GET /api/ai/jobs/{job_id}/export`.
- Removes: `POST /api/ai/summary`.

- [ ] **Step 1: Replace synchronous API tests with failing job API tests**

Update the isolation fixture to patch a new `SummaryJobStore` rooted at `tmp_path / "summary-jobs"`. Add tests proving:

```python
def test_ai_job_requires_key_before_work(client, pro_token, monkeypatch):
    monkeypatch.setattr(main.ai, "available", lambda: False)
    response = client.post(
        "/api/ai/jobs", json={"url": "https://www.youtube.com/watch?v=x"},
        headers={"Authorization": f"Bearer {pro_token}"},
    )
    assert response.status_code == 503


def test_ai_job_requires_pro(client, monkeypatch):
    monkeypatch.setattr(main.ai, "available", lambda: True)
    response = client.post(
        "/api/ai/jobs", json={"url": "https://www.youtube.com/watch?v=x"}
    )
    assert response.status_code == 402


def test_ai_job_creation_probes_once_and_queues(client, pro_token, monkeypatch):
    monkeypatch.setattr(main.ai, "available", lambda: True)
    monkeypatch.setattr(main.downloader, "extract_info", lambda _url: {
        "webpage_url": "https://www.youtube.com/watch?v=x", "title": "Video",
        "duration": 60, "subtitles": {}, "automatic_captions": {}, "formats": [{}],
    })
    monkeypatch.setattr(main.summary_jobs.store, "submit", lambda _job: None)
    response = client.post(
        "/api/ai/jobs",
        json={"url": "https://www.youtube.com/watch?v=x", "source_lang": "auto",
              "output_lang": "en"},
        headers={"Authorization": f"Bearer {pro_token}"},
    )
    assert response.status_code == 202
    assert response.json()["status"] == "queued"


@pytest.mark.parametrize("path", [
    "/api/ai/jobs/deadbeef",
    "/api/ai/jobs/deadbeef/result",
    "/api/ai/jobs/deadbeef/export",
])
def test_all_ai_job_reads_require_pro(client, path):
    assert client.get(path).status_code == 402


def test_old_summary_route_is_removed(client, pro_token):
    response = client.post(
        "/api/ai/summary", json={"url": "https://www.youtube.com/watch?v=x"},
        headers={"Authorization": f"Bearer {pro_token}"},
    )
    assert response.status_code == 404
```

Also cover unsafe URL, probe failure, disk full, full AI queue, unknown/unfinished result, completed JSON, authenticated Markdown headers, and path confinement.

Extend the security tests so `safe_ext("md") == "md"` and `content_type_for("md") == "text/markdown"`.

- [ ] **Step 2: Run focused API tests and confirm route failures**

Run: `./.venv/bin/python -m pytest tests/test_api.py -q`

Expected: new AI job tests fail because routes do not exist and the old route still returns a non-404 response.

- [ ] **Step 3: Add request model and Pro guard**

Replace summary use of `AIIn` with:

```python
class AISummaryJobIn(BaseModel):
    url: str = Field(min_length=4, max_length=2048)
    title: str | None = Field(default=None, max_length=200)
    source_lang: str = Field(default="auto", min_length=1, max_length=32)
    output_lang: Literal["zh-CN", "en"] = "zh-CN"


class AITranslateIn(BaseModel):
    url: str = Field(min_length=4, max_length=2048)
    title: str | None = Field(default=None, max_length=200)
    target_lang: str = Field(default="简体中文", max_length=32)
```

Add a helper that raises the current 402 response unless either Pro header verifies. Use it for every AI job endpoint and keep translation gating unchanged.

- [ ] **Step 4: Add job creation, polling, result, and export routes**

Creation order is: rate limit, AI configured, Pro token, checked URL, disk room, queue capacity, `await asyncio.to_thread(downloader.extract_info, url)`, job create, submit. Use metadata title and canonical `webpage_url` when available. Map `ProbeError` to 422.

Status returns `job.public()`. Result returns 409 until done, 404 when absent, and parsed JSON when complete. Export returns a `FileResponse` only for a completed path inside `SUMMARY_JOB_DIR`, with `text/markdown; charset=utf-8`, UTF-8 content disposition, and `Cache-Control: no-store`.

Delete the old summary route. Keep translation at the same path using `AITranslateIn`.

Add `md` to `security.SAFE_EXTS` and map it to `text/markdown` in `CONTENT_TYPES` before serving the export.

- [ ] **Step 5: Integrate lifecycle, health, and cleanup**

The lifespan sweeper calls both stores each interval and shuts down both pools. Health adds `active_ai_jobs` and `local_asr` while retaining current fields. Startup logs paths and feature availability without secrets.

- [ ] **Step 6: Run API and backend suites**

Run: `./.venv/bin/python -m pytest tests/test_api.py -q`

Expected: API tests pass.

Run: `./.venv/bin/python -m pytest tests -q`

Expected: the complete backend suite passes.

---

### Task 6: Browser API Client, Polling, and Timestamp Helpers

**Files:**
- Modify: `frontend/lib/api.ts:14-78,106-195,197-246`
- Create: `frontend/lib/video.ts`
- Modify: `frontend/components/downloader.tsx:65-93,131-154`

**Interfaces:**
- Produces frontend types `SummaryJob`, `SummaryResult`, `TranscriptSegment`, and `SummaryStatus`.
- Produces API methods `createSummaryJob`, `summaryJob`, `summaryResult`, and `summaryExport`.
- Produces `pollSummaryJob()`.
- Produces `formatTimestamp()` and `timestampUrl()`.

- [ ] **Step 1: Read the installed Next.js client-component guide**

Read: `frontend/node_modules/next/dist/docs/01-app/01-getting-started/05-server-and-client-components.md`

Expected: confirm that browser APIs, local state, effects, event handlers, and `localStorage` stay inside files marked `"use client"` or utilities called only by them.

- [ ] **Step 2: Add exact AI job and result types**

In `frontend/lib/api.ts`, replace the old plain summary response with discriminated types matching backend JSON:

```typescript
export type SummaryStatus =
  | "queued" | "extracting" | "transcribing" | "summarizing"
  | "done" | "error" | "expired";

export interface TranscriptSegment {
  start_seconds: number;
  end_seconds: number;
  text: string;
}

export interface SummaryJob {
  job_id: string;
  status: SummaryStatus;
  title: string;
  percent: number;
  error: string | null;
  transcript_source: "manual_caption" | "automatic_caption" | "local_whisper" | null;
  transcript_lang: string | null;
  ready: boolean;
  expires_at: number;
}

export interface SummaryResult {
  title: string;
  source_url: string;
  output_language: "zh-CN" | "en";
  overview: string;
  key_points: Array<{ text: string; start_seconds: number; end_seconds: number; evidence: string }>;
  chapters: Array<{ title: string; start_seconds: number; summary: string }>;
  core_knowledge: string[];
  target_audience: string;
  transcript: {
    source: "manual_caption" | "automatic_caption" | "local_whisper";
    language: string;
    duration: number | null;
    segments: TranscriptSegment[];
  };
  model: string;
}
```

- [ ] **Step 3: Implement API methods, authenticated blob export, and polling**

Add:

```typescript
createSummaryJob: (input: {
  url: string;
  title?: string | null;
  source_lang: string;
  output_lang: "zh-CN" | "en";
}) => request<SummaryJob>("/api/ai/jobs", {
  method: "POST",
  body: JSON.stringify(input),
}),
summaryJob: (id: string) => request<SummaryJob>(`/api/ai/jobs/${id}`),
summaryResult: (id: string) => request<SummaryResult>(`/api/ai/jobs/${id}/result`),
```

Implement `summaryExport(id)` with `fetch`, the current bearer token, shared JSON error parsing, and a `Blob` return. Do not set JSON content type on the export GET.

Implement `pollSummaryJob` with the same stop-function contract as `pollJob`. Stop on `done`, `error`, `expired`, or 404. Continue after temporary network failures. Use the summary-specific expired error `总结任务已过期，请重新生成`.

- [ ] **Step 4: Add universal timestamp helpers**

In `frontend/lib/video.ts`, implement:

```typescript
export function formatTimestamp(seconds: number): string
export function timestampUrl(sourceUrl: string, seconds: number): string | null
```

`formatTimestamp` emits `M:SS` or `H:MM:SS`. `timestampUrl` sets a `t=<seconds>s` query for YouTube/youtu.be and `t=<seconds>` for bilibili.com. It returns `null` for invalid or unsupported URLs and never throws.

- [ ] **Step 5: Clear stale probe state as soon as the input changes**

In `Downloader`, replace the raw input setter with a handler that updates the text and, when a probe exists, stops current polling, clears local probe/job/error state, and calls `onProbed?.(null)`. The clear button must also call `onProbed?.(null)`.

- [ ] **Step 6: Run frontend static checks**

Run: `npm run lint`

Expected: no ESLint errors.

Run: `npm run build`

Expected: Next.js production build and strict TypeScript checks pass.

---

### Task 7: AI Panel and Grounded Summary Result UI

**Files:**
- Modify: `frontend/components/ai-panel.tsx`
- Create: `frontend/components/summary-result.tsx`
- Modify: `frontend/components/icons.tsx` only if an existing path cannot express export or navigation.

**Interfaces:**
- Consumes: Task 6 API types, methods, polling, and timestamp helpers.
- Produces: the complete approved summary controls, progress, result, transcript, and export flow.
- Preserves: the current subtitle translation tab and copy action.

- [ ] **Step 1: Replace summary state with the asynchronous state model**

Use these state values in `AIPanel`:

```typescript
const [sourceLang, setSourceLang] = React.useState("auto");
const [outputLang, setOutputLang] = React.useState<"zh-CN" | "en">("zh-CN");
const [summaryJob, setSummaryJob] = React.useState<SummaryJob | null>(null);
const [result, setResult] = React.useState<SummaryResult | null>(null);
const summaryStopRef = React.useRef<(() => void) | null>(null);
```

An effect keyed by `probe?.url` stops polling and clears job, result, summary error, and selectors that depend on available captions. The unmount cleanup also stops polling. Translation keeps its own `busy` and SRT state so summary polling does not block translation state updates.

- [ ] **Step 2: Implement generation, result retrieval, and export**

The summary action creates the job and starts `pollSummaryJob`. On a `done` update, fetch `summaryResult` once and store it. Map 402 to the existing upgrade flow. Display other `ApiError` messages directly.

The export action calls `summaryExport`, creates a temporary object URL, clicks an in-memory anchor with a sanitized `${title}-AI-summary.md` filename, revokes the URL, and reports a safe download error if fetch fails.

- [ ] **Step 3: Add selectors, privacy copy, and phase progress**

The summary tab shows:

- `Auto` plus unique manual and automatic caption language codes.
- `简体中文` and `English` output options.
- A note that transcript text is sent to Anthropic, while fallback speech recognition runs locally and temporary audio is deleted.
- Current-video title.
- Progress bar and labels: `排队中`, `正在提取字幕`, `正在本地识别语音`, `正在生成总结`, `已完成`, `失败`, `已过期`.
- Transcript-source badges: `创作者字幕`, `自动字幕`, `本地 Whisper`.

Do not claim a fixed completion time.

- [ ] **Step 4: Render grounded results in a focused component**

`SummaryResultView` receives:

```typescript
{
  result: SummaryResult;
  exporting: boolean;
  onExport: () => void;
}
```

Render overview, key points with timestamps/evidence, chapters, core knowledge, target audience, source badge/language, and a collapsed full transcript. Supported timestamps open a new source tab with `rel="noreferrer"`; unsupported timestamps scroll to the closest transcript segment using a stable segment element ID.

Use semantic headings, buttons for local jumps, links for external jumps, and readable focus styles. Keep all generated text as React text nodes.

- [ ] **Step 5: Run frontend lint and production build**

Run: `npm run lint`

Expected: no ESLint errors.

Run: `npm run build`

Expected: production build succeeds with no TypeScript errors.

---

### Task 8: Configuration, Documentation, and Complete Verification

**Files:**
- Modify: `backend/.env.example`
- Modify: `README.md`
- Review: all changed files

**Interfaces:**
- Documents only behavior that exists after Tasks 1–7.
- Provides final evidence that backend and frontend work together.

- [ ] **Step 1: Update environment example**

Add commented entries for:

```dotenv
# SUMMARY_JOB_DIR=/absolute/path/to/summary-jobs
# SUMMARY_JOB_TTL_SECONDS=3600
# AI_MAX_WORKERS=1
# AI_MAX_QUEUED_JOBS=10
# WHISPER_MODEL=small
# WHISPER_DEVICE=cpu
# WHISPER_COMPUTE_TYPE=int8
```

Keep the existing Anthropic configuration and explain that the Whisper model downloads on the first captionless summary.

- [ ] **Step 2: Update the existing Chinese README**

Update feature overview, prerequisites, optional AI setup, project structure, API table, environment table, validation, current limitations, and privacy notes. State accurately:

- Captions are preferred and local Whisper is fallback.
- Transcript text is sent to Anthropic.
- Temporary audio/results are deleted by job cleanup.
- The backend remains single-process and summaries do not survive process restarts.
- AI job status/result/export endpoints require Pro authentication.
- Local Whisper has CPU, RAM, disk-cache, and first-run download costs.

Remove claims that AI requires existing captions and replace the old summary route in the API table.

- [ ] **Step 3: Run the complete backend suite**

Run: `./.venv/bin/python -m pytest tests -q`

Expected: all backend tests pass with no network, model, or Anthropic call.

- [ ] **Step 4: Run frontend lint and production build**

Run: `npm run lint && npm run build`

Expected: both commands exit zero.

- [ ] **Step 5: Run a mocked local API smoke test**

Use `TestClient` in a one-off Python command or a focused pytest test to:

1. Redeem a Pro token.
2. Stub metadata, transcript, and Anthropic summary functions.
3. Create an AI job.
4. Run or await its worker.
5. Poll status to `done`.
6. Retrieve JSON result.
7. Retrieve Markdown export and verify `text/markdown` plus attachment headers.

Run the focused smoke test with `./.venv/bin/python -m pytest tests/test_api.py -q -k ai_job_end_to_end`.

Expected: one smoke test passes and all external work remains mocked.

- [ ] **Step 6: Inspect the final working tree**

Run: `git status --short && git diff --check`

Expected: only intended source, test, configuration, plan, specification, and README files are changed; `git diff --check` reports no whitespace errors. Do not commit.
