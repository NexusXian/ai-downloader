# AI Video Summary Design

**Date:** 2026-08-02
**Status:** Approved design awaiting written-spec review

## Context

Universal Video Downloader already offers a synchronous, Pro-only Anthropic summary endpoint. It selects one existing caption track, truncates the text, and returns a fixed Simplified Chinese plain-text summary. It cannot summarize videos without captions, preserve timestamp evidence, process long transcripts completely, report progress, retain results across a refresh, or export notes.

This design replaces that summary path with a focused downloader-native workflow. It does not attempt to match the full feature sets of NoteGPT or BibiGPT.

## Goals

- Generate structured summaries for videos with or without captions.
- Prefer creator or automatic captions and use local Whisper only as a fallback.
- Preserve transcript timestamps and connect summary claims to supporting excerpts.
- Process long videos without silently truncating their transcripts.
- Run extraction, transcription, and summarization asynchronously with progress states.
- Support Simplified Chinese and English summary output.
- Keep results temporarily and export them as Markdown.
- Preserve the existing Pro access requirement and Anthropic provider.
- Keep download jobs and AI jobs isolated from each other.

## Non-goals

- AI chat
- Slides or flashcards
- Accounts or permanent summary history
- Database or external queue
- Multi-model selection
- Local-file uploads
- Batch, playlist, or collection summaries
- Server-side cancellation
- Changes to subtitle translation beyond adapting it to shared transcript utilities

## Architecture

### Summary job store

Add a summary-specific in-memory job store and worker pool. It follows the existing download-job pattern but owns separate state, storage, cleanup, and concurrency.

Summary phases are:

- `queued`
- `extracting`
- `transcribing`
- `summarizing`
- `done`
- `error`
- `expired`

Each job has a random UUID, request settings, phase, progress, safe public error, transcript metadata, artifact paths, creation time, update time, and expiry time. Internal URLs and filesystem paths are never included in public responses.

Summary artifacts live under a separate `SUMMARY_JOB_DIR`, defaulting to `backend/_summary_jobs`. A dedicated sweeper expires in-memory jobs and removes their directories. It also removes orphan directories left by process restarts. Download-job cleanup must not inspect this directory.

The AI worker pool is separate from the download worker pool. Its default concurrency is one because local transcription is CPU- and memory-intensive.

### Transcript service

Create one transcript service used by summarization and subtitle translation. It relies on centralized downloader metadata and media helpers rather than calling private yt-dlp helpers from AI code.

The service:

1. Probes and validates the canonical source through the normal duration, playlist, DRM, cookie, and URL safety path.
2. Selects the requested caption language or applies the existing language preference when `auto` is selected.
3. Prefers creator captions over automatic captions for the same language.
4. Parses SRT, VTT, JSON3, and SRV caption data into one normalized segment model.
5. Falls back to a temporary best-audio download when no usable caption track exists.
6. Runs `faster-whisper` locally and converts its segments into the same model.
7. Deletes temporary audio after transcription succeeds or fails.

Normalized transcript segments contain:

- `start_seconds`
- `end_seconds`
- `text`

Transcript metadata contains:

- Source type: `manual_caption`, `automatic_caption`, or `local_whisper`
- Detected or selected language
- Video duration
- Segment count

Empty cues, formatting tags, duplicate whitespace, and non-dialogue caption markup are removed without discarding timestamps.

### Local Whisper

Use `faster-whisper` as the local speech-to-text engine. Defaults are:

- Model: `small`
- Device: `cpu`
- Compute type: `int8`

The model loads lazily on the first captionless job and is reused within the process. Its model files are downloaded to the normal local model cache on first use. Model download or initialization failures produce a safe, actionable job error.

### Summary service

Anthropic remains the only summary provider. Model instructions are placed in a system message. Titles and transcript content are marked as untrusted source material and placed in delimited user content.

Long transcripts are divided at segment boundaries using bounded input estimates. Each chunk retains its original timestamp range. The service generates a compact structured partial summary for each chunk, then generates one final result from all partial summaries. A transcript that fits within one bounded request skips the partial-summary stage.

The provider response is schema-validated. If the first response is malformed, the service performs one repair request using the validation failure and original response. A second invalid response fails the job.

Timestamps returned by the provider must fall within the video duration. Supporting excerpts are resolved back to normalized transcript segments rather than trusted directly from generated text.

## Result model

The completed JSON result contains:

- `title`
- `source_url`
- `output_language`
- `overview`
- `key_points`
  - `text`
  - `start_seconds`
  - `end_seconds`
  - `evidence`
- `chapters`
  - `title`
  - `start_seconds`
  - `summary`
- `core_knowledge`
- `target_audience`
- `transcript`
  - source metadata
  - normalized segments
- `model`

`core_knowledge` is a list of concise statements. The result uses the selected output language, while transcript text stays in its source language.

## API

### Create summary job

`POST /api/ai/jobs`

Request:

- `url`: required HTTP or HTTPS source URL
- `title`: optional display title, maximum 200 characters
- `source_lang`: caption language code or `auto`
- `output_lang`: `zh-CN` or `en`

The route requires configured Anthropic access and a valid Pro token. It applies URL validation, normal probing and duration limits, rate limiting, free-disk checks, and AI queue capacity checks before returning `202` with the job ID and initial state.

### Read job state

`GET /api/ai/jobs/{job_id}`

Returns public phase, progress, timestamps, expiry, transcript source metadata when available, and a safe error when failed. It does not return the full transcript or summary while polling.

### Read completed result

`GET /api/ai/jobs/{job_id}/result`

