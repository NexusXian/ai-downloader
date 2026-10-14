"""FastAPI app: routes, lifespan, and the TTL sweeper.

Run with a SINGLE worker. The job store is an in-process dict, so with multiple
workers a status poll can land on a worker that never saw the job.
    uvicorn main:app --port 8000
"""
from __future__ import annotations

import asyncio
import functools
import importlib.util
import json
import logging
import shutil
from collections.abc import Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

import ai
import config
import downloader
import jobs
import mind_map
import pro
import summary_jobs
from security import (
    UnsafeURLError,
    content_disposition,
    content_type_for,
    sanitize_filename,
    validate_url,
)

logging.basicConfig(
    level=logging.DEBUG if config.DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    async def sweeper() -> None:
        while True:
            try:
                await asyncio.sleep(config.SWEEP_INTERVAL_SECONDS)
                await asyncio.gather(
                    asyncio.to_thread(jobs.store.sweep),
                    asyncio.to_thread(summary_jobs.store.sweep),
                )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("sweeper iteration failed")

    task = asyncio.create_task(sweeper())
    log.info(
        "job dirs: %s | %s | ai: %s | local asr: %s",
        config.JOB_DIR,
        config.SUMMARY_JOB_DIR,
        "on" if ai.available() else "off",
        "on" if local_asr_available() else "off",
    )
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        try:
            jobs.store.shutdown()
        finally:
            summary_jobs.store.shutdown()


app = FastAPI(title="万能视频下载器 API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "HEAD", "POST"],
    allow_headers=["*"],
)


# --- helpers -----------------------------------------------------------------

def client_ip(request: Request) -> str:
    # X-Forwarded-For is client-controlled unless a trusted proxy overwrites it.
    # Fine for per-IP fairness on a self-hosted box; not an authentication signal.
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def is_pro(authorization: str | None, x_pro_token: str | None) -> bool:
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    return pro.verify(token or x_pro_token)


def enforce_rate_limit(request: Request) -> None:
    allowed, retry_after = pro.limiter.check(client_ip(request))
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"请求过于频繁，请 {retry_after} 秒后再试",
            headers={"Retry-After": str(retry_after)},
        )


def checked_url(raw: str) -> str:
    try:
        return validate_url(raw)
    except UnsafeURLError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def canonical_url(raw: Any, fallback: str) -> str:
    """Return the extractor's canonical URL only if it passes the same URL guard.

    yt-dlp's sanitize_info does not validate URLs, and the summary worker refetches
    this value for audio, so an unvalidated canonical URL would bypass the guard.
    """
    if not isinstance(raw, str) or not raw.strip():
        return fallback
    try:
        return validate_url(raw)
    except ValueError:
        log.warning("ignoring unsafe canonical url from extractor metadata")
        return fallback


def local_asr_available() -> bool:
    return importlib.util.find_spec("faster_whisper") is not None


def require_summary_pro(
    authorization: str | None,
    x_pro_token: str | None,
) -> None:
    if not is_pro(authorization, x_pro_token):
        raise HTTPException(status_code=402, detail="AI 总结为 Pro 功能，请升级后使用")


@contextmanager
def no_store_ai_read_errors() -> Iterator[None]:
    try:
        yield
    except HTTPException as exc:
        exc.headers = {**(exc.headers or {}), "Cache-Control": "no-store"}
        raise
    except Exception:
        raise HTTPException(
            status_code=500,
            detail="服务器暂时不可用，请稍后重试",
            headers={"Cache-Control": "no-store"},
        ) from None


