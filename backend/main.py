"""FastAPI app: routes, lifespan, and the TTL sweeper.

Run with a SINGLE worker. The job store is an in-process dict, so with multiple
workers a status poll can land on a worker that never saw the job.
    uvicorn main:app --port 8000
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

import ai
import config
import downloader
import jobs
import pro
from security import UnsafeURLError, content_disposition, content_type_for, validate_url

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
                await asyncio.to_thread(jobs.store.sweep)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("sweeper iteration failed")

    task = asyncio.create_task(sweeper())
    log.info("job dir: %s | ai: %s", config.JOB_DIR, "on" if ai.available() else "off")
    try:
        yield
    finally:
        task.cancel()
        jobs.store.shutdown()


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


class AIIn(BaseModel):
    url: str = Field(min_length=4, max_length=2048)
    title: str | None = Field(default=None, max_length=200)
    target_lang: str = Field(default="简体中文", max_length=32)


# --- routes ------------------------------------------------------------------

@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "ai_enabled": ai.available(),
        "active_jobs": jobs.store.count_active(),
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


@app.post("/api/redeem")
async def redeem_code(payload: RedeemIn, request: Request) -> dict[str, Any]:
    enforce_rate_limit(request)
    try:
        return pro.redeem(payload.code)
    except pro.RedeemError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/ai/summary")
async def ai_summary(
    payload: AIIn,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_pro_token: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    enforce_rate_limit(request)
    if not ai.available():
        raise HTTPException(status_code=503, detail="未配置 AI 密钥，该功能暂不可用")
    if not is_pro(authorization, x_pro_token):
        raise HTTPException(status_code=402, detail="AI 总结为 Pro 功能，请升级后使用")

    url = checked_url(payload.url)
    try:
        return await asyncio.to_thread(ai.summarize, url, payload.title)
    except ai.AIUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ai.AIError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/ai/translate")
async def ai_translate(
    payload: AIIn,
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