Returns the structured result only when the job is complete. Incomplete jobs return a conflict response; missing or expired jobs return not found.

### Export Markdown

`GET /api/ai/jobs/{job_id}/export`

Returns a UTF-8 Markdown attachment containing metadata, overview, timestamped key points with evidence, chapters, core knowledge, target audience, and the full timestamped transcript.

All AI job endpoints require the Pro token. The browser downloads exports as an authenticated blob rather than navigating directly.

The old synchronous `POST /api/ai/summary` route and matching browser client are removed after migration. `POST /api/ai/translate` remains available.

## Processing flow

1. The user probes a video normally.
2. The AI panel submits the canonical URL, selected transcript language, and selected output language.
3. The server creates a summary job and returns immediately.
4. The worker resolves metadata and attempts caption extraction.
5. If captions are unavailable or unusable, the worker downloads temporary audio and runs local Whisper.
6. The normalized transcript is divided into bounded chunks when necessary.
7. Anthropic generates partial summaries and the final structured result.
8. The server validates timestamps and attaches supporting transcript excerpts.
9. JSON and Markdown artifacts are written atomically.
10. The browser fetches and renders the result.
11. The sweeper deletes the job and artifacts after the configured TTL.

Progress is monotonic. Phase changes provide the main user feedback; percentages are estimated within each phase and never claim media-time accuracy.

## User interface

The existing AI panel remains attached to the current probe result.

Before generation, it displays:

- Transcript language selector with `Auto` and available caption languages
- Summary language selector with `简体中文` and `English`
- Generate action
- Privacy note stating that transcript text is sent to Anthropic and local Whisper audio remains on the server

While running, it displays phase, estimated progress, and transcript source once known. Polling tolerates temporary network errors and stops when the selected video changes or the component unmounts.

The completed result displays:

- Overview
- Timestamped key points and supporting excerpts
- Chapter outline
- Core knowledge
- Target audience
- Transcript source and language badge
- Full timestamped transcript
- Markdown export action

YouTube and Bilibili timestamp helpers open the original video near the selected time. For unknown URL formats, selecting a timestamp reveals or focuses the supporting local transcript segment instead.

Changing or clearing the downloader input clears the parent probe and all AI job/result state. A result from one video must never remain visible for another video.

## Error handling

Public errors distinguish these cases without exposing provider details:

- AI provider not configured
- Pro access required or invalid
- Unsupported or unsafe URL
- Video duration exceeds the configured limit
- Caption extraction failed
- No usable captions and local ASR unavailable
- Audio extraction failed
- Whisper model initialization or transcription failed
- Summary provider failed or timed out
- Structured output remained invalid after repair
- AI queue is full
- Insufficient disk space
- Job expired

Failures delete partial audio and incomplete result artifacts. Provider stack traces may be logged server-side, but transcript text, prompts, API keys, signed caption URLs, and raw provider responses are not logged.

## Security and privacy

- Reuse normal downloader validation for metadata and media access.
- Do not trust caption URLs without the same network controls required for yt-dlp traffic.
- Keep Anthropic and Whisper configuration server-side.
- Separate source text from system instructions and explicitly treat source content as data.
- Escape all displayed content through React text rendering.
- Validate output types, lengths, timestamps, and item counts before storage.
- Require Pro access for creation, polling, result, and export endpoints.
- Use unguessable job IDs and owner-only job directories.
- Do not persist results beyond the configured TTL.
- Disclose the Anthropic transcript transfer in the UI.

Existing deployment requirements for trusted proxy headers and network egress controls remain in force.

## Configuration

Add these environment settings:

- `SUMMARY_JOB_DIR`, default `backend/_summary_jobs`
- `SUMMARY_JOB_TTL_SECONDS`, default `3600`
- `AI_MAX_WORKERS`, default `1`
- `AI_MAX_QUEUED_JOBS`, default `10`
- `WHISPER_MODEL`, default `small`
- `WHISPER_DEVICE`, default `cpu`
- `WHISPER_COMPUTE_TYPE`, default `int8`

Document the first-use Whisper model download and expected resource cost in the existing project documentation. Add the summary artifact directory to ignore rules.

## Testing and validation

Backend automated coverage will include:

- Manual-caption preference and requested-language selection
- Automatic-caption fallback
- SRT, VTT, JSON3, and SRV normalization
- Captionless fallback to mocked Whisper output
- Whisper timestamp and language conversion
- Temporary-audio cleanup on success and failure
- Chunk boundaries, complete transcript coverage, and timestamp preservation
- Single-pass and multi-stage summary generation
- Structured response validation and one repair attempt
- Evidence resolution against transcript segments
- Pro authorization on every AI job endpoint
- Queue capacity, progress, completion, failure, expiry, and orphan cleanup
- Result and authenticated Markdown export behavior
- Safe public errors and absence of sensitive response fields
- Existing subtitle translation behavior after transcript-service migration

Automated tests mock external media, Whisper, and Anthropic calls. They do not require network access, model downloads, or paid API usage.

Final verification consists of:

- Full backend pytest suite
- Frontend lint
- Strict TypeScript production build
- Local API smoke test covering job creation, polling, result retrieval, and export with mocked external services

## Migration and compatibility

- Existing download, batch, file, Pro redemption, and subtitle translation APIs remain unchanged.
- The frontend moves from the synchronous summary call to the new job client and polling helper.
- The old summary route, response type, and unused synchronous summary function are deleted rather than retained as legacy code.
- Existing AI tests are updated to the job workflow, and regression coverage protects translation and download behavior.
