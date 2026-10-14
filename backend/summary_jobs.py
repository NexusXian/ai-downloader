from __future__ import annotations

import copy
import json
import logging
import os
import shutil
import stat
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import ai
import config
import transcripts

log = logging.getLogger(__name__)

SummaryStatus = Literal[
    "queued",
    "extracting",
    "transcribing",
    "summarizing",
    "done",
    "error",
    "expired",
]

TRANSCRIPT_FILENAME = "transcript.json"
MIND_MAP_FILENAME = "mind_map.json"

MindMapStatus = Literal["idle", "queued", "generating", "done", "error"]

_MIND_MAP_GENERIC_ERROR = "思维导图生成失败，请稍后重试"
_MIND_MAP_SUBMISSION_ERROR = "思维导图服务暂时不可用，请稍后重试"


class MindMapUnavailable(RuntimeError):
    """The parent summary cannot back a mind map. Message is user-facing."""


class MindMapBusy(RuntimeError):
    """A mind map for this job is already running. Message is user-facing."""


class MindMapSubmissionError(RuntimeError):
    """The worker pool refused the mind map. Message is user-facing."""

_ACTIVE_STATUSES = {"queued", "extracting", "transcribing", "summarizing"}
_MIND_MAP_ACTIVE_STATUSES = {"queued", "generating"}
_GENERIC_ERROR = "AI 总结失败，请稍后重试"
_SUBMISSION_ERROR = "AI 总结服务暂时不可用，请稍后重试"


def _prepare_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)


class SummaryQueueFull(RuntimeError):
    pass


class SummarySubmissionError(RuntimeError):
    pass


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
    summary_path: Path | None = None
    transcript_path: Path | None = None
    mind_map_status: MindMapStatus = "idle"
    mind_map_percent: float = 0.0
    mind_map_error: str | None = None
    mind_map_path: Path | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(init=False)
    finished_at: float | None = None

    def __post_init__(self) -> None:
        self.updated_at = self.created_at

    def public(self) -> dict[str, Any]:
        return {
            "job_id": self.id,
            "status": self.status,
            "title": self.title,
            "percent": round(self.percent, 1),
            "error": self.error,
            "transcript_source": self.transcript_source,
            "transcript_lang": self.transcript_lang,
            "ready": (
                self.status == "done"
                and self.summary_path is not None
            ),
            "transcript_ready": self.transcript_path is not None,
            "mind_map_status": self.mind_map_status,
            "mind_map_percent": round(self.mind_map_percent, 1),
            "mind_map_error": self.mind_map_error,
            "mind_map_ready": (
                self.mind_map_status == "done" and self.mind_map_path is not None
            ),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "expires_at": self.created_at + config.SUMMARY_JOB_TTL_SECONDS,
        }


