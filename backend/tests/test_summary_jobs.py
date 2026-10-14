from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ai  # noqa: E402
import config  # noqa: E402
import summary_jobs  # noqa: E402
import transcripts  # noqa: E402


def test_import_does_not_create_summary_artifact_directory(tmp_path):
    summary_dir = tmp_path / "import-only-summary-jobs"
    env = os.environ.copy()
    env["SUMMARY_JOB_DIR"] = str(summary_dir)

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import config; import summary_jobs; "
            "assert not config.SUMMARY_JOB_DIR.exists()",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert not summary_dir.exists()


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SUMMARY_JOB_DIR", tmp_path / "summary-jobs")
    instance = summary_jobs.SummaryJobStore()
    yield instance
    instance.shutdown()


def _create_job(store, **overrides):
    values = {
        "url": "https://example.com/v",
        "title": "Title",
        "source_lang": "auto",
        "output_lang": "en",
        "source_info": {"duration": 10},
    }
    values.update(overrides)
    return store.create(**values)


def _transcript():
    return transcripts.Transcript(
        source="manual_caption",
        language="en",
        duration=10,
        segments=(
            transcripts.TranscriptSegment(1.25, 2.5, "Evidence one"),
            transcripts.TranscriptSegment(4, 6, "Evidence two"),
        ),
    )


def _complete_markdown():
    return "# Title\n\n- [00:00:01.250] Point\n"


def _mock_success(monkeypatch):
    monkeypatch.setattr(
        summary_jobs.transcripts,
        "build_transcript",
        lambda *_args, **_kwargs: _transcript(),
    )
    monkeypatch.setattr(
        summary_jobs.ai,
        "summarize_transcript",
        lambda *_args, **_kwargs: _complete_markdown(),
    )


def test_public_job_hides_url_metadata_and_paths(store):
    job = _create_job(
        store,
        url="https://example.com/private",
        source_info={"duration": 10, "secret": "metadata"},
    )
    store.update(
        job.id,
        summary_path=Path("/private/summary.md"),
        transcript_path=Path("/private/transcript.json"),
        mind_map_path=Path("/private/mind_map.json"),
    )

    payload = store.get(job.id).public()

    assert payload["status"] == "queued"
    assert set(payload) == {
        "job_id",
        "status",
        "title",
        "percent",
        "error",
        "transcript_source",
        "transcript_lang",
        "ready",
        "transcript_ready",
        "mind_map_status",
        "mind_map_percent",
        "mind_map_error",
        "mind_map_ready",
        "created_at",
        "updated_at",
        "expires_at",
    }
    assert payload["expires_at"] == job.created_at + config.SUMMARY_JOB_TTL_SECONDS
    assert "example.com" not in str(payload)
    assert "metadata" not in str(payload)
    assert "/private" not in str(payload)


def test_public_timestamps_start_equal_and_update_on_mutation(store, monkeypatch):
    job = _create_job(store)
    initial = store.get(job.id).public()

    assert isinstance(initial["created_at"], float)
    assert isinstance(initial["updated_at"], float)
    assert initial["updated_at"] == initial["created_at"]

    monkeypatch.setattr(summary_jobs.time, "time", lambda: initial["created_at"] + 10)
    store.update(job.id, status="extracting", percent=5)
    updated = store.get(job.id).public()

    assert updated["created_at"] == initial["created_at"]
    assert updated["updated_at"] == initial["created_at"] + 10


def test_failure_updates_public_updated_at(store, monkeypatch):
    job = _create_job(store)
    created_at = store.get(job.id).created_at
    monkeypatch.setattr(summary_jobs.time, "time", lambda: created_at + 20)

    store._fail(job.id, "safe failure", config.SUMMARY_JOB_DIR / job.id)

    failed = store.get(job.id).public()
    assert failed["status"] == "error"
    assert failed["created_at"] == created_at
    assert failed["updated_at"] == created_at + 20


