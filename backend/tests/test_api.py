"""API tests. Network-touching routes are stubbed so the suite stays fast."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ai  # noqa: E402
import config  # noqa: E402
import downloader  # noqa: E402
import jobs  # noqa: E402
import main  # noqa: E402
import pro  # noqa: E402
import security  # noqa: E402
import summary_jobs  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(security.config, "ALLOW_RESOLVED_NETS", "198.18.0.0/15")
    monkeypatch.setattr(config, "JOB_DIR", tmp_path / "jobs")
    monkeypatch.setattr(config, "SUMMARY_JOB_DIR", tmp_path / "summary-jobs")
    download_store = jobs.JobStore()
    summary_store = summary_jobs.SummaryJobStore()
    monkeypatch.setattr(jobs, "store", download_store)
    monkeypatch.setattr(summary_jobs, "store", summary_store)
    monkeypatch.setattr(main.jobs, "store", jobs.store)
    # Generous limits so unrelated tests don't trip the limiter.
    monkeypatch.setattr(pro, "limiter", pro.RateLimiter(1000, 60))
    monkeypatch.setattr(main.pro, "limiter", pro.limiter)
    # Shut the real pools down through saved bound methods: a test may monkeypatch
    # `shutdown`, and monkeypatch only undoes its patches after this finalizer.
    real_shutdowns = (download_store.shutdown, summary_store.shutdown)
    yield
    for shutdown in real_shutdowns:
        shutdown()


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def pro_token():
    return pro.redeem(config.REDEEM_CODES[0])["token"]


FAKE_META = {
    "url": "https://www.youtube.com/watch?v=x",
    "title": "测试视频",
    "duration": 60,
    "thumbnail": None,
    "uploader": "tester",
    "extractor": "Youtube",
    "is_live": False,
    "live_status": None,
    "formats": [
        {"height": 1080, "label": "1080p", "ext": "mp4", "filesize": 1,
         "filesize_estimated": True, "compatible": True, "drm": False},
    ],
    "subtitles": ["en"],
    "auto_subtitles": [],
    "has_subtitles": True,
}


def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert "ffmpeg" in body and "ai_enabled" in body
    assert body["active_ai_jobs"] == 0
    assert isinstance(body["local_asr"], bool)


def test_limits_free_vs_pro(client, pro_token):
    free = client.get("/api/limits").json()
    assert free["is_pro"] is False
    assert free["max_height"] == config.FREE_MAX_HEIGHT

    paid = client.get("/api/limits", headers={"Authorization": f"Bearer {pro_token}"}).json()
    assert paid["is_pro"] is True
    assert paid["max_height"] == config.PRO_MAX_HEIGHT


def test_pro_token_accepted_via_either_header(client, pro_token):
    assert client.get("/api/limits", headers={"X-Pro-Token": pro_token}).json()["is_pro"]
    assert client.get(
        "/api/limits", headers={"Authorization": f"Bearer {pro_token}"}
    ).json()["is_pro"]


def test_sites_list(client):
    body = client.get("/api/sites").json()
    assert body["total"] == "1800+"
    assert any(s["slug"] == "bilibili" for s in body["popular"])


# --- SSRF at the API boundary -----------------------------------------------

@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "http://localhost/x",
    "http://127.0.0.1/",
    "http://169.254.169.254/latest/meta-data/",
    "http://192.168.0.1/",
    "http://[::1]/",
    "http://metadata.google.internal/",
])
def test_ssrf_rejected_on_probe_and_jobs(client, url):
    assert client.post("/api/probe", json={"url": url}).status_code == 400
    assert client.post("/api/jobs", json={"url": url}).status_code == 400


def test_batch_reports_rejected_urls_without_failing_whole_request(
    client, pro_token, monkeypatch
):
    monkeypatch.setattr(downloader, "probe", lambda url: FAKE_META)
    monkeypatch.setattr(jobs.store, "submit", lambda job: None)
    resp = client.post(
        "/api/batch",
        json={"urls": ["https://www.youtube.com/watch?v=ok", "http://127.0.0.1/evil"]},
        headers={"Authorization": f"Bearer {pro_token}"},
    )
    body = resp.json()
    assert len(body["jobs"]) == 1
    assert len(body["rejected"]) == 1


# --- probe -------------------------------------------------------------------

def test_probe_returns_shaped_metadata(client, monkeypatch):
    monkeypatch.setattr(main.downloader, "probe", lambda url: FAKE_META)
    body = client.post(
        "/api/probe", json={"url": "https://www.youtube.com/watch?v=x"}
    ).json()
    assert body["title"] == "测试视频"
    assert body["formats"][0]["compatible"] is True


def test_probe_failure_is_422_with_message(client, monkeypatch):
    def boom(url):
        raise downloader.ProbeError("该视频受 DRM 保护，无法下载")

    monkeypatch.setattr(main.downloader, "probe", boom)
    resp = client.post("/api/probe", json={"url": "https://www.youtube.com/watch?v=x"})
    assert resp.status_code == 422
    assert "DRM" in resp.json()["detail"]


@pytest.mark.parametrize("payload", [{}, {"url": ""}, {"url": "ab"}])
def test_probe_validates_input(client, payload):
    assert client.post("/api/probe", json=payload).status_code == 422


# --- job creation and gating -------------------------------------------------

def test_free_user_cannot_extract_mp3(client):
    resp = client.post(
        "/api/jobs",
        json={"url": "https://www.youtube.com/watch?v=x", "mode": "audio"},
    )
    assert resp.status_code == 402


def test_pro_user_can_extract_mp3(client, pro_token, monkeypatch):
    monkeypatch.setattr(jobs.store, "submit", lambda job: None)
    resp = client.post(
        "/api/jobs",
        json={"url": "https://www.youtube.com/watch?v=x", "mode": "audio"},
        headers={"Authorization": f"Bearer {pro_token}"},
    )
    assert resp.status_code == 202


def test_free_user_height_is_clamped_server_side(client, monkeypatch):
    """A client asking for 4K on the free tier must not get it."""
    monkeypatch.setattr(jobs.store, "submit", lambda job: None)
    resp = client.post(
        "/api/jobs", json={"url": "https://www.youtube.com/watch?v=x", "height": 2160}
    )
    job = jobs.store.get(resp.json()["job_id"])
    assert job.max_height == config.FREE_MAX_HEIGHT


def test_free_batch_over_cap_is_402(client):
    urls = [f"https://www.youtube.com/watch?v={i}" for i in range(5)]
    resp = client.post("/api/batch", json={"urls": urls})
    assert resp.status_code == 402
    assert "Pro" in resp.json()["detail"]


def test_batch_rejects_more_than_schema_max(client, pro_token):
    urls = [f"https://www.youtube.com/watch?v={i}" for i in range(200)]
    resp = client.post(
        "/api/batch", json={"urls": urls},
        headers={"Authorization": f"Bearer {pro_token}"},
    )
    assert resp.status_code == 422


def test_disk_full_returns_507(client, monkeypatch):
    monkeypatch.setattr(main.jobs, "disk_has_room", lambda: False)
    resp = client.post("/api/jobs", json={"url": "https://www.youtube.com/watch?v=x"})
    assert resp.status_code == 507


def test_rate_limit_returns_429_with_retry_after(client, monkeypatch):
    monkeypatch.setattr(main.pro, "limiter", pro.RateLimiter(2, 60))
    for _ in range(2):
        client.post("/api/probe", json={"url": "https://www.youtube.com/watch?v=x"})
    resp = client.post("/api/probe", json={"url": "https://www.youtube.com/watch?v=x"})
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers


# --- status and file ---------------------------------------------------------

def test_unknown_job_is_404(client):
    assert client.get("/api/jobs/deadbeef").status_code == 404
    assert client.get("/api/jobs/deadbeef/file").status_code == 404


def test_file_before_ready_is_409(client):
    job = jobs.store.create(url="https://www.youtube.com/watch?v=x")
    assert client.get(f"/api/jobs/{job.id}/file").status_code == 409


def test_file_gone_from_disk_is_410(client):
    job = jobs.store.create(url="https://www.youtube.com/watch?v=x")
    jobs.store.update(
        job.id, status="done", filepath=config.JOB_DIR / job.id / "media.mp4",
        filename="v.mp4", ext="mp4",
    )
    assert client.get(f"/api/jobs/{job.id}/file").status_code == 410


def test_file_outside_job_dir_is_403(client, tmp_path):
    """Defense in depth against a path that escapes the job directory."""
    outside = tmp_path / "escape.mp4"
    outside.write_bytes(b"x" * 16)
    job = jobs.store.create(url="https://www.youtube.com/watch?v=x")
    jobs.store.update(
        job.id, status="done", filepath=outside, filename="escape.mp4", ext="mp4",
    )
    assert client.get(f"/api/jobs/{job.id}/file").status_code == 403


def test_download_headers_carry_both_filename_forms(client):
    job = jobs.store.create(url="https://www.youtube.com/watch?v=x")
    job_dir = config.JOB_DIR / job.id
    job_dir.mkdir(parents=True, exist_ok=True)
    media = job_dir / "media.mp4"
    media.write_bytes(b"\x00" * 64)
    jobs.store.update(
        job.id, status="done", filepath=media, filename="测试视频.mp4", ext="mp4",
    )

    resp = client.get(f"/api/jobs/{job.id}/file")
    assert resp.status_code == 200
    cd = resp.headers["content-disposition"]
    assert "filename=" in cd and "filename*=UTF-8''" in cd
    assert resp.headers["content-type"] == "video/mp4"


def test_head_on_file_is_allowed(client):
    """Clients probe headers with HEAD before downloading; 405 would break that."""
    job = jobs.store.create(url="https://www.youtube.com/watch?v=x")
    job_dir = config.JOB_DIR / job.id
    job_dir.mkdir(parents=True, exist_ok=True)
    media = job_dir / "media.mp4"
    media.write_bytes(b"\x00" * 64)
    jobs.store.update(
        job.id, status="done", filepath=media, filename="v.mp4", ext="mp4",
    )
    assert client.head(f"/api/jobs/{job.id}/file").status_code == 200


# --- AI ----------------------------------------------------------------------

AI_URL = "https://www.youtube.com/watch?v=x"
AI_INFO = {
    "webpage_url": "https://www.youtube.com/watch?v=canonical",
    "title": "Metadata title",
    "duration": 60,
    "subtitles": {},
    "automatic_captions": {},
    "formats": [{}],
}


def _create_summary_job(**overrides):
    values = {
        "url": AI_URL,
        "title": "Summary title",
        "source_lang": "auto",
        "output_lang": "en",
        "source_info": AI_INFO,
    }
    values.update(overrides)
    return summary_jobs.store.create(**values)


def _complete_summary_job():
    job = _create_summary_job()
    job_dir = config.SUMMARY_JOB_DIR / job.id
    job_dir.mkdir(mode=0o700, parents=True)
    markdown = "# Summary title\n\nOverview\n"
    summary_path = job_dir / "summary.md"
    summary_path.write_text(markdown, encoding="utf-8")
    summary_jobs.store.update(
        job.id,
        status="done",
        percent=100,
        summary_path=summary_path,
    )
    return job, markdown


def test_ai_job_end_to_end(client, monkeypatch):
    source_url = "https://8.8.8.8/watch?v=smoke"
    source_info = dict(AI_INFO, webpage_url=source_url)
    transcript = summary_jobs.transcripts.Transcript(
        source="manual_caption",
        language="en",
        duration=60,
        segments=(
            summary_jobs.transcripts.TranscriptSegment(
                start_seconds=1,
                end_seconds=5,
                text="The smoke test follows the complete summary job path.",
            ),
        ),
    )
    expected = "# Complete summary\n\nThe API and worker complete the job.\n"

    def build_transcript(_url, _info, _preferred, _job_dir, on_phase):
        on_phase("reading_caption", 100)
        return transcript

    def summarize(_title, _url, _transcript, _output_lang, on_progress):
        on_progress(95)
        return expected

    monkeypatch.setattr(config, "AI_ENABLED", True)
    monkeypatch.setattr(main.downloader, "extract_info", lambda _url: source_info)
    monkeypatch.setattr(summary_jobs.transcripts, "build_transcript", build_transcript)
    monkeypatch.setattr(summary_jobs.ai, "summarize_transcript", summarize)

    redeem = client.post("/api/redeem", json={"code": config.REDEEM_CODES[0]})
    assert redeem.status_code == 200
    headers = {"Authorization": f"Bearer {redeem.json()['token']}"}

    created = client.post(
        "/api/ai/jobs",
        json={"url": source_url, "source_lang": "auto", "output_lang": "en"},
        headers=headers,
    )
    assert created.status_code == 202
    job_id = created.json()["job_id"]

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        status = client.get(f"/api/ai/jobs/{job_id}", headers=headers)
        assert status.status_code == 200
        if status.json()["status"] == "done":
            break
        assert status.json()["status"] != "error"
        time.sleep(0.01)
    else:
        pytest.fail("AI summary job did not finish")

    assert status.json()["ready"] is True
    assert status.json()["transcript_source"] == "manual_caption"
    assert status.headers["cache-control"] == "no-store"

    result = client.get(f"/api/ai/jobs/{job_id}/result", headers=headers)
    assert result.status_code == 200
    assert result.text == expected
    assert result.headers["content-type"] == "text/markdown; charset=utf-8"
    assert "content-disposition" not in result.headers
    assert result.headers["cache-control"] == "no-store"

    export = client.get(f"/api/ai/jobs/{job_id}/export", headers=headers)
    assert export.status_code == 200
    assert export.text == expected
    assert export.headers["content-type"] == "text/markdown; charset=utf-8"
    assert export.headers["content-disposition"].startswith("attachment;")
    assert export.headers["cache-control"] == "no-store"


def test_ai_job_creation_follows_admission_order(client, monkeypatch):
    events = []
    original_create = summary_jobs.store.create

    monkeypatch.setattr(
        main, "enforce_rate_limit", lambda _request: events.append("rate_limit"),
    )
    monkeypatch.setattr(
        main.ai, "available", lambda: events.append("ai_available") or True,
    )
    monkeypatch.setattr(
        main, "is_pro",
        lambda _authorization, _token: events.append("pro") or True,
    )
    monkeypatch.setattr(
        main, "checked_url", lambda url: events.append("checked_url") or url,
    )
    monkeypatch.setattr(
        summary_jobs, "disk_has_room", lambda: events.append("disk") or True,
    )
    monkeypatch.setattr(
        summary_jobs.store, "can_accept",
        lambda: events.append("queue") or True,
    )
    monkeypatch.setattr(
        main.downloader, "extract_info",
        lambda _url: events.append("extract_info") or dict(AI_INFO),
    )
    monkeypatch.setattr(
        main, "canonical_url",
        lambda raw, _fallback: events.append("canonical") or raw,
    )

    def create(**kwargs):
        events.append("create")
        return original_create(**kwargs)

    monkeypatch.setattr(summary_jobs.store, "create", create)
    monkeypatch.setattr(
        summary_jobs.store, "submit", lambda _job: events.append("submit"),
    )

    response = client.post(
        "/api/ai/jobs",
        json={
            "url": AI_URL,
            "title": "Client title",
            "source_lang": "auto",
            "output_lang": "en",
        },
        headers={"Authorization": "Bearer accepted"},
    )

    assert response.status_code == 202
    assert events == [
        "rate_limit",
        "ai_available",
        "pro",
        "checked_url",
        "disk",
        "queue",
        "extract_info",
        "canonical",
        "create",
        "submit",
    ]
    stored = summary_jobs.store.get(response.json()["job_id"])
    assert stored.url == AI_INFO["webpage_url"]
    assert stored.title == AI_INFO["title"]
    assert stored.source_info == AI_INFO
    assert stored.source_lang == "auto"
    assert stored.output_lang == "en"


def test_ai_job_requires_key_before_work(client, pro_token, monkeypatch):
    monkeypatch.setattr(main.ai, "available", lambda: False)
    monkeypatch.setattr(
        main, "checked_url",
        lambda _url: pytest.fail("URL validation must not run without AI"),
    )
    response = client.post(
        "/api/ai/jobs",
        json={"url": AI_URL},
        headers={"Authorization": f"Bearer {pro_token}"},
    )
    assert response.status_code == 503
    assert "未配置" in response.json()["detail"]


def test_ai_job_requires_pro_before_url_work(client, monkeypatch):
    monkeypatch.setattr(main.ai, "available", lambda: True)
    monkeypatch.setattr(
        main, "checked_url",
        lambda _url: pytest.fail("URL validation must not run without Pro"),
    )
    response = client.post("/api/ai/jobs", json={"url": AI_URL})
    assert response.status_code == 402


def test_ai_job_rejects_unsafe_url_before_probe(client, pro_token, monkeypatch):
    monkeypatch.setattr(main.ai, "available", lambda: True)
    monkeypatch.setattr(
        main.downloader, "extract_info",
        lambda _url: pytest.fail("unsafe URL must not be probed"),
    )
    response = client.post(
        "/api/ai/jobs",
        json={"url": "http://127.0.0.1/private"},
        headers={"Authorization": f"Bearer {pro_token}"},
    )
    assert response.status_code == 400


def test_ai_job_disk_full_returns_507_before_queue_or_probe(
    client, pro_token, monkeypatch,
):
    monkeypatch.setattr(main.ai, "available", lambda: True)
    monkeypatch.setattr(summary_jobs, "disk_has_room", lambda: False)
    monkeypatch.setattr(
        summary_jobs.store, "can_accept",
        lambda: pytest.fail("queue must not be checked when disk is full"),
    )
    monkeypatch.setattr(
        main.downloader, "extract_info",
        lambda _url: pytest.fail("probe must not run when disk is full"),
    )
    response = client.post(
        "/api/ai/jobs",
        json={"url": AI_URL},
        headers={"Authorization": f"Bearer {pro_token}"},
    )
    assert response.status_code == 507


def test_ai_job_disk_check_oserror_returns_safe_507_before_probe(
    client, pro_token, monkeypatch,
):
    probes = []
    monkeypatch.setattr(main.ai, "available", lambda: True)
    monkeypatch.setattr(
        summary_jobs.shutil,
        "disk_usage",
        lambda _path: (_ for _ in ()).throw(OSError("private storage detail")),
    )
    monkeypatch.setattr(
        main.downloader,
        "extract_info",
        lambda url: probes.append(url) or dict(AI_INFO),
    )
    monkeypatch.setattr(summary_jobs.store, "submit", lambda _job: None)

    response = client.post(
        "/api/ai/jobs",
        json={"url": AI_URL},
        headers={"Authorization": f"Bearer {pro_token}"},
    )

    assert response.status_code == 507
    assert response.json()["detail"] == "服务器磁盘空间不足，请稍后重试"
    assert "private" not in response.text
    assert probes == []


def test_ai_job_full_queue_returns_503_before_probe(client, pro_token, monkeypatch):
    monkeypatch.setattr(main.ai, "available", lambda: True)
    monkeypatch.setattr(summary_jobs, "disk_has_room", lambda: True)
    monkeypatch.setattr(summary_jobs.store, "can_accept", lambda: False)
    monkeypatch.setattr(
        main.downloader, "extract_info",
        lambda _url: pytest.fail("probe must not run when queue is full"),
    )
    response = client.post(
        "/api/ai/jobs",
        json={"url": AI_URL},
        headers={"Authorization": f"Bearer {pro_token}"},
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "AI 总结队列已满，请稍后重试"


def test_ai_job_probe_failure_is_422(client, pro_token, monkeypatch):
    monkeypatch.setattr(main.ai, "available", lambda: True)
    monkeypatch.setattr(summary_jobs, "disk_has_room", lambda: True)
    monkeypatch.setattr(summary_jobs.store, "can_accept", lambda: True)

    def fail(_url):
        raise downloader.ProbeError("视频不可用")

    monkeypatch.setattr(main.downloader, "extract_info", fail)
    response = client.post(
        "/api/ai/jobs",
        json={"url": AI_URL},
        headers={"Authorization": f"Bearer {pro_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "视频不可用"


def test_ai_job_atomic_queue_race_uses_same_safe_503(
    client, pro_token, monkeypatch,
):
    monkeypatch.setattr(main.ai, "available", lambda: True)
    monkeypatch.setattr(summary_jobs, "disk_has_room", lambda: True)
    monkeypatch.setattr(summary_jobs.store, "can_accept", lambda: True)
    monkeypatch.setattr(main.downloader, "extract_info", lambda _url: dict(AI_INFO))
    monkeypatch.setattr(
        summary_jobs.store,
        "create",
        lambda **_kwargs: (_ for _ in ()).throw(
            summary_jobs.SummaryQueueFull("AI 总结队列已满，请稍后重试")
        ),
    )
    monkeypatch.setattr(
        summary_jobs.store, "submit",
        lambda _job: pytest.fail("rejected job must not be submitted"),
    )
    response = client.post(
        "/api/ai/jobs",
        json={"url": AI_URL},
        headers={"Authorization": f"Bearer {pro_token}"},
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "AI 总结队列已满，请稍后重试"


def test_ai_job_rejects_unsafe_metadata_canonical_url(client, pro_token, monkeypatch):
    """Extractor metadata is untrusted; the worker re-fetches the stored URL."""
    monkeypatch.setattr(main.ai, "available", lambda: True)
    monkeypatch.setattr(
        main.downloader, "extract_info",
        lambda _url: dict(AI_INFO, webpage_url="http://169.254.169.254/latest/meta-data/"),
    )
    monkeypatch.setattr(summary_jobs.store, "submit", lambda _job: None)

    response = client.post(
        "/api/ai/jobs",
        json={"url": AI_URL},
        headers={"Authorization": f"Bearer {pro_token}"},
    )

    assert response.status_code == 202
    assert summary_jobs.store.get(response.json()["job_id"]).url == AI_URL


@pytest.mark.parametrize("webpage_url", ["http://[::1", {"url": AI_URL}])
def test_ai_job_rejects_malformed_or_non_string_canonical_url(
    client, pro_token, monkeypatch, webpage_url,
):
    monkeypatch.setattr(main.ai, "available", lambda: True)
    monkeypatch.setattr(
        main.downloader, "extract_info",
        lambda _url: dict(AI_INFO, webpage_url=webpage_url),
    )
    monkeypatch.setattr(summary_jobs.store, "submit", lambda _job: None)

    response = client.post(
        "/api/ai/jobs",
        json={"url": AI_URL},
        headers={"Authorization": f"Bearer {pro_token}"},
    )

    assert response.status_code == 202
    assert summary_jobs.store.get(response.json()["job_id"]).url == AI_URL


def test_ai_job_prefers_safe_metadata_canonical_url(client, pro_token, monkeypatch):
    monkeypatch.setattr(main.ai, "available", lambda: True)
    monkeypatch.setattr(main.downloader, "extract_info", lambda _url: dict(AI_INFO))
    monkeypatch.setattr(summary_jobs.store, "submit", lambda _job: None)

    response = client.post(
        "/api/ai/jobs",
        json={"url": AI_URL},
        headers={"Authorization": f"Bearer {pro_token}"},
    )

    assert response.status_code == 202
    stored = summary_jobs.store.get(response.json()["job_id"])
    assert stored.url == AI_INFO["webpage_url"]


def test_ai_job_storage_failure_is_507(client, pro_token, monkeypatch):
    monkeypatch.setattr(main.ai, "available", lambda: True)
    monkeypatch.setattr(main.downloader, "extract_info", lambda _url: dict(AI_INFO))
    monkeypatch.setattr(
        summary_jobs.store, "create",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("/private/summary-jobs")),
    )
    monkeypatch.setattr(
        summary_jobs.store, "submit",
        lambda _job: pytest.fail("failed job must not be submitted"),
    )

    response = client.post(
        "/api/ai/jobs",
        json={"url": AI_URL},
        headers={"Authorization": f"Bearer {pro_token}"},
    )

    assert response.status_code == 507
    assert response.json()["detail"] == "服务器磁盘空间不足，请稍后重试"
    assert "private" not in response.text


def test_ai_job_submission_failure_is_safe_503_and_releases_capacity(
    client, pro_token, monkeypatch,
):
    monkeypatch.setattr(main.ai, "available", lambda: True)
    monkeypatch.setattr(main.downloader, "extract_info", lambda _url: dict(AI_INFO))

    def fail_submit(*_args, **_kwargs):
        raise RuntimeError("broken executor secret")

    monkeypatch.setattr(summary_jobs.store._pool, "submit", fail_submit)

    response = client.post(
        "/api/ai/jobs",
        json={"url": AI_URL},
        headers={"Authorization": f"Bearer {pro_token}"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "AI 总结服务暂时不可用，请稍后重试"
    assert "secret" not in response.text
    assert summary_jobs.store.count_active() == 0
    assert summary_jobs.store._jobs == {}
    assert list(config.SUMMARY_JOB_DIR.iterdir()) == []


def test_ai_job_creation_offloads_all_blocking_summary_work(
    client, pro_token, monkeypatch,
):
    loop_threads = []
    worker_threads = {}
    original_create = summary_jobs.store.create

    monkeypatch.setattr(main.ai, "available", lambda: True)
    monkeypatch.setattr(
        main, "require_summary_pro",
        lambda _authorization, _token: loop_threads.append(
            threading.current_thread().name
        ),
    )

    def record(name, result):
        worker_threads.setdefault(name, []).append(threading.current_thread().name)
        return result

    monkeypatch.setattr(
        main, "checked_url",
        lambda url: record("checked_url", url),
    )
    monkeypatch.setattr(
        summary_jobs, "disk_has_room",
        lambda: record("disk_has_room", True),
    )
    monkeypatch.setattr(
        summary_jobs.store, "can_accept",
        lambda: record("can_accept", True),
    )
    monkeypatch.setattr(
        main.downloader, "extract_info",
        lambda _url: record("extract_info", dict(AI_INFO)),
    )
    monkeypatch.setattr(
        main, "canonical_url",
        lambda raw, _fallback: record("canonical_url", raw),
    )

    def create(**kwargs):
        record("create", None)
        return original_create(**kwargs)

    monkeypatch.setattr(summary_jobs.store, "create", create)
    monkeypatch.setattr(
        summary_jobs.store, "submit",
        lambda _job: record("submit", None),
    )

    response = client.post(
        "/api/ai/jobs",
        json={"url": AI_URL},
        headers={"Authorization": f"Bearer {pro_token}"},
    )

    assert response.status_code == 202
    assert loop_threads
    assert set(worker_threads) == {
        "checked_url",
        "disk_has_room",
        "can_accept",
        "extract_info",
        "canonical_url",
        "create",
        "submit",
    }
    assert len(worker_threads["extract_info"]) == 1
    assert all(
        thread != loop_threads[0]
        for threads in worker_threads.values()
        for thread in threads
    )


def test_health_offloads_summary_store_lock(client, monkeypatch):
    loop_threads = []
    store_threads = []
    monkeypatch.setattr(
        main, "local_asr_available",
        lambda: loop_threads.append(threading.current_thread().name) or True,
    )
    monkeypatch.setattr(
        summary_jobs.store,
        "count_active",
        lambda: store_threads.append(threading.current_thread().name) or 0,
    )

    response = client.get("/api/health")

    assert response.status_code == 200
    assert loop_threads and store_threads
    assert store_threads[0] != loop_threads[0]


@pytest.mark.parametrize("path", [
    "/api/ai/jobs/deadbeef",
    "/api/ai/jobs/deadbeef/result",
    "/api/ai/jobs/deadbeef/export",
])
def test_all_ai_job_reads_require_pro(client, path):
    response = client.get(path)
    assert response.status_code == 402
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("path", [
    "/api/ai/jobs/deadbeef",
    "/api/ai/jobs/deadbeef/result",
    "/api/ai/jobs/deadbeef/export",
])
def test_unknown_ai_job_reads_are_safe_404(client, pro_token, path):
    response = client.get(
        path, headers={"Authorization": f"Bearer {pro_token}"},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "任务不存在或已过期"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("path", [
    "/api/ai/jobs/deadbeef",
    "/api/ai/jobs/deadbeef/result",
    "/api/ai/jobs/deadbeef/export",
])
def test_unexpected_ai_job_read_errors_are_safe_and_not_cached(
    client, pro_token, monkeypatch, path,
):
    monkeypatch.setattr(
        summary_jobs.store,
        "get",
        lambda _job_id: (_ for _ in ()).throw(OSError("/private/job-data")),
    )

    response = client.get(
        path, headers={"Authorization": f"Bearer {pro_token}"},
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "服务器暂时不可用，请稍后重试"
    assert response.headers["cache-control"] == "no-store"
    assert "private" not in response.text


def test_ai_job_status_returns_public_snapshot(client, pro_token):
    job = _create_summary_job()
    response = client.get(
        f"/api/ai/jobs/{job.id}",
        headers={"Authorization": f"Bearer {pro_token}"},
    )
    assert response.status_code == 200
    assert response.json() == summary_jobs.store.get(job.id).public()
    assert "source_info" not in response.json()
    assert "url" not in response.json()


@pytest.mark.parametrize("suffix", ["", "/result"])
def test_ai_job_reads_are_never_cached(client, pro_token, suffix):
    job, _markdown = _complete_summary_job()
    response = client.get(
        f"/api/ai/jobs/{job.id}{suffix}",
        headers={"Authorization": f"Bearer {pro_token}"},
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("suffix", ["", "/result"])
def test_ai_job_reads_offload_blocking_store_work(client, pro_token, monkeypatch, suffix):
    loop_threads = []
    store_threads = []
    original_get = summary_jobs.store.get
    original_read = summary_jobs.store.read_summary
    job, _markdown = _complete_summary_job()

    monkeypatch.setattr(
        main, "require_summary_pro",
        lambda _authorization, _token: loop_threads.append(
            threading.current_thread().name
        ),
    )
    monkeypatch.setattr(
        summary_jobs.store, "get",
        lambda job_id: store_threads.append(threading.current_thread().name)
        or original_get(job_id),
    )
    monkeypatch.setattr(
        summary_jobs.store, "read_summary",
        lambda job_id: store_threads.append(threading.current_thread().name)
        or original_read(job_id),
    )

    response = client.get(
        f"/api/ai/jobs/{job.id}{suffix}",
        headers={"Authorization": f"Bearer {pro_token}"},
    )

    assert response.status_code == 200
    assert store_threads and loop_threads
    assert all(name != loop_threads[0] for name in store_threads)


@pytest.mark.parametrize("suffix", ["result", "export"])
def test_failed_ai_job_reads_report_the_safe_failure(client, pro_token, suffix):
    job = _create_summary_job()
    summary_jobs.store.update(job.id, status="error", error="语音识别失败")
    response = client.get(
        f"/api/ai/jobs/{job.id}/{suffix}",
        headers={"Authorization": f"Bearer {pro_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "语音识别失败"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("suffix", ["result", "export"])
def test_failed_ai_job_without_message_uses_fixed_error(client, pro_token, suffix):
    job = _create_summary_job()
    summary_jobs.store.update(job.id, status="error")
    response = client.get(
        f"/api/ai/jobs/{job.id}/{suffix}",
        headers={"Authorization": f"Bearer {pro_token}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "AI 总结失败，请稍后重试"
    assert response.headers["cache-control"] == "no-store"


def test_ai_job_export_buffers_bytes_before_artifact_sweep(
    client, pro_token, monkeypatch,
):
    job, markdown = _complete_summary_job()
    summary_path = summary_jobs.store.get(job.id).summary_path
    original_read_summary = summary_jobs.store.read_summary
    calls = []

    def read_then_sweep(job_id):
        content = original_read_summary(job_id)
        calls.append(job_id)
        summary_jobs.store.update(job_id, created_at=0)
        assert summary_jobs.store.sweep(ttl=0) == 1
        assert not summary_path.exists()
        return content

    monkeypatch.setattr(summary_jobs.store, "read_summary", read_then_sweep)

    response = client.get(
        f"/api/ai/jobs/{job.id}/export",
        headers={"Authorization": f"Bearer {pro_token}"},
    )

    assert response.status_code == 200
    assert response.content == markdown.encode()
    assert calls == [job.id]


def test_ai_job_export_requires_the_canonical_artifact_path(client, pro_token):
    job, _markdown = _complete_summary_job()
    decoy = config.SUMMARY_JOB_DIR / job.id / "other.md"
    decoy.write_text("decoy", encoding="utf-8")
    summary_jobs.store.update(job.id, summary_path=decoy)
    response = client.get(
        f"/api/ai/jobs/{job.id}/export",
        headers={"Authorization": f"Bearer {pro_token}"},
    )
    assert response.status_code == 403
    assert "other.md" not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_ai_job_export_rejects_canonical_symlink(
    client, pro_token, tmp_path,
):
    job, _markdown = _complete_summary_job()
    summary_path = summary_jobs.store.get(job.id).summary_path
    outside = tmp_path / "outside.md"
    outside.write_text("private", encoding="utf-8")
    summary_path.unlink()
    summary_path.symlink_to(outside)

    response = client.get(
        f"/api/ai/jobs/{job.id}/export",
        headers={"Authorization": f"Bearer {pro_token}"},
    )

    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store"
    assert "private" not in response.text


@pytest.mark.parametrize("suffix", ["result", "export"])
def test_ai_job_artifacts_return_409_before_completion(client, pro_token, suffix):
    job = _create_summary_job()
    response = client.get(
        f"/api/ai/jobs/{job.id}/{suffix}",
        headers={"Authorization": f"Bearer {pro_token}"},
    )
    assert response.status_code == 409
    assert response.headers["cache-control"] == "no-store"


def test_ai_job_result_returns_completed_markdown(client, pro_token):
    job, markdown = _complete_summary_job()
    response = client.get(
        f"/api/ai/jobs/{job.id}/result",
        headers={"Authorization": f"Bearer {pro_token}"},
    )
    assert response.status_code == 200
    assert response.text == markdown
    assert response.headers["content-type"] == "text/markdown; charset=utf-8"
    assert "content-disposition" not in response.headers


def test_ai_job_missing_completed_result_is_safe_404(client, pro_token):
    job, _markdown = _complete_summary_job()
    summary_jobs.store.get(job.id).summary_path.unlink()
    response = client.get(
        f"/api/ai/jobs/{job.id}/result",
        headers={"Authorization": f"Bearer {pro_token}"},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "任务不存在或已过期"
    assert response.headers["cache-control"] == "no-store"


def test_ai_job_export_has_safe_utf8_attachment_headers(client, pro_token):
    job, markdown = _complete_summary_job()
    summary_jobs.store.update(job.id, title="测试总结")
    response = client.get(
        f"/api/ai/jobs/{job.id}/export",
        headers={"Authorization": f"Bearer {pro_token}"},
    )
    assert response.status_code == 200
    assert response.text == markdown
    assert response.headers["content-type"] == "text/markdown; charset=utf-8"
    assert response.headers["cache-control"] == "no-store"
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert "filename=" in disposition
    assert "filename*=UTF-8''" in disposition
    assert "%E6%B5%8B%E8%AF%95%E6%80%BB%E7%BB%93" in disposition


def test_ai_job_export_rejects_path_outside_summary_dir(
    client, pro_token, tmp_path,
):
    job, _markdown = _complete_summary_job()
    outside = tmp_path / "outside.md"
    outside.write_text("private", encoding="utf-8")
    summary_jobs.store.update(job.id, summary_path=outside)
    response = client.get(
        f"/api/ai/jobs/{job.id}/export",
        headers={"Authorization": f"Bearer {pro_token}"},
    )
    assert response.status_code == 403
    assert "outside" not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_ai_job_missing_completed_export_is_safe_404(client, pro_token):
    job, _markdown = _complete_summary_job()
    summary_jobs.store.get(job.id).summary_path.unlink()
    response = client.get(
        f"/api/ai/jobs/{job.id}/export",
        headers={"Authorization": f"Bearer {pro_token}"},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "任务不存在或已过期"
    assert response.headers["cache-control"] == "no-store"


def test_expired_ai_job_is_safe_404(client, pro_token):
    job = _create_summary_job()
    summary_jobs.store.update(job.id, status="done", created_at=0)
    assert summary_jobs.store.sweep(ttl=0) == 1
    response = client.get(
        f"/api/ai/jobs/{job.id}",
        headers={"Authorization": f"Bearer {pro_token}"},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "任务不存在或已过期"
    assert response.headers["cache-control"] == "no-store"


def test_old_summary_route_is_removed(client, pro_token):
    response = client.post(
        "/api/ai/summary",
        json={"url": AI_URL},
        headers={"Authorization": f"Bearer {pro_token}"},
    )
    assert response.status_code == 404


def test_ai_translate_without_key_is_503(client, pro_token, monkeypatch):
    monkeypatch.setattr(main.ai, "available", lambda: False)
    response = client.post(
        "/api/ai/translate",
        json={"url": AI_URL},
        headers={"Authorization": f"Bearer {pro_token}"},
    )
    assert response.status_code == 503
    assert "未配置" in response.json()["detail"]


def test_ai_translate_requires_pro_when_configured(client, monkeypatch):
    monkeypatch.setattr(main.ai, "available", lambda: True)
    response = client.post("/api/ai/translate", json={"url": AI_URL})
    assert response.status_code == 402


def test_ai_translate_response_is_unchanged(client, pro_token, monkeypatch):
    expected = {
        "srt": "translated",
        "source_lang": "en",
        "target_lang": "English",
        "model": "test-model",
    }
    monkeypatch.setattr(main.ai, "available", lambda: True)
    monkeypatch.setattr(
        main.ai, "translate_subtitles",
        lambda _url, _target: expected,
    )
    response = client.post(
        "/api/ai/translate",
        json={"url": AI_URL, "target_lang": "English"},
        headers={"Authorization": f"Bearer {pro_token}"},
    )
    assert response.status_code == 200
    assert response.json() == expected


def _mind_map_payload():
    return {
        "root": {
            "id": "n1",
            "label": "Root topic",
            "timestamp": None,
            "children": [
                {"id": "n2", "label": "Section", "timestamp": 5.0, "children": []},
            ],
        },
        "node_count": 2,
    }


def _complete_mind_map_job():
    job, _markdown = _complete_summary_job()
    payload = _mind_map_payload()
    path = config.SUMMARY_JOB_DIR / job.id / "mind_map.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    summary_jobs.store.update(
        job.id,
        mind_map_status="done",
        mind_map_percent=100,
        mind_map_path=path,
    )
    return job, payload


def test_mind_map_creation_requires_pro(client, monkeypatch):
    monkeypatch.setattr(main.ai, "available", lambda: True)
    job, _markdown = _complete_summary_job()

    response = client.post(f"/api/ai/jobs/{job.id}/mind-map")

    assert response.status_code == 402


def test_mind_map_creation_requires_an_ai_key(client, pro_token, monkeypatch):
    monkeypatch.setattr(main.ai, "available", lambda: False)
    job, _markdown = _complete_summary_job()

    response = client.post(
        f"/api/ai/jobs/{job.id}/mind-map",
        headers={"Authorization": f"Bearer {pro_token}"},
    )

    assert response.status_code == 503
    assert "未配置" in response.json()["detail"]


def test_mind_map_creation_returns_the_claimed_job(client, pro_token, monkeypatch):
    monkeypatch.setattr(main.ai, "available", lambda: True)
    monkeypatch.setattr(
        summary_jobs.store,
        "submit_mind_map",
        lambda job_id: summary_jobs.store.get(job_id),
    )
    job, _markdown = _complete_summary_job()

    response = client.post(
        f"/api/ai/jobs/{job.id}/mind-map",
        headers={"Authorization": f"Bearer {pro_token}"},
    )

    assert response.status_code == 202
    assert response.json()["job_id"] == job.id


def test_mind_map_creation_rejects_an_unfinished_summary(client, pro_token, monkeypatch):
    monkeypatch.setattr(main.ai, "available", lambda: True)
    job = _create_summary_job()

    response = client.post(
        f"/api/ai/jobs/{job.id}/mind-map",
        headers={"Authorization": f"Bearer {pro_token}"},
    )

    assert response.status_code == 409
    assert "AI 总结" in response.json()["detail"]


def test_mind_map_creation_reports_an_expired_summary_as_404(
    client, pro_token, monkeypatch,
):
    monkeypatch.setattr(main.ai, "available", lambda: True)

    response = client.post(
        "/api/ai/jobs/missing/mind-map",
        headers={"Authorization": f"Bearer {pro_token}"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "任务不存在或已过期"


def test_mind_map_creation_reports_a_running_map_as_409(client, pro_token, monkeypatch):
    monkeypatch.setattr(main.ai, "available", lambda: True)
    job, _markdown = _complete_summary_job()
    summary_jobs.store.update(job.id, mind_map_status="generating")

    response = client.post(
        f"/api/ai/jobs/{job.id}/mind-map",
        headers={"Authorization": f"Bearer {pro_token}"},
    )

    assert response.status_code == 409
    assert "正在生成" in response.json()["detail"]


def test_mind_map_result_requires_pro(client):
    job, _payload = _complete_mind_map_job()

    assert client.get(f"/api/ai/jobs/{job.id}/mind-map").status_code == 402


def test_mind_map_result_returns_validated_json(client, pro_token):
    job, payload = _complete_mind_map_job()

    response = client.get(
        f"/api/ai/jobs/{job.id}/mind-map",
        headers={"Authorization": f"Bearer {pro_token}"},
    )

    assert response.status_code == 200
    assert response.json() == payload
    assert response.headers["content-type"] == "application/json"
    assert response.headers["cache-control"] == "no-store"


def test_mind_map_result_is_409_before_generation(client, pro_token):
    job, _markdown = _complete_summary_job()

    response = client.get(
        f"/api/ai/jobs/{job.id}/mind-map",
        headers={"Authorization": f"Bearer {pro_token}"},
    )

    assert response.status_code == 409
    assert response.headers["cache-control"] == "no-store"


def test_mind_map_result_surfaces_generation_errors(client, pro_token):
    job, _markdown = _complete_summary_job()
    summary_jobs.store.update(
        job.id, mind_map_status="error", mind_map_error="模型返回无效",
    )

    response = client.get(
        f"/api/ai/jobs/{job.id}/mind-map",
        headers={"Authorization": f"Bearer {pro_token}"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "模型返回无效"


def test_mind_map_result_rejects_a_path_mismatch(client, pro_token):
    job, _payload = _complete_mind_map_job()
    summary_jobs.store.update(
        job.id, mind_map_path=config.SUMMARY_JOB_DIR / "elsewhere" / "mind_map.json",
    )

    response = client.get(
        f"/api/ai/jobs/{job.id}/mind-map",
        headers={"Authorization": f"Bearer {pro_token}"},
    )

    assert response.status_code == 403


def test_mind_map_result_reports_corrupted_json(client, pro_token):
    job, _payload = _complete_mind_map_job()
    summary_jobs.store.get(job.id).mind_map_path.write_text("{not json", encoding="utf-8")

    response = client.get(
        f"/api/ai/jobs/{job.id}/mind-map",
        headers={"Authorization": f"Bearer {pro_token}"},
    )

    assert response.status_code == 422
    assert "损坏" in response.json()["detail"]


@pytest.mark.parametrize("fmt,content_type,marker", [
    ("json", "application/json; charset=utf-8", '"label": "Root topic"'),
    ("markdown", "text/markdown; charset=utf-8", "## Root topic"),
    ("opml", "text/x-opml; charset=utf-8", "<opml"),
])
def test_mind_map_export_serializes_each_format(
    client, pro_token, fmt, content_type, marker,
):
    job, _payload = _complete_mind_map_job()

    response = client.get(
        f"/api/ai/jobs/{job.id}/mind-map/export?format={fmt}",
        headers={"Authorization": f"Bearer {pro_token}"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == content_type
    assert response.headers["cache-control"] == "no-store"
    assert "attachment" in response.headers["content-disposition"]
    assert marker in response.text


def test_mind_map_export_defaults_to_json(client, pro_token):
    job, payload = _complete_mind_map_job()

    response = client.get(
        f"/api/ai/jobs/{job.id}/mind-map/export",
        headers={"Authorization": f"Bearer {pro_token}"},
    )

    assert response.status_code == 200
    assert json.loads(response.text) == payload


def test_mind_map_export_rejects_an_unknown_format(client, pro_token):
    job, _payload = _complete_mind_map_job()

    response = client.get(
        f"/api/ai/jobs/{job.id}/mind-map/export?format=svg",
        headers={"Authorization": f"Bearer {pro_token}"},
    )

    assert response.status_code == 422


def test_mind_map_export_names_the_file_from_the_title(client, pro_token):
    job, _payload = _complete_mind_map_job()
    summary_jobs.store.update(job.id, title="测试视频")

    response = client.get(
        f"/api/ai/jobs/{job.id}/mind-map/export?format=opml",
        headers={"Authorization": f"Bearer {pro_token}"},
    )

    disposition = response.headers["content-disposition"]
    assert "-mindmap.opml" in disposition
    assert "filename*=UTF-8''" in disposition


def test_mind_map_export_requires_pro(client):
    job, _payload = _complete_mind_map_job()

    response = client.get(f"/api/ai/jobs/{job.id}/mind-map/export")

    assert response.status_code == 402


def test_play_serves_the_media_inline_for_seeking(client):
    job = jobs.store.create(url=AI_URL, mode="video")
    job_dir = config.JOB_DIR / job.id
    job_dir.mkdir(parents=True)
    media = job_dir / "media.mp4"
    media.write_bytes(b"\x00" * 64)
    jobs.store.update(
        job.id, status="done", filepath=media, filename="测试视频.mp4", ext="mp4",
    )

    response = client.get(f"/api/jobs/{job.id}/play")

    assert response.status_code == 200
    assert response.headers["content-type"] == "video/mp4"
    assert response.headers["content-disposition"].startswith("inline;")
    assert response.headers["accept-ranges"] == "bytes"


def test_play_supports_range_requests(client):
    job = jobs.store.create(url=AI_URL, mode="video")
    job_dir = config.JOB_DIR / job.id
    job_dir.mkdir(parents=True)
    media = job_dir / "media.mp4"
    media.write_bytes(bytes(range(64)))
    jobs.store.update(job.id, status="done", filepath=media, filename="v.mp4", ext="mp4")

    response = client.get(
        f"/api/jobs/{job.id}/play", headers={"Range": "bytes=8-15"},
    )

    assert response.status_code == 206
    assert response.content == bytes(range(8, 16))


def test_play_is_409_before_the_download_finishes(client):
    job = jobs.store.create(url=AI_URL, mode="video")

    assert client.get(f"/api/jobs/{job.id}/play").status_code == 409


def test_play_is_404_for_an_unknown_job(client):
    assert client.get("/api/jobs/missing/play").status_code == 404


def test_play_is_410_once_the_file_is_swept(client):
    job = jobs.store.create(url=AI_URL, mode="video")
    job_dir = config.JOB_DIR / job.id
    job_dir.mkdir(parents=True)
    media = job_dir / "media.mp4"
    media.write_bytes(b"\x00" * 8)
    jobs.store.update(job.id, status="done", filepath=media, filename="v.mp4", ext="mp4")
    media.unlink()

    assert client.get(f"/api/jobs/{job.id}/play").status_code == 410


def test_lifespan_sweeps_and_shuts_down_both_stores(monkeypatch):
    calls = []
    monkeypatch.setattr(config, "SWEEP_INTERVAL_SECONDS", 0.001)
    # Stub stores rather than patching the real ones: the isolation fixture must
    # still be able to shut the real thread pools down during teardown.
    monkeypatch.setattr(jobs, "store", SimpleNamespace(
        sweep=lambda: calls.append("jobs.sweep"),
        shutdown=lambda: calls.append("jobs.shutdown"),
    ))
    monkeypatch.setattr(summary_jobs, "store", SimpleNamespace(
        sweep=lambda: calls.append("summary.sweep"),
        shutdown=lambda: calls.append("summary.shutdown"),
    ))

    async def exercise():
        async with main.lifespan(main.app):
            for _ in range(100):
                if "jobs.sweep" in calls and "summary.sweep" in calls:
                    break
                await asyncio.sleep(0.001)

    asyncio.run(exercise())

    assert "jobs.sweep" in calls
    assert "summary.sweep" in calls
    assert "jobs.shutdown" in calls
    assert "summary.shutdown" in calls
