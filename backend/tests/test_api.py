"""API tests. Network-touching routes are stubbed so the suite stays fast."""
from __future__ import annotations

import sys
from pathlib import Path

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


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(security.config, "ALLOW_RESOLVED_NETS", "198.18.0.0/15")
    monkeypatch.setattr(config, "JOB_DIR", tmp_path / "jobs")
    monkeypatch.setattr(jobs, "store", jobs.JobStore())
    monkeypatch.setattr(main.jobs, "store", jobs.store)
    # Generous limits so unrelated tests don't trip the limiter.
    monkeypatch.setattr(pro, "limiter", pro.RateLimiter(1000, 60))
    monkeypatch.setattr(main.pro, "limiter", pro.limiter)


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

def test_ai_without_key_is_503(client, pro_token, monkeypatch):
    """No key must degrade cleanly, not error out."""
    monkeypatch.setattr(main.ai, "available", lambda: False)
    for path in ("/api/ai/summary", "/api/ai/translate"):
        resp = client.post(
            path, json={"url": "https://www.youtube.com/watch?v=x"},
            headers={"Authorization": f"Bearer {pro_token}"},
        )
        assert resp.status_code == 503
        assert "未配置" in resp.json()["detail"]


def test_ai_requires_pro_when_configured(client, monkeypatch):
    monkeypatch.setattr(main.ai, "available", lambda: True)
    resp = client.post("/api/ai/summary", json={"url": "https://www.youtube.com/watch?v=x"})
    assert resp.status_code == 402


def test_ai_summary_happy_path(client, pro_token, monkeypatch):
    monkeypatch.setattr(main.ai, "available", lambda: True)
    monkeypatch.setattr(
        main.ai, "summarize",
        lambda url, title: {"summary": "一句话总结", "source_lang": "en", "model": "m"},
    )
    resp = client.post(
        "/api/ai/summary", json={"url": "https://www.youtube.com/watch?v=x"},
        headers={"Authorization": f"Bearer {pro_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["summary"] == "一句话总结"


def test_subtitle_markup_stripping():
    """Auto-captions repeat lines and carry tags; both must go."""
    raw = (
        "WEBVTT\n\n1\n00:00:01,000 --> 00:00:02,000\n"
        "<c>hello</c>\n\n2\n00:00:02,000 --> 00:00:03,000\nhello\n\n"
        "3\n00:00:03,000 --> 00:00:04,000\nworld\n"
    )
    out = ai.strip_subtitle_markup(raw)
    assert out.splitlines() == ["hello", "world"]
    assert "-->" not in out and "<c>" not in out