def test_create_and_get_return_deep_defensive_snapshots(store):
    source_info = {"duration": 10, "nested": {"language": "en"}}
    created = _create_job(store, source_info=source_info)
    job_id = created.id

    source_info["nested"]["language"] = "changed-input"
    created.id = "changed-id"
    created.source_info["nested"]["language"] = "changed-snapshot"
    created.summary_path = Path("/changed/summary.md")

    stored = store.get(job_id)
    assert stored.id == job_id
    assert stored.source_info == {"duration": 10, "nested": {"language": "en"}}
    assert stored.summary_path is None

    stored.status = "done"
    stored.source_info["nested"]["language"] = "changed-again"
    fresh = store.get(job_id)
    assert fresh.status == "queued"
    assert fresh.source_info["nested"]["language"] == "en"


def test_completed_snapshot_always_has_ready_path(store, monkeypatch):
    _mock_success(monkeypatch)
    job = _create_job(store)
    queued = store.get(job.id)

    store._run(job.id)

    finished = store.get(job.id)
    assert queued.status == "queued"
    assert queued.summary_path is None
    assert finished.status == "done"
    assert finished.summary_path is not None
    assert finished.public()["ready"] is True


def test_progress_never_decreases(store):
    job = _create_job(store)
    store.update(job.id, percent=45.5)
    store.update(job.id, percent=12)
    assert store.get(job.id).percent == 45.5


def test_queue_capacity_counts_only_active_jobs(store, monkeypatch):
    monkeypatch.setattr(config, "AI_MAX_QUEUED_JOBS", 1)
    first = _create_job(store)
    assert store.count_active() == 1
    assert store.can_accept() is False

    store.update(first.id, status="done")

    assert store.count_active() == 0
    assert store.can_accept() is True


