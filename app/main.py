"""Điểm khởi chạy FastAPI: cấu hình middleware và định tuyến. Logic nằm ở các module khác."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import config, database, scanner, streamer, subtitle
from .schemas import MovieList, MovieOut, ProgressIn, ScanStatus

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

# CSP: chỉ cho phép tài nguyên cùng origin và 2 CDN đã ghim phiên bản (Tailwind, Plyr).
CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self' https://cdn.tailwindcss.com https://cdn.plyr.io",
        "style-src 'self' 'unsafe-inline' https://cdn.plyr.io",  # Tailwind CDN và Plyr chèn style lúc chạy
        "img-src 'self' data: blob: https://cdn.plyr.io",
        "media-src 'self' blob: https://cdn.plyr.io",
        "connect-src 'self' https://cdn.plyr.io",
        "object-src 'none'",
        "base-uri 'none'",
        "frame-ancestors 'none'",
    ]
)


class SecurityHeadersMiddleware:
    """Thêm header bảo mật vào mọi response (ASGI thuần để không ảnh hưởng streaming)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("X-Frame-Options", "DENY")
                headers.setdefault("Referrer-Policy", "no-referrer")
                headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
            await send(message)

        await self.app(scope, receive, send_with_headers)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    database.init_db()
    config.THUMB_DIR.mkdir(parents=True, exist_ok=True)
    if config.auto_scan_enabled():
        scanner.start_background_scan()
    yield


app = FastAPI(title="Local Media Server", lifespan=lifespan)
app.add_middleware(SecurityHeadersMiddleware)
if config.get_cors_origins():
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.get_cors_origins(),
        allow_methods=["GET", "HEAD", "POST"],
        allow_headers=["Content-Type", "Range"],
    )
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")


def _require_movie(movie_id: int) -> dict:
    movie = database.get_movie(movie_id)
    if movie is None:
        raise HTTPException(status_code=404, detail="Movie not found")
    return movie


def _safe_file(raw_path: str | None) -> Path | None:
    """File phải tồn tại và nằm trong thư mục phim đã cấu hình."""
    if not raw_path:
        return None
    path = config.resolve_within_media_dirs(Path(raw_path))
    return path if path is not None and path.is_file() else None


# ------------------------------------------------------------------------------ pages
@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(config.TEMPLATES_DIR / "index.html", media_type="text/html; charset=utf-8")


# -------------------------------------------------------------------------------- API
@app.get("/api/movies", response_model=MovieList)
def api_movies(
    q: str = Query("", max_length=100),
    limit: int = Query(60, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> MovieList:
    rows, total = database.list_movies(q, limit, offset)
    return MovieList(items=[MovieOut.from_row(r) for r in rows], total=total)


@app.get("/api/movies/{movie_id}", response_model=MovieOut)
def api_movie(movie_id: int) -> MovieOut:
    return MovieOut.from_row(_require_movie(movie_id), database.list_subtitles(movie_id))


@app.get("/api/continue", response_model=list[MovieOut])
def api_continue() -> list[MovieOut]:
    return [MovieOut.from_row(r) for r in database.continue_watching()]


@app.post("/api/progress", status_code=204)
def api_progress(body: ProgressIn) -> Response:
    if not database.save_progress(body.movie_id, body.position, body.duration):
        raise HTTPException(status_code=404, detail="Movie not found")
    return Response(status_code=204)


@app.post("/api/scan", response_model=ScanStatus, status_code=202)
def api_scan() -> ScanStatus:
    started = scanner.start_background_scan()
    return ScanStatus(running=True, started=started)


@app.get("/api/scan/status", response_model=ScanStatus)
def api_scan_status() -> ScanStatus:
    return ScanStatus(running=scanner.is_scanning())


# --------------------------------------------------------------------------- media
@app.api_route("/stream/{movie_id}", methods=["GET", "HEAD"], include_in_schema=False)
def stream(movie_id: int, request: Request) -> Response:
    movie = _require_movie(movie_id)
    path = _safe_file(movie["file_path"])
    if path is None:
        raise HTTPException(status_code=404, detail="Video file is missing")
    return streamer.build_response(path, path.stat().st_size, request.headers.get("range"), request.method)


@app.get("/subtitles/{movie_id}")
@app.get("/subtitles/{movie_id}/{lang}")
def subtitles(movie_id: int, lang: str | None = None) -> Response:
    """Phụ đề dạng WebVTT. Không chỉ định ngôn ngữ thì trả track mặc định."""
    _require_movie(movie_id)
    track = database.get_subtitle(movie_id, lang)
    path = _safe_file(track["path"]) if track else None
    if path is None:
        raise HTTPException(status_code=404, detail="Subtitle not found")
    try:
        content = subtitle.load_as_vtt(path)
    except ValueError:
        raise HTTPException(status_code=413, detail="Subtitle file is too large")
    return Response(content, media_type="text/vtt; charset=utf-8", headers={"Cache-Control": "no-cache"})