async def summary_job_or_404(job_id: str) -> summary_jobs.SummaryJob:
    # Offloaded: get() deep-copies the whole job, including the yt-dlp info dict.
    job = await asyncio.to_thread(summary_jobs.store.get, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return job


def require_finished_summary(job: summary_jobs.SummaryJob, pending: str) -> None:
    if job.status == "error":
        raise HTTPException(
            status_code=422,
            detail=job.error or "AI 总结失败，请稍后重试",
        )
    if job.status != "done":
        raise HTTPException(status_code=409, detail=pending)


# --- models ------------------------------------------------------------------

class ProbeIn(BaseModel):
    url: str = Field(min_length=4, max_length=2048)


class JobIn(BaseModel):
    url: str = Field(min_length=4, max_length=2048)
    mode: Literal["video", "audio", "subtitle"] = "video"
    height: int | None = Field(default=None, ge=144, le=4320)
    subtitle_lang: str | None = Field(default=None, max_length=32)


class BatchIn(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=50)
    mode: Literal["video", "audio", "subtitle"] = "video"
    height: int | None = Field(default=None, ge=144, le=4320)


class RedeemIn(BaseModel):
    code: str = Field(min_length=1, max_length=64)


class AISummaryJobIn(BaseModel):
    url: str = Field(min_length=4, max_length=2048)
    title: str | None = Field(default=None, max_length=200)
    source_lang: str = Field(default="auto", min_length=1, max_length=32)
    output_lang: Literal["zh-CN", "en"] = "zh-CN"


class AITranslateIn(BaseModel):
    url: str = Field(min_length=4, max_length=2048)
    title: str | None = Field(default=None, max_length=200)
    target_lang: str = Field(default="简体中文", max_length=32)


# --- routes ------------------------------------------------------------------

@app.get("/api/health")
async def health() -> dict[str, Any]:
    active_ai_jobs = await asyncio.to_thread(summary_jobs.store.count_active)
    return {
        "status": "ok",
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "ai_enabled": ai.available(),
        "active_jobs": jobs.store.count_active(),
        "active_ai_jobs": active_ai_jobs,
        "local_asr": local_asr_available(),
        "disk_ok": jobs.disk_has_room(),
    }


@app.get("/api/limits")
async def limits(
    authorization: Annotated[str | None, Header()] = None,
    x_pro_token: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    pro_user = is_pro(authorization, x_pro_token)
    return {
        "is_pro": pro_user,
        **pro.limits_for(pro_user),
        "free_max_height": config.FREE_MAX_HEIGHT,
        "pro_max_height": config.PRO_MAX_HEIGHT,
        "free_batch_limit": config.FREE_BATCH_LIMIT,
        "pro_batch_limit": config.PRO_BATCH_LIMIT,
        "ai_enabled": ai.available(),
    }


@app.post("/api/probe")
async def probe(payload: ProbeIn, request: Request) -> dict[str, Any]:
    enforce_rate_limit(request)
    url = checked_url(payload.url)
    try:
        return await asyncio.to_thread(downloader.probe, url)
    except downloader.ProbeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/jobs", status_code=202)
async def create_job(
    payload: JobIn,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_pro_token: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    enforce_rate_limit(request)
    url = checked_url(payload.url)

    if not jobs.disk_has_room():
        raise HTTPException(status_code=507, detail="服务器磁盘空间不足，请稍后重试")

    pro_user = is_pro(authorization, x_pro_token)
    if payload.mode == "audio" and not pro_user:
        raise HTTPException(status_code=402, detail="MP3 提取为 Pro 功能，请升级后使用")

    job = jobs.store.create(
        url=url,
        mode=payload.mode,
        max_height=pro.clamp_height(payload.height, pro_user),
        subtitle_lang=payload.subtitle_lang,
        is_pro=pro_user,
    )
    jobs.store.submit(job)
    return job.public()


@app.post("/api/batch", status_code=202)
async def create_batch(
    payload: BatchIn,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_pro_token: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    enforce_rate_limit(request)
    pro_user = is_pro(authorization, x_pro_token)
    cap = pro.limits_for(pro_user)["batch_limit"]

    urls = [u.strip() for u in payload.urls if u.strip()]
    if len(urls) > cap:
        raise HTTPException(
            status_code=402,
            detail=(
                f"免费版单次仅支持 {cap} 个链接，升级 Pro 可批量下载 "
                f"{config.PRO_BATCH_LIMIT} 个"
            ),
        )
    if not jobs.disk_has_room():
        raise HTTPException(status_code=507, detail="服务器磁盘空间不足，请稍后重试")

    created, rejected = [], []
    for raw in urls:
        try:
            url = validate_url(raw)
        except UnsafeURLError as exc:
            rejected.append({"url": raw, "error": str(exc)})
            continue
        job = jobs.store.create(
            url=url, mode=payload.mode,
            max_height=pro.clamp_height(payload.height, pro_user),
            is_pro=pro_user,
        )
        jobs.store.submit(job)
        created.append(job.public())

    return {"jobs": created, "rejected": rejected}


@app.get("/api/jobs/{job_id}")
async def job_status(job_id: str) -> dict[str, Any]:
    job = jobs.store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return job.public()


@app.api_route("/api/jobs/{job_id}/file", methods=["GET", "HEAD"])
async def job_file(job_id: str) -> FileResponse:
    job = jobs.store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    if job.status != "done" or not job.filepath:
        raise HTTPException(status_code=409, detail="文件尚未准备好")

    path = Path(job.filepath)
    # Confirm the resolved path is still inside the job dir before serving.
    try:
        path.resolve().relative_to(config.JOB_DIR)
    except ValueError:
        raise HTTPException(status_code=403, detail="非法文件路径") from None
    if not path.is_file():
        raise HTTPException(status_code=410, detail="文件已被清理，请重新下载")

    filename = job.filename or f"video.{job.ext or 'mp4'}"
    # FileResponse handles Range/Accept-Ranges (starlette >= 0.49.1, which fixes
    # GHSA-7f5h-v6xp-fcq8). Content-Disposition is set explicitly so Chinese
    # filenames survive: ASCII fallback plus RFC 5987 UTF-8 form.
    return FileResponse(
        path,
        media_type=content_type_for(job.ext or "mp4"),
        headers={
            "Content-Disposition": content_disposition(filename),
            "Cache-Control": "no-store",
        },
    )


@app.api_route("/api/jobs/{job_id}/play", methods=["GET", "HEAD"])
async def job_play(job_id: str) -> FileResponse:
    """Same bytes as /file, marked inline so a <video> element can seek it.

    Chrome refuses to play a media response marked as an attachment, so the mind
    map's click-to-seek needs its own disposition rather than reusing /file.
    """
    job = jobs.store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    if job.status != "done" or not job.filepath:
        raise HTTPException(status_code=409, detail="文件尚未准备好")

    path = Path(job.filepath)
    try:
        path.resolve().relative_to(config.JOB_DIR)
    except ValueError:
        raise HTTPException(status_code=403, detail="非法文件路径") from None
    if not path.is_file():
        raise HTTPException(status_code=410, detail="文件已被清理，请重新下载")

    filename = job.filename or f"video.{job.ext or 'mp4'}"
    return FileResponse(
        path,
        media_type=content_type_for(job.ext or "mp4"),
        headers={
            "Content-Disposition": content_disposition(filename, inline=True),
            "Cache-Control": "no-store",
        },
    )


@app.post("/api/redeem")
async def redeem_code(payload: RedeemIn, request: Request) -> dict[str, Any]:
    enforce_rate_limit(request)
    try:
        return pro.redeem(payload.code)
    except pro.RedeemError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/ai/jobs", status_code=202)
async def create_ai_job(
    payload: AISummaryJobIn,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_pro_token: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    enforce_rate_limit(request)
    if not ai.available():
        raise HTTPException(status_code=503, detail="未配置 AI 密钥，该功能暂不可用")
    require_summary_pro(authorization, x_pro_token)

    url = await asyncio.to_thread(checked_url, payload.url)
    if not await asyncio.to_thread(summary_jobs.disk_has_room):
        raise HTTPException(status_code=507, detail="服务器磁盘空间不足，请稍后重试")
    if not await asyncio.to_thread(summary_jobs.store.can_accept):
        raise HTTPException(status_code=503, detail="AI 总结队列已满，请稍后重试")

    try:
        source_info = await asyncio.to_thread(downloader.extract_info, url)
    except downloader.ProbeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    canonical = await asyncio.to_thread(
        canonical_url,
        source_info.get("webpage_url"),
        url,
    )
    try:
        # Offloaded: create() deep-copies the whole yt-dlp info dict under a lock.
        job = await asyncio.to_thread(
            functools.partial(
                summary_jobs.store.create,
                url=canonical,
                title=source_info.get("title") or payload.title or "未命名视频",
                source_lang=payload.source_lang,
                output_lang=payload.output_lang,
                source_info=source_info,
            )
        )
    except summary_jobs.SummaryQueueFull:
        raise HTTPException(
            status_code=503,
            detail="AI 总结队列已满，请稍后重试",
        ) from None
    except OSError:
        log.warning("summary job storage preparation failed")
        raise HTTPException(
            status_code=507,
            detail="服务器磁盘空间不足，请稍后重试",
        ) from None
    try:
        await asyncio.to_thread(summary_jobs.store.submit, job)
    except summary_jobs.SummarySubmissionError:
        raise HTTPException(
            status_code=503,
            detail="AI 总结服务暂时不可用，请稍后重试",
        ) from None
    return job.public()


@app.get("/api/ai/jobs/{job_id}")
async def ai_job_status(
    job_id: str,
    response: Response,
    authorization: Annotated[str | None, Header()] = None,
    x_pro_token: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    with no_store_ai_read_errors():
        require_summary_pro(authorization, x_pro_token)
        job = await summary_job_or_404(job_id)
        response.headers["Cache-Control"] = "no-store"
        return job.public()


@app.get("/api/ai/jobs/{job_id}/result")
async def ai_job_result(
    job_id: str,
    authorization: Annotated[str | None, Header()] = None,
    x_pro_token: Annotated[str | None, Header()] = None,
) -> Response:
    with no_store_ai_read_errors():
        require_summary_pro(authorization, x_pro_token)
        job = await summary_job_or_404(job_id)
        require_finished_summary(job, "总结结果尚未准备好")
        try:
            content = await asyncio.to_thread(summary_jobs.store.read_summary, job_id)
        except PermissionError:
            raise HTTPException(status_code=403, detail="非法文件路径") from None
        except RuntimeError:
            raise HTTPException(status_code=404, detail="任务不存在或已过期") from None
        return Response(
            content=content,
            media_type=f"{content_type_for('md')}; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )


@app.get("/api/ai/jobs/{job_id}/export")
async def ai_job_export(
    job_id: str,
    authorization: Annotated[str | None, Header()] = None,
    x_pro_token: Annotated[str | None, Header()] = None,
) -> Response:
    with no_store_ai_read_errors():
        require_summary_pro(authorization, x_pro_token)
        job = await summary_job_or_404(job_id)
        require_finished_summary(job, "总结文件尚未准备好")
        try:
            content = await asyncio.to_thread(summary_jobs.store.read_summary, job_id)
        except PermissionError:
            raise HTTPException(status_code=403, detail="非法文件路径") from None
        except RuntimeError:
            raise HTTPException(status_code=404, detail="任务不存在或已过期") from None

        filename = f"{sanitize_filename(job.title)}.md"
        return Response(
            content=content,
            media_type=f"{content_type_for('md')}; charset=utf-8",
            headers={
                "Content-Disposition": content_disposition(filename),
                "Cache-Control": "no-store",
            },
        )


@app.post("/api/ai/jobs/{job_id}/mind-map", status_code=202)
async def create_ai_mind_map(
    job_id: str,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_pro_token: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    enforce_rate_limit(request)
    if not ai.available():
        raise HTTPException(status_code=503, detail="未配置 AI 密钥，该功能暂不可用")
    require_summary_pro(authorization, x_pro_token)

    try:
        job = await asyncio.to_thread(summary_jobs.store.submit_mind_map, job_id)
    except summary_jobs.MindMapBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except summary_jobs.MindMapUnavailable as exc:
        detail = str(exc)
        status = 404 if "过期" in detail else 409
        raise HTTPException(status_code=status, detail=detail) from None
    except summary_jobs.MindMapSubmissionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    return job.public()


async def read_mind_map_or_error(job_id: str) -> dict[str, Any]:
    job = await summary_job_or_404(job_id)
    if job.mind_map_status == "error":
        raise HTTPException(
            status_code=422,
            detail=job.mind_map_error or "思维导图生成失败，请稍后重试",
        )
    if job.mind_map_status != "done":
        raise HTTPException(status_code=409, detail="思维导图尚未准备好")
    try:
        raw = await asyncio.to_thread(summary_jobs.store.read_mind_map, job_id)
    except PermissionError:
        raise HTTPException(status_code=403, detail="非法文件路径") from None
    except RuntimeError:
        raise HTTPException(status_code=404, detail="任务不存在或已过期") from None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise HTTPException(status_code=422, detail="思维导图数据已损坏，请重新生成") from None
    return {"job": job, "mind_map": payload}


@app.get("/api/ai/jobs/{job_id}/mind-map")
async def ai_mind_map_result(
    job_id: str,
    authorization: Annotated[str | None, Header()] = None,
    x_pro_token: Annotated[str | None, Header()] = None,
) -> Response:
    with no_store_ai_read_errors():
        require_summary_pro(authorization, x_pro_token)
        loaded = await read_mind_map_or_error(job_id)
        return JSONResponse(
            content=loaded["mind_map"],
            headers={"Cache-Control": "no-store"},
        )


@app.get("/api/ai/jobs/{job_id}/mind-map/export")
async def ai_mind_map_export(
    job_id: str,
    format: Literal["json", "markdown", "opml"] = "json",
    authorization: Annotated[str | None, Header()] = None,
    x_pro_token: Annotated[str | None, Header()] = None,
) -> Response:
    with no_store_ai_read_errors():
        require_summary_pro(authorization, x_pro_token)
        loaded = await read_mind_map_or_error(job_id)
        job = loaded["job"]
        payload = loaded["mind_map"]
        stem = sanitize_filename(job.title)

        try:
            if format == "markdown":
                body = mind_map.to_markdown(payload, job.title)
                ext = "md"
            elif format == "opml":
                body = mind_map.to_opml(payload, job.title)
                ext = "opml"
            else:
                body = json.dumps(payload, ensure_ascii=False, indent=2)
                ext = "json"
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail="思维导图数据已损坏，请重新生成",
            ) from None

        return Response(
            content=body.encode("utf-8"),
            media_type=f"{content_type_for(ext)}; charset=utf-8",
            headers={
                "Content-Disposition": content_disposition(f"{stem}-mindmap.{ext}"),
                "Cache-Control": "no-store",
            },
        )


@app.post("/api/ai/translate")
async def ai_translate(
    payload: AITranslateIn,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_pro_token: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    enforce_rate_limit(request)
    if not ai.available():
        raise HTTPException(status_code=503, detail="未配置 AI 密钥，该功能暂不可用")
    if not is_pro(authorization, x_pro_token):
        raise HTTPException(status_code=402, detail="字幕翻译为 Pro 功能，请升级后使用")

    url = checked_url(payload.url)
    try:
        return await asyncio.to_thread(ai.translate_subtitles, url, payload.target_lang)
    except ai.AIUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ai.AIError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/sites")
async def sites() -> dict[str, Any]:
    """Popular platforms, for display. yt-dlp supports ~1800 extractors."""
    return {
        "total": "1800+",
        "popular": [
            {"name": "YouTube", "slug": "youtube"},
            {"name": "哔哩哔哩", "slug": "bilibili"},
            {"name": "抖音", "slug": "douyin"},
            {"name": "TikTok", "slug": "tiktok"},
            {"name": "微博", "slug": "weibo"},
            {"name": "小红书", "slug": "xiaohongshu"},
            {"name": "X / Twitter", "slug": "twitter"},
            {"name": "Instagram", "slug": "instagram"},
            {"name": "Vimeo", "slug": "vimeo"},
        ],
    }


@app.exception_handler(UnsafeURLError)
async def unsafe_url_handler(_request: Request, exc: UnsafeURLError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})
