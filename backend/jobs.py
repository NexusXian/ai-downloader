"""In-memory job store and download worker.

No database by design. Consequences, stated plainly rather than discovered later:
  * Jobs die with the process. A restart loses in-flight work.
  * The store is per-process, so this app MUST run with a single uvicorn worker --
    with --workers 2 a client polling /api/jobs/{id} can hit a worker that never
    saw the job and get a 404.
Both are acceptable for a self-hosted tool; the exit path is Redis or SQLite.
"""
from __future__ import annotations

import logging
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import config
import downloader
from security import safe_ext, sanitize_filename

log = logging.getLogger(__name__)

Status = Literal["queued", "probing", "downloading", "merging", "converting", "done", "error", "expired"]
Mode = Literal["video", "audio", "subtitle"]


@dataclass
class Job:
    id: str
    url: str
    mode: Mode = "video"
    max_height: int = config.FREE_MAX_HEIGHT
    subtitle_lang: str | None = None
    is_pro: bool = False

    status: Status = "queued"
    title: str | None = None
    thumbnail: str | None = None
    percent: float = 0.0
    downloaded_bytes: int = 0
    total_bytes: int | None = None
    speed: float | None = None
    eta: int | None = None
    error: str | None = None

    filepath: Path | None = None
    filename: str | None = None
    ext: str | None = None
    filesize: int | None = None

    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    def public(self) -> dict[str, Any]:
        """Client-facing view. Never exposes filesystem paths."""
        return {
            "job_id": self.id,
            "status": self.status,
            "mode": self.mode,
            "title": self.title,
            "thumbnail": self.thumbnail,
            "percent": round(self.percent, 1),
            "downloaded_bytes": self.downloaded_bytes,
            "total_bytes": self.total_bytes,
            "speed": self.speed,
            "eta": self.eta,
            "error": self.error,
            "filename": self.filename,
            "filesize": self.filesize,
            "ready": self.status == "done" and self.filepath is not None,
        }


class JobStore:
    """Thread-safe job registry.

    Worker threads mutate jobs from yt-dlp progress hooks while request handlers
    read them, so every access goes through the lock.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(
            max_workers=config.MAX_WORKERS, thread_name_prefix="dl"
        )
        config.JOB_DIR.mkdir(parents=True, exist_ok=True)
        # Downloaded media shouldn't be world-readable while it sits on disk.
        config.JOB_DIR.chmod(0o700)

    # --- registry ---
    def create(self, **kwargs: Any) -> Job:
        job = Job(id=uuid.uuid4().hex, **kwargs)
        with self._lock:
            self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def update(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            for key, value in fields.items():
                setattr(job, key, value)

    def count_active(self) -> int:
        with self._lock:
            return sum(
                1 for j in self._jobs.values()
                if j.status in {"queued", "probing", "downloading", "merging", "converting"}
            )

    # --- execution ---
    def submit(self, job: Job) -> None:
        self._pool.submit(self._run, job.id)

    def _run(self, job_id: str) -> None:
        job = self.get(job_id)
        if job is None:
            return

        job_dir = config.JOB_DIR / job.id
        try:
            job_dir.mkdir(parents=True, exist_ok=True)
            self.update(job_id, status="probing")

            meta = downloader.probe(job.url)
            self.update(job_id, title=meta["title"], thumbnail=meta.get("thumbnail"))

            self.update(job_id, status="downloading")
            # On-disk name is job-scoped and opaque -- never derived from remote
            # metadata (CVE-2024-38519). The pretty title is used only in headers.
            stem = job_dir / "media"

            def on_progress(ev: dict[str, Any]) -> None:
                self._apply_progress(job_id, ev)

            if job.mode == "audio":
                produced = downloader.download_audio(job.url, stem, on_progress)
            elif job.mode == "subtitle":
                lang = job.subtitle_lang or (
                    (meta["subtitles"] or meta["auto_subtitles"] or ["en"])[0]
                )
                produced = downloader.download_subtitle(job.url, stem, lang, on_progress)
            else:
                produced = downloader.download_video(
                    job.url, stem, job.max_height, on_progress
                )

            ext = safe_ext(produced.suffix, "mp4")
            pretty = sanitize_filename(meta["title"])
            self.update(
                job_id,
                status="done",
                percent=100.0,
                filepath=produced,
                ext=ext,
                filename=f"{pretty}.{ext}",
                filesize=produced.stat().st_size,
                speed=None,
                eta=None,
                finished_at=time.time(),
            )
            log.info("job %s done: %s (%s bytes)", job_id, produced.name, produced.stat().st_size)

        except downloader.ProbeError as exc:
            self.update(job_id, status="error", error=str(exc), finished_at=time.time())
            shutil.rmtree(job_dir, ignore_errors=True)
        except Exception as exc:  # noqa: BLE001
            log.exception("job %s crashed", job_id)
            self.update(
                job_id, status="error", error=f"下载失败：{exc}"[:200],
                finished_at=time.time(),
            )
            shutil.rmtree(job_dir, ignore_errors=True)

    def _apply_progress(self, job_id: str, ev: dict[str, Any]) -> None:
        phase = ev.get("phase")
        if phase == "downloading":
            total = ev.get("total_bytes")
            done = ev.get("downloaded_bytes") or 0
            percent = (done / total * 100) if total else 0.0
            self.update(
                job_id,
                status="downloading",
                downloaded_bytes=done,
                total_bytes=total,
                # Cap at 99: the file isn't usable until merge/move completes, and
                # showing 100% while work continues reads as a hang.
                percent=min(percent, 99.0),
                speed=ev.get("speed"),
                eta=ev.get("eta"),
            )
        elif phase == "merging":
            self.update(job_id, status="merging", percent=99.0, speed=None, eta=None)
        elif phase == "converting":
            self.update(job_id, status="converting", percent=99.0, speed=None, eta=None)

    # --- cleanup ---
    def sweep(self, ttl: int | None = None) -> int:
        """Delete expired jobs and their files. Returns how many were reclaimed.

        The safety net for abandoned jobs; the primary cleanup is per-job removal
        on failure and after serving.
        """
        ttl = config.JOB_TTL_SECONDS if ttl is None else ttl
        cutoff = time.time() - ttl
        removed = 0

        with self._lock:
            stale = [
                j for j in self._jobs.values()
                if j.created_at < cutoff
                and j.status not in {"downloading", "merging", "converting", "probing"}
            ]
            for job in stale:
                job.status = "expired"
                self._jobs.pop(job.id, None)

        for job in stale:
            shutil.rmtree(config.JOB_DIR / job.id, ignore_errors=True)
            removed += 1

        # Also reclaim orphaned directories left by a previous process.
        if config.JOB_DIR.is_dir():
            with self._lock:
                known = set(self._jobs)
            for path in config.JOB_DIR.iterdir():
                if not path.is_dir() or path.name in known:
                    continue
                try:
                    if path.stat().st_mtime < cutoff:
                        shutil.rmtree(path, ignore_errors=True)
                        removed += 1
                except OSError:
                    continue

        if removed:
            log.info("sweeper reclaimed %d job(s)", removed)
        return removed

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


def disk_has_room() -> bool:
    """Refuse new work when the disk is nearly full, before starting a download."""
    try:
        return shutil.disk_usage(config.JOB_DIR).free >= config.MIN_FREE_DISK_BYTES
    except OSError:
        return True


store = JobStore()