def test_concurrent_create_admits_at_most_configured_capacity(store, monkeypatch):
    capacity = 3
    contenders = 12
    monkeypatch.setattr(config, "AI_MAX_QUEUED_JOBS", capacity)
    barrier = threading.Barrier(contenders)
    results_lock = threading.Lock()
    admitted = []
    rejected = []

    def attempt_create(index):
        barrier.wait()
        try:
            result = _create_job(store, title=f"Title {index}")
        except Exception as exc:
            with results_lock:
                rejected.append(exc)
        else:
            with results_lock:
                admitted.append(result)

    threads = [
        threading.Thread(target=attempt_create, args=(index,))
        for index in range(contenders)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert not any(thread.is_alive() for thread in threads)
    assert len(admitted) == capacity
    assert len(rejected) == contenders - capacity
    assert all(isinstance(exc, summary_jobs.SummaryQueueFull) for exc in rejected)
    assert all(str(exc) == "AI 总结队列已满，请稍后重试" for exc in rejected)
    assert store.count_active() == capacity


def test_submit_uses_configured_ai_worker_pool(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SUMMARY_JOB_DIR", tmp_path / "summary-jobs")
    monkeypatch.setattr(config, "AI_MAX_WORKERS", 2)
    instance = summary_jobs.SummaryJobStore()
    job = _create_job(instance)
    called = []
    finished = threading.Event()

    def run(job_id):
        called.append((job_id, threading.current_thread().name))
        finished.set()

    monkeypatch.setattr(instance, "_run", run)
    try:
        instance.submit(job)
        assert finished.wait(timeout=2)
        assert instance._pool._max_workers == 2
        assert called == [(job.id, called[0][1])]
        assert called[0][1].startswith("ai")
    finally:
        instance.shutdown()


def test_submit_failure_rolls_back_admitted_job_and_directory(store, monkeypatch):
    job = _create_job(store)
    job_dir = config.SUMMARY_JOB_DIR / job.id
    job_dir.mkdir(mode=0o700)
    (job_dir / "partial").write_text("data", encoding="utf-8")

    def fail_submit(*_args, **_kwargs):
        raise RuntimeError("broken executor secret")

    monkeypatch.setattr(store._pool, "submit", fail_submit)

    with pytest.raises(
        summary_jobs.SummarySubmissionError,
        match="^AI 总结服务暂时不可用，请稍后重试$",
    ) as exc_info:
        store.submit(job)

    assert "secret" not in str(exc_info.value)
    assert store.get(job.id) is None
    assert store.count_active() == 0
    assert not job_dir.exists()


def test_worker_writes_markdown_atomically(store, monkeypatch):
    _mock_success(monkeypatch)
    replacements = []
    original_replace = Path.replace

    def replace(source, target):
        replacements.append((source, Path(target)))
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", replace)
    job = _create_job(store)

    store._run(job.id)

    finished = store.get(job.id)
    assert finished.status == "done"
    assert finished.percent == 100
    assert finished.transcript_source == "manual_caption"
    assert finished.transcript_lang == "en"
    assert store.read_summary(job.id) == _complete_markdown().encode()
    assert finished.summary_path.read_text(encoding="utf-8") == _complete_markdown()
    assert not list(finished.summary_path.parent.glob("*.tmp"))
    assert {(source.name, target.name) for source, target in replacements} == {
        ("summary.md.tmp", "summary.md"),
        ("transcript.json.tmp", "transcript.json"),
    }
    assert all(source.parent == target.parent for source, target in replacements)
    assert (finished.summary_path.parent.stat().st_mode & 0o777) == 0o700
    assert finished.public()["ready"] is True


def test_worker_maps_callbacks_to_monotonic_phase_progress(store, monkeypatch):
    observed = []
    job = _create_job(store)

    def snapshot():
        current = store.get(job.id)
        observed.append((current.status, current.percent))

    def build_transcript(_url, _info, _preferred, _job_dir, on_phase):
        for phase, percent in (
            ("downloading_audio", 0),
            ("downloading_audio", 100),
            ("transcribing", 0),
            ("transcribing", 100),
        ):
            on_phase(phase, percent)
            snapshot()
        return _transcript()

    def summarize(_title, _url, _value, _lang, on_progress):
        snapshot()
        for percent in (70, 60, 100):
            on_progress(percent)
            snapshot()
        return _complete_markdown()

    monkeypatch.setattr(summary_jobs.transcripts, "build_transcript", build_transcript)
    monkeypatch.setattr(summary_jobs.ai, "summarize_transcript", summarize)

    store._run(job.id)

    assert [percent for _status, percent in observed] == [
        5,
        50,
        50,
        50,
        55,
        70,
        70,
        95,
    ]
    assert observed[0][0] == "extracting"
    assert any(status == "transcribing" for status, _percent in observed)
    assert any(status == "summarizing" for status, _percent in observed)
    assert all(5 <= percent <= 95 for _status, percent in observed)


def test_worker_failure_exposes_safe_error_and_removes_partial_files(
    store, monkeypatch,
):
    monkeypatch.setattr(
        summary_jobs.transcripts,
        "build_transcript",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            transcripts.TranscriptError("语音识别失败")
        ),
    )
    job = _create_job(store, output_lang="zh-CN")

    store._run(job.id)

    failed = store.get(job.id)
    assert failed.status == "error"
    assert failed.error == "语音识别失败"
    assert failed.finished_at is not None
    assert not (config.SUMMARY_JOB_DIR / job.id).exists()


def test_ai_domain_error_is_preserved_exactly(store, monkeypatch):
    monkeypatch.setattr(
        summary_jobs.transcripts,
        "build_transcript",
        lambda *_args, **_kwargs: _transcript(),
    )
    monkeypatch.setattr(
        summary_jobs.ai,
        "summarize_transcript",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ai.AIError("x" * 500)),
    )
    job = _create_job(store)

    store._run(job.id)

    assert store.get(job.id).error == "x" * 500