class SummaryJobStore:
    def __init__(self, *, _initialize_directory: bool = True) -> None:
        self._jobs: dict[str, SummaryJob] = {}
        self._lock = threading.Lock()
        self._root = config.SUMMARY_JOB_DIR
        self._pool = ThreadPoolExecutor(
            max_workers=config.AI_MAX_WORKERS,
            thread_name_prefix="ai",
        )
        if _initialize_directory:
            _prepare_directory(self._root)

    def create(self, **kwargs: Any) -> SummaryJob:
        _prepare_directory(self._root)
        with self._lock:
            active = sum(
                job.status in _ACTIVE_STATUSES for job in self._jobs.values()
            )
            if active >= config.AI_MAX_QUEUED_JOBS:
                raise SummaryQueueFull("AI 总结队列已满，请稍后重试")
            job = SummaryJob(id=uuid.uuid4().hex, **copy.deepcopy(kwargs))
            self._jobs[job.id] = job
            return copy.deepcopy(job)

    def get(self, job_id: str) -> SummaryJob | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return copy.deepcopy(job) if job is not None else None

    def update(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            if "percent" in fields:
                percent = min(100.0, max(0.0, float(fields["percent"])))
                fields["percent"] = max(job.percent, percent)
            changed = False
            for key, value in fields.items():
                if getattr(job, key) != value:
                    setattr(job, key, value)
                    changed = True
            if changed:
                job.updated_at = time.time()

    def count_active(self) -> int:
        with self._lock:
            return sum(job.status in _ACTIVE_STATUSES for job in self._jobs.values())

    def can_accept(self) -> bool:
        return self.count_active() < config.AI_MAX_QUEUED_JOBS

    def submit(self, job: SummaryJob) -> None:
        try:
            self._pool.submit(self._run, job.id)
        except Exception:
            with self._lock:
                self._jobs.pop(job.id, None)
            self._remove_job_dir(self._root / job.id, job.id)
            raise SummarySubmissionError(_SUBMISSION_ERROR) from None

    def submit_mind_map(self, job_id: str) -> SummaryJob:
        """Claim the mind map slot for a finished job, then queue generation.

        The claim happens under the same lock that reads the job so two concurrent
        requests cannot both start a generation for one summary.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise MindMapUnavailable("任务不存在或已过期")
            if job.mind_map_status in ("queued", "generating"):
                raise MindMapBusy("思维导图正在生成中，请稍候")
            if job.status != "done" or job.transcript_path is None:
                raise MindMapUnavailable("请先完成 AI 总结，再生成思维导图")

            job.mind_map_status = "queued"
            job.mind_map_percent = 0.0
            job.mind_map_error = None
            job.mind_map_path = None
            job.updated_at = time.time()
            claimed = copy.deepcopy(job)

        try:
            self._pool.submit(self._run_mind_map, job_id)
        except Exception:
            self.update(
                job_id,
                mind_map_status="error",
                mind_map_error=_MIND_MAP_SUBMISSION_ERROR,
            )
            raise MindMapSubmissionError(_MIND_MAP_SUBMISSION_ERROR) from None
        return claimed

    def _run_mind_map(self, job_id: str) -> None:
        job = self.get(job_id)
        if job is None or job.transcript_path is None:
            return

        try:
            self.update(job_id, mind_map_status="generating", mind_map_percent=15)
            payload = json.loads(self.read_transcript(job_id).decode("utf-8"))
            transcript = transcripts.deserialize(payload)
            self.update(job_id, mind_map_percent=40)

            mind_map = ai.generate_mind_map(
                job.title,
                job.url,
                transcript,
                job.output_lang,
            )
            mind_map_path = self._atomic_write(
                self._root / job_id,
                MIND_MAP_FILENAME,
                json.dumps(mind_map, ensure_ascii=False, separators=(",", ":")),
            )
            self.update(
                job_id,
                mind_map_status="done",
                mind_map_percent=100,
                mind_map_path=mind_map_path,
                mind_map_error=None,
            )
        except (
            transcripts.TranscriptError,
            ai.AIUnavailable,
            ai.AIError,
        ) as exc:
            self._fail_mind_map(job_id, str(exc))
        except (OSError, PermissionError, RuntimeError, ValueError):
            self._fail_mind_map(job_id, _MIND_MAP_GENERIC_ERROR)
        except Exception as exc:
            stack = "".join(traceback.format_tb(exc.__traceback__)).rstrip()
            log.error("mind map job %s crashed\n%s", job_id, stack)
            self._fail_mind_map(job_id, _MIND_MAP_GENERIC_ERROR)

    def _fail_mind_map(self, job_id: str, error: str) -> None:
        self.update(
            job_id,
            mind_map_status="error",
            mind_map_error=error,
            mind_map_path=None,
        )

    def read_mind_map(self, job_id: str) -> bytes:
        return self._read_artifact(
            job_id,
            MIND_MAP_FILENAME,
            "mind_map_path",
            require_done=False,
        )

    def _run(self, job_id: str) -> None:
        job = self.get(job_id)
        if job is None:
            return

        job_dir = self._root / job.id
        try:
            _prepare_directory(self._root)
            job_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            job_dir.chmod(0o700)
            self.update(job_id, status="extracting", percent=5)

            def on_phase(phase: str, percent: float) -> None:
                bounded = min(100.0, max(0.0, float(percent)))
                status = "transcribing" if phase == "transcribing" else "extracting"
                self.update(
                    job_id,
                    status=status,
                    percent=5 + bounded * 0.45,
                )

            transcript = transcripts.build_transcript(
                job.url,
                job.source_info,
                job.source_lang,
                job_dir,
                on_phase,
            )
            transcript_path = self._write_transcript(job_dir, transcript)
            self.update(
                job_id,
                transcript_source=transcript.source,
                transcript_lang=transcript.language,
                transcript_path=transcript_path,
                status="summarizing",
                percent=55,
            )

            def on_summary_progress(percent: float) -> None:
                self.update(
                    job_id,
                    status="summarizing",
                    percent=min(95.0, max(55.0, float(percent))),
                )

            markdown = ai.summarize_transcript(
                job.title,
                job.url,
                transcript,
                job.output_lang,
                on_summary_progress,
            )
            summary_path = self._write_summary(job_dir, markdown)
            self.update(
                job_id,
                status="done",
                percent=100,
                summary_path=summary_path,
                finished_at=time.time(),
            )
        except (transcripts.TranscriptError, ai.AIUnavailable, ai.AIError) as exc:
            self._fail(job_id, str(exc), job_dir)
        except Exception as exc:
            stack = "".join(traceback.format_tb(exc.__traceback__)).rstrip()
            log.error("summary job %s crashed\n%s", job_id, stack)
            self._fail(job_id, _GENERIC_ERROR, job_dir)

    def _atomic_write(self, job_dir: Path, name: str, content: str) -> Path:
        target = job_dir / name
        tmp = job_dir / f"{name}.tmp"

        with tmp.open("w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())

        tmp.replace(target)
        return target

    def _write_summary(
        self,
        job_dir: Path,
        markdown: str,
    ) -> Path:
        return self._atomic_write(job_dir, "summary.md", markdown)

    def _write_transcript(
        self,
        job_dir: Path,
        transcript: transcripts.Transcript,
    ) -> Path:
        payload = json.dumps(
            transcripts.serialize(transcript),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return self._atomic_write(job_dir, TRANSCRIPT_FILENAME, payload)

    def _fail(self, job_id: str, error: str, job_dir: Path) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                failed_at = time.time()
                job.status = "error"
                job.error = error
                job.summary_path = None
                job.transcript_path = None
                job.finished_at = failed_at
                job.updated_at = failed_at
            self._remove_job_dir(job_dir, job_id)

    def _remove_job_dir(self, path: Path, job_id: str) -> bool:
        try:
            shutil.rmtree(path)
            return True
        except FileNotFoundError:
            if not path.exists():
                return True
        except OSError:
            pass
        log.warning("summary job %s artifact cleanup failed", job_id)
        return False

    def read_summary(self, job_id: str) -> bytes:
        return self._read_artifact(job_id, "summary.md", "summary_path")

    def read_transcript(self, job_id: str) -> bytes:
        return self._read_artifact(
            job_id,
            TRANSCRIPT_FILENAME,
            "transcript_path",
            require_done=False,
        )

    def _read_artifact(
        self,
        job_id: str,
        filename: str,
        path_attr: str,
        *,
        require_done: bool = True,
    ) -> bytes:
        with self._lock:
            job = self._jobs.get(job_id)
            expected = self._root / job_id / filename
            if job is None or (require_done and job.status != "done"):
                raise RuntimeError("summary is not ready")
            if getattr(job, path_attr) != expected:
                raise PermissionError("invalid summary path")

            try:
                root = self._root.resolve(strict=True)
                parent_stat = expected.parent.lstat()
                resolved_parent = expected.parent.resolve(strict=True)
                resolved_parent.relative_to(root)
                file_stat = expected.lstat()
            except ValueError:
                raise PermissionError("invalid summary path") from None
            except OSError:
                raise RuntimeError("summary is not ready") from None

            if stat.S_ISLNK(parent_stat.st_mode) or not stat.S_ISDIR(parent_stat.st_mode):
                raise PermissionError("invalid summary path")
            if stat.S_ISLNK(file_stat.st_mode):
                raise PermissionError("invalid summary path")
            if not stat.S_ISREG(file_stat.st_mode):
                raise RuntimeError("summary is not ready")

            try:
                with expected.open("rb") as handle:
                    return handle.read()
            except OSError:
                raise RuntimeError("summary is not ready") from None

    def sweep(self, ttl: int | None = None) -> int:
        ttl = config.SUMMARY_JOB_TTL_SECONDS if ttl is None else ttl
        cutoff = time.time() - ttl
        removed = 0

        with self._lock:
            stale = [
                job
                for job in self._jobs.values()
                if job.created_at < cutoff
                and job.status not in _ACTIVE_STATUSES
                # A finished summary can outlive its TTL while a mind map is still
                # running; removing the dir underneath the worker would fail the job.
                and job.mind_map_status not in _MIND_MAP_ACTIVE_STATUSES
            ]
            for job in stale:
                path = self._root / job.id
                if self._remove_job_dir(path, job.id):
                    job.status = "expired"
                    self._jobs.pop(job.id, None)
                    removed += 1

            if self._root.is_dir():
                known = set(self._jobs)
                for path in self._root.iterdir():
                    if not path.is_dir() or path.name in known:
                        continue
                    try:
                        old = path.stat().st_mtime < cutoff
                    except OSError:
                        log.warning(
                            "summary job %s artifact cleanup failed", path.name,
                        )
                        continue
                    if old and self._remove_job_dir(path, path.name):
                        removed += 1

        if removed:
            log.info("summary sweeper reclaimed %d job(s)", removed)
        return removed

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


def disk_has_room() -> bool:
    try:
        _prepare_directory(config.SUMMARY_JOB_DIR)
        return (
            shutil.disk_usage(config.SUMMARY_JOB_DIR).free
            >= config.MIN_FREE_DISK_BYTES
        )
    except OSError:
        return False


store = SummaryJobStore(_initialize_directory=False)
