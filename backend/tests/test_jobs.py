"""Job store, sweeper, and format-selector tests. No network."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
import downloader  # noqa: E402
import jobs  # noqa: E402


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "JOB_DIR", tmp_path / "jobs")
    return jobs.JobStore()


def test_created_job_starts_queued(store):
    job = store.create(url="https://example.com/v")
    assert job.status == "queued"
    assert job.percent == 0.0
    assert store.get(job.id) is job


def test_public_view_hides_filesystem_paths(store):
    job = store.create(url="https://example.com/v")
    store.update(job.id, filepath=Path("/secret/path/media.mp4"), status="done")
    payload = store.get(job.id).public()
    assert "filepath" not in payload
    assert "/secret" not in str(payload)
    assert payload["ready"] is True


def test_unknown_job_is_none(store):
    assert store.get("nope") is None


def test_update_on_missing_job_is_noop(store):
    store.update("ghost", status="done")  # must not raise


def test_progress_percent_capped_below_100(store):
    """A merge still has to run, so 100% before the file exists reads as a hang."""
    job = store.create(url="https://example.com/v")
    store._apply_progress(job.id, {
        "phase": "downloading", "downloaded_bytes": 1000, "total_bytes": 1000,
    })
    assert store.get(job.id).percent == 99.0


def test_progress_handles_unknown_total(store):
    job = store.create(url="https://example.com/v")
    store._apply_progress(job.id, {
        "phase": "downloading", "downloaded_bytes": 500, "total_bytes": None,
    })
    assert store.get(job.id).percent == 0.0
    assert store.get(job.id).downloaded_bytes == 500


def test_merging_phase_clears_speed(store):
    job = store.create(url="https://example.com/v")
    store.update(job.id, speed=1000.0, eta=5)
    store._apply_progress(job.id, {"phase": "merging"})
    got = store.get(job.id)
    assert got.status == "merging"
    assert got.speed is None and got.eta is None


def test_active_count_tracks_in_flight(store):
    a = store.create(url="https://example.com/a")
    b = store.create(url="https://example.com/b")
    store.update(a.id, status="downloading")
    store.update(b.id, status="done")
    assert store.count_active() == 1


# --- sweeper -----------------------------------------------------------------

def test_sweeper_removes_expired_job_and_files(store):
    job = store.create(url="https://example.com/v")
    job_dir = config.JOB_DIR / job.id
    job_dir.mkdir(parents=True)
    (job_dir / "media.mp4").write_bytes(b"x" * 32)
    store.update(job.id, status="done", created_at=time.time() - 99_999)

    assert store.sweep() >= 1
    assert store.get(job.id) is None
    assert not job_dir.exists()


def test_sweeper_spares_fresh_jobs(store):
    job = store.create(url="https://example.com/v")
    store.update(job.id, status="done")
    store.sweep()
    assert store.get(job.id) is not None


def test_sweeper_spares_in_flight_jobs(store):
    """An old-but-still-downloading job must not have its files pulled."""
    job = store.create(url="https://example.com/v")
    store.update(job.id, status="downloading", created_at=time.time() - 99_999)
    store.sweep()
    assert store.get(job.id) is not None


def test_sweeper_reclaims_orphan_dirs(store):
    """Directories from a previous process still need cleaning up."""
    orphan = config.JOB_DIR / "orphan_from_last_run"
    orphan.mkdir(parents=True)
    (orphan / "media.mp4").write_bytes(b"x")
    import os
    old = time.time() - 99_999
    os.utime(orphan, (old, old))

    store.sweep()
    assert not orphan.exists()


def test_job_dir_is_owner_only(store):
    assert (config.JOB_DIR.stat().st_mode & 0o777) == 0o700


# --- format selector ---------------------------------------------------------

def test_selector_prefers_h264_and_aac():
    """The headline correctness fix: default selection yields AV1+Opus, which iOS
    Safari cannot reliably play. H.264/AAC must be requested first."""
    sel = downloader.build_format_selector(1080)
    assert sel.index("avc1") < sel.index("bv*[height<=1080]")
    assert "acodec^=mp4a" in sel


def test_selector_respects_height_cap():
    assert "height<=720" in downloader.build_format_selector(720)


def test_selector_has_unconstrained_fallback():
    """A download must never fail purely because no format matched."""
    assert downloader.build_format_selector(480).endswith("bv*+ba/b")


def test_selector_opt_out_of_compatibility():
    assert "avc1" not in downloader.build_format_selector(1080, prefer_compatible=False)


def test_probe_uses_shared_extract_info_result(monkeypatch):
    seen = {"extract_calls": []}
    source_info = {
        "_type": "playlist",
        "entries": [{
            "webpage_url": "https://example.com/watch/first",
            "title": "First video",
            "duration": 12,
            "thumbnail": "http://cdn.example.com/thumb.jpg",
            "uploader": "Uploader",
            "extractor_key": "Example",
            "formats": [{"height": 720, "vcodec": "avc1.64001f", "has_drm": False}],
            "subtitles": {"en": [{"ext": "vtt"}]},
            "automatic_captions": {},
        }],
    }

    class StubYoutubeDL:
        def __init__(self, opts):
            seen["opts"] = opts

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def extract_info(self, url, *, download):
            seen["extract_calls"].append((url, download))
            return source_info

        def sanitize_info(self, info):
            return info

    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", StubYoutubeDL)
    shared_extract_info = downloader.extract_info
    shared_calls = []

    def recording_extract_info(url):
        shared_calls.append(url)
        return shared_extract_info(url)

    monkeypatch.setattr(downloader, "extract_info", recording_extract_info)
    got = downloader.probe("https://example.com/playlist")

    assert shared_calls == ["https://example.com/playlist"]
    assert seen["extract_calls"] == [("https://example.com/playlist", False)]
    assert seen["opts"]["skip_download"] is True
    assert got["title"] == "First video"
    assert got["thumbnail"] == "https://cdn.example.com/thumb.jpg"
    assert got["subtitles"] == ["en"]


def test_download_source_audio_keeps_source_format(monkeypatch, tmp_path):
    captured = {}
    expected = tmp_path / "source.webm"

    def fake_run(url, opts, out_stem, *, default_ext, subtitle=False):
        captured.update({
            "url": url,
            "opts": opts,
            "out_stem": out_stem,
            "default_ext": default_ext,
            "subtitle": subtitle,
        })
        return expected

    monkeypatch.setattr(downloader, "_run", fake_run)
    got = downloader.download_source_audio(
        "https://example.com/v", tmp_path / "source", lambda _progress: None,
    )

    assert got == expected
    assert captured["opts"]["format"] == "bestaudio/best"
    assert "postprocessors" not in captured["opts"]
    assert captured["default_ext"] == "m4a"


def test_read_caption_track_validates_and_decodes_url(monkeypatch):
    signed_url = "https://captions.example.com/subtitle?sig=signed"
    seen = {}

    class Response:
        def read(self, limit):
            seen["read_limit"] = limit
            return b"Hello \xff"

    class StubYoutubeDL:
        def __init__(self, opts):
            seen["opts"] = opts

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def urlopen(self, url):
            seen["url"] = url
            return Response()

    def validate_url(url):
        seen["validated_url"] = url
        return url

    monkeypatch.setattr(downloader.security, "validate_url", validate_url)
    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", StubYoutubeDL)

    ext, raw = downloader.read_caption_track({"ext": "VTT", "url": signed_url})

    assert ext == "vtt"
    assert raw == "Hello �"
    assert seen["validated_url"] == signed_url
    assert seen["url"] == signed_url
    assert seen["read_limit"] == downloader.MAX_CAPTION_BYTES + 1


def test_read_caption_track_hides_signed_url_on_failure(monkeypatch):
    signed_url = "https://captions.example.com/subtitle?sig=secret"

    class StubYoutubeDL:
        def __init__(self, _opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def urlopen(self, url):
            raise RuntimeError(f"failed to read {url}")

    monkeypatch.setattr(downloader.security, "validate_url", lambda url: url)
    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", StubYoutubeDL)

    with pytest.raises(downloader.ProbeError, match="^字幕下载失败$") as exc_info:
        downloader.read_caption_track({"ext": "vtt", "url": signed_url})

    assert signed_url not in str(exc_info.value)


def test_caption_payload_limit_is_ten_mib():
    assert downloader.MAX_CAPTION_BYTES == 10 * 1024 * 1024


def test_read_caption_track_rejects_remote_payload_at_limit_plus_one(monkeypatch):
    seen = []

    class Response:
        def read(self, limit=None):
            seen.append(limit)
            return b"x" * (downloader.MAX_CAPTION_BYTES + 1)

    class StubYoutubeDL:
        def __init__(self, _opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def urlopen(self, _url):
            return Response()

    monkeypatch.setattr(downloader.security, "validate_url", lambda url: url)
    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", StubYoutubeDL)

    with pytest.raises(downloader.ProbeError, match="^字幕文件过大$"):
        downloader.read_caption_track({
            "ext": "vtt",
            "url": "https://captions.example.com/subtitle",
        })

    assert seen == [downloader.MAX_CAPTION_BYTES + 1]


@pytest.mark.parametrize("data", [b"123456", "你你"])
def test_read_caption_track_measures_embedded_payload_bytes(
    monkeypatch, data,
):
    monkeypatch.setattr(downloader, "MAX_CAPTION_BYTES", 5)

    with pytest.raises(downloader.ProbeError, match="^字幕文件过大$"):
        downloader.read_caption_track({"ext": "vtt", "data": data})


# --- error translation -------------------------------------------------------

@pytest.mark.parametrize("raw,needle", [
    ("ERROR: This video is DRM protected", "DRM"),
    ("Sign in to confirm you're not a bot", "登录"),
    ("ERROR: Video unavailable", "不存在"),
    ("ERROR: Private video, login required", "登录"),
    ("Unsupported URL: https://x.test/", "不支持"),
    ("socket timed out", "超时"),
])
def test_errors_become_actionable_chinese(raw, needle):
    assert needle in downloader._friendly_error(raw)


def test_error_message_is_bounded():
    assert len(downloader._friendly_error("boom " * 500)) <= 200


# --- retry classification ----------------------------------------------------

@pytest.mark.parametrize("message", [
    # Observed in real testing: YouTube 403s mid-download, and the retry works.
    "unable to download video data: HTTP Error 403: Forbidden",
    "socket timed out",
    "Connection reset by peer",
    "temporarily unavailable, try again",
])
def test_transient_errors_are_retried(message):
    assert downloader._is_retryable(message) is True


@pytest.mark.parametrize("message", [
    "This video is DRM protected",
    "Private video, login required",
    "Video unavailable",
    "Sign in to confirm you're not a bot",
    "Unsupported URL: https://x.test/",
])
def test_permanent_errors_are_not_retried(message):
    """Retrying a permanent failure only delays the message the user needs."""
    assert downloader._is_retryable(message) is False


# --- output discovery --------------------------------------------------------

def test_find_output_prefers_exact_extension(tmp_path):
    stem = tmp_path / "media"
    stem.with_suffix(".mp4").write_bytes(b"x" * 100)
    assert downloader._find_output(stem, "mp4", subtitle=False).suffix == ".mp4"


def test_find_output_ignores_part_files(tmp_path):
    stem = tmp_path / "media"
    (tmp_path / "media.mp4.part").write_bytes(b"x" * 999)
    stem.with_suffix(".webm").write_bytes(b"x" * 10)
    found = downloader._find_output(stem, "mp4", subtitle=False)
    assert found is not None and not found.name.endswith(".part")


def test_find_output_handles_subtitle_lang_infix(tmp_path):
    """yt-dlp writes media.en.srt, not media.srt."""
    stem = tmp_path / "media"
    (tmp_path / "media.en.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n")
    found = downloader._find_output(stem, "srt", subtitle=True)
    assert found is not None and found.suffix == ".srt"


def test_find_output_returns_none_when_nothing_written(tmp_path):
    assert downloader._find_output(tmp_path / "media", "mp4", subtitle=False) is None