def test_unexpected_worker_failure_uses_fixed_error_and_removes_partial_artifacts(
    store, monkeypatch,
):
    _mock_success(monkeypatch)
    original_replace = Path.replace

    def fail_second_replace(source, target):
        if source.name == "summary.md.tmp":
            raise OSError("/private/path?token=secret")
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_second_replace)
    job = _create_job(store)

    store._run(job.id)

    failed = store.get(job.id)
    assert failed.status == "error"
    assert failed.error == "AI 总结失败，请稍后重试"
    assert "private" not in str(failed.public())
    assert "secret" not in str(failed.public())
    assert not (config.SUMMARY_JOB_DIR / job.id).exists()


def test_worker_cleanup_failure_is_safe_and_retained_for_retry(
    store, monkeypatch, caplog,
):
    monkeypatch.setattr(
        summary_jobs.transcripts,
        "build_transcript",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            transcripts.TranscriptError("语音识别失败")
        ),
    )
    job = _create_job(store)
    job_dir = config.SUMMARY_JOB_DIR / job.id

    def fail_delete(*_args, **_kwargs):
        raise PermissionError("secret deletion detail")

    monkeypatch.setattr(summary_jobs.shutil, "rmtree", fail_delete)

    with caplog.at_level(logging.WARNING, logger=summary_jobs.__name__):
        store._run(job.id)

    failed = store.get(job.id)
    assert failed.status == "error"
    assert failed.error == "语音识别失败"
    assert job_dir.exists()
    assert job.id in caplog.text
    assert job.url not in caplog.text
    assert "secret deletion detail" not in caplog.text


def test_read_summary_fails_before_job_is_ready(store):
    job = _create_job(store)
    with pytest.raises(RuntimeError, match="not ready"):
        store.read_summary(job.id)


def test_read_summary_maps_concurrent_artifact_removal_to_not_ready(
    store, monkeypatch,
):
    _mock_success(monkeypatch)
    job = _create_job(store)
    store._run(job.id)
    summary_path = store.get(job.id).summary_path
    original_open = Path.open

    def remove_then_open(path, *args, **kwargs):
        if path == summary_path:
            path.unlink()
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", remove_then_open)

    with pytest.raises(RuntimeError, match="not ready"):
        store.read_summary(job.id)


def test_read_summary_holds_lock_until_canonical_bytes_are_loaded(
    store, monkeypatch,
):
    _mock_success(monkeypatch)
    job = _create_job(store)
    store._run(job.id)
    store.update(job.id, created_at=0)
    summary_path = store.get(job.id).summary_path
    expected = summary_path.read_bytes()
    original_open = Path.open
    read_started = threading.Event()
    allow_read = threading.Event()
    sweep_started = threading.Event()
    sweep_finished = threading.Event()
    result = []
    errors = []

    def blocking_open(path, *args, **kwargs):
        if path == summary_path and args and args[0] == "rb":
            read_started.set()
            assert allow_read.wait(timeout=2)
        return original_open(path, *args, **kwargs)

    def read_summary():
        try:
            result.append(store.read_summary(job.id))
        except Exception as exc:
            errors.append(exc)

    def sweep():
        sweep_started.set()
        store.sweep(ttl=0)
        sweep_finished.set()

    monkeypatch.setattr(Path, "open", blocking_open)
    reader = threading.Thread(target=read_summary)
    reader.start()
    assert read_started.wait(timeout=2)

    sweeper = threading.Thread(target=sweep)
    sweeper.start()
    assert sweep_started.wait(timeout=2)
    assert not sweep_finished.wait(timeout=0.05)
    assert summary_path.is_file()

    allow_read.set()
    reader.join(timeout=2)
    sweeper.join(timeout=2)

    assert not errors
    assert result == [expected]
    assert sweep_finished.is_set()
    assert store.get(job.id) is None
    assert not summary_path.exists()


def test_worker_preserves_generated_markdown(store, monkeypatch):
    _mock_success(monkeypatch)
    job = _create_job(store)
    store._run(job.id)

    markdown = store.get(job.id).summary_path.read_text(encoding="utf-8")
    assert markdown == _complete_markdown()


def test_summary_directories_are_owner_only(store):
    assert (config.SUMMARY_JOB_DIR.stat().st_mode & 0o777) == 0o700


@pytest.mark.parametrize("status", ["done", "error"])
def test_sweep_removes_expired_terminal_jobs_and_artifacts(store, status):
    job = _create_job(store)
    job_dir = config.SUMMARY_JOB_DIR / job.id
    job_dir.mkdir(mode=0o700)
    (job_dir / "artifact").write_text("data", encoding="utf-8")
    store.update(job.id, status=status, created_at=time.time() - 100)

    assert store.sweep(ttl=10) == 1
    assert store.get(job.id) is None
    assert not job_dir.exists()


@pytest.mark.parametrize(
    "status", ["queued", "extracting", "transcribing", "summarizing"],
)
def test_sweep_preserves_active_jobs_and_artifacts(store, status):
    job = _create_job(store)
    job_dir = config.SUMMARY_JOB_DIR / job.id
    job_dir.mkdir(mode=0o700)
    (job_dir / "working").write_text("data", encoding="utf-8")
    old = time.time() - 100
    os.utime(job_dir, (old, old))
    store.update(job.id, status=status, created_at=old)

    assert store.sweep(ttl=10) == 0
    assert store.get(job.id).id == job.id
    assert job_dir.exists()


def test_sweep_retries_failed_deletion_without_counting_it(
    store, monkeypatch, caplog,
):
    job = _create_job(store)
    job_dir = config.SUMMARY_JOB_DIR / job.id
    job_dir.mkdir(mode=0o700)
    (job_dir / "artifact").write_text("data", encoding="utf-8")
    store.update(job.id, status="done", created_at=time.time() - 100)
    original_rmtree = summary_jobs.shutil.rmtree
    attempts = []

    def fail_once(path, *args, **kwargs):
        attempts.append(path)
        if len(attempts) == 1:
            raise PermissionError("secret deletion detail")
        return original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(summary_jobs.shutil, "rmtree", fail_once)

    with caplog.at_level(logging.WARNING, logger=summary_jobs.__name__):
        assert store.sweep(ttl=10) == 0

    retained = store.get(job.id)
    assert retained.status == "done"
    assert job_dir.exists()
    assert job.id in caplog.text
    assert "secret deletion detail" not in caplog.text

    assert store.sweep(ttl=10) == 1
    assert store.get(job.id) is None
    assert not job_dir.exists()
    assert attempts == [job_dir, job_dir]


def test_sweep_removes_old_orphan_directories(store):
    orphan = config.SUMMARY_JOB_DIR / "orphan"
    orphan.mkdir(mode=0o700)
    (orphan / "summary.md.tmp").write_text("partial", encoding="utf-8")
    old = time.time() - 100
    os.utime(orphan, (old, old))

    assert store.sweep(ttl=10) == 1
    assert not orphan.exists()


def test_disk_has_room_uses_summary_directory(monkeypatch, tmp_path):
    summary_dir = tmp_path / "summary-jobs"
    seen = []
    monkeypatch.setattr(config, "SUMMARY_JOB_DIR", summary_dir)
    monkeypatch.setattr(config, "MIN_FREE_DISK_BYTES", 100)
    monkeypatch.setattr(
        summary_jobs.shutil,
        "disk_usage",
        lambda path: seen.append(path) or SimpleNamespace(free=99),
    )

    assert summary_jobs.disk_has_room() is False
    assert seen == [summary_dir]


def test_disk_has_room_fails_closed_on_oserror(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SUMMARY_JOB_DIR", tmp_path / "summary-jobs")
    monkeypatch.setattr(
        summary_jobs.shutil,
        "disk_usage",
        lambda _path: (_ for _ in ()).throw(OSError("private storage detail")),
    )

    assert summary_jobs.disk_has_room() is False
