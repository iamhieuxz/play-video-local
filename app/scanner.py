"""Quét ổ cứng, đọc thông tin bằng ffprobe và tạo ảnh bìa bằng ffmpeg."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import config, database, subtitle

log = logging.getLogger("scanner")

# Không cho phép hai lần quét chạy chồng lên nhau.
_scan_lock = threading.Lock()

# Windows: không bật cửa sổ console khi gọi ffmpeg ngầm.
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass(frozen=True)
class ScanResult:
    found: int = 0
    added: int = 0
    removed: int = 0
    skipped_running: bool = False


# --------------------------------------------------------------------------- ffmpeg
def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _run(args: list[str], timeout: int) -> subprocess.CompletedProcess[bytes] | None:
    """Chạy lệnh CLI với danh sách đối số (không dùng shell). Trả None nếu lỗi/quá hạn."""
    try:
        return subprocess.run(
            args,
            capture_output=True,
            timeout=timeout,
            check=False,
            creationflags=_CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("Command failed (%s): %s", args[0], exc)
        return None


def resolution_label(width: int | None, height: int | None) -> str | None:
    if not height:
        return None
    if (width or 0) >= 3800 or height >= 2000:
        return "4K"
    return f"{height}p"


def probe(path: Path) -> tuple[float | None, str | None]:
    """Trả về (thời lượng giây, nhãn độ phân giải) bằng ffprobe."""
    result = _run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-show_entries", "format=duration",
            "-of", "json",
            str(path),
        ],
        config.FFPROBE_TIMEOUT,
    )
    if result is None or result.returncode != 0:
        return None, None
    try:
        data = json.loads(result.stdout or b"{}")
        duration_raw = data.get("format", {}).get("duration")
        duration = float(duration_raw) if duration_raw not in (None, "N/A") else None
        streams = data.get("streams") or [{}]
        resolution = resolution_label(streams[0].get("width"), streams[0].get("height"))
        return duration, resolution
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None, None


def make_thumbnail(video: Path, out: Path, duration: float | None) -> bool:
    """Cắt 1 khung hình làm ảnh bìa: phút thứ 3, hoặc giữa phim nếu phim ngắn."""
    if duration and duration > config.THUMB_SECONDS * 2:
        seek = config.THUMB_SECONDS
    elif duration:
        seek = duration / 2
    else:
        seek = 1
    result = _run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-ss", f"{seek:.2f}",
            "-i", str(video),
            "-frames:v", "1",
            "-vf", "scale=480:-2",
            "-q:v", "4",
            str(out),
        ],
        config.FFMPEG_TIMEOUT,
    )
    ok = result is not None and result.returncode == 0 and out.exists() and out.stat().st_size > 0
    if not ok and out.exists():
        out.unlink(missing_ok=True)
    return ok


# --------------------------------------------------------------------------- helpers
def thumb_name(video: Path) -> str:
    """Tên ảnh bìa ổn định theo đường dẫn file (đổi tên/di chuyển file thì tạo ảnh mới)."""
    digest = hashlib.sha1(str(video).encode("utf-8", "surrogatepass")).hexdigest()[:16]
    return f"{digest}.jpg"


def make_title(video: Path) -> str:
    return video.stem.replace("_", " ").strip() or video.stem


def category_of(video: Path, root: Path) -> str:
    """Tên thư mục con đầu tiên bên dưới thư mục gốc; phim nằm ngay ở gốc thì lấy tên thư mục gốc."""
    try:
        parts = video.relative_to(root).parts
    except ValueError:
        return root.name
    return parts[0] if len(parts) > 1 else (root.name or str(root))


def iter_videos(root: Path, on_error: Callable[[OSError], None] | None = None):
    """Duyệt đệ quy, bỏ qua file/thư mục ẩn. Lỗi đọc thư mục được báo qua `on_error`."""
    for dirpath, dirnames, filenames in os.walk(root, onerror=on_error):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            if name.startswith("."):
                continue
            path = Path(dirpath) / name
            if path.suffix.lower() in config.VIDEO_EXTS:
                yield path


def _under_verified_root(file_path: str, roots: list[Path]) -> bool:
    """Đường dẫn trong DB có chắc chắn nằm trong một thư mục vừa quét xong hay không."""
    path = Path(file_path)
    return any(path.is_relative_to(root) for root in roots)


# --------------------------------------------------------------------------- scan
def is_scanning() -> bool:
    return _scan_lock.locked()


def start_background_scan() -> bool:
    """Chạy quét ở luồng nền. Trả False nếu đang có lần quét khác."""
    if _scan_lock.locked():
        return False
    threading.Thread(target=scan_all, name="media-scan", daemon=True).start()
    return True


def scan_all() -> ScanResult:
    """Quét mọi thư mục phim, cập nhật DB và xoá phim đã mất khỏi ổ cứng.

    Phim chỉ bị xoá khi thư mục gốc chứa nó được quét trọn vẹn trong lần quét này:
    rút ổ cứng ngoài hay đổi `MEDIA_DIRS` không được làm mất danh sách và tiến độ xem.
    """
    if not _scan_lock.acquire(blocking=False):
        return ScanResult(skipped_running=True)
    try:
        database.init_db()
        config.THUMB_DIR.mkdir(parents=True, exist_ok=True)
        ffmpeg_ok = has_ffmpeg()
        if not ffmpeg_ok:
            log.warning("ffmpeg/ffprobe not found in PATH: no thumbnails or durations will be generated")

        seen: set[str] = set()
        added = 0
        # Chỉ những thư mục gốc quét trọn vẹn mới đủ căn cứ để xoá phim được coi là đã mất.
        verified_roots: list[Path] = []
        for root in config.get_media_dirs():
            if not root.is_dir():
                log.warning("Media directory not found, keeping its movies: %s", root)
                continue
            root = root.resolve()
            walk_errors: list[OSError] = []
            for video in iter_videos(root, on_error=walk_errors.append):
                key = str(video)
                seen.add(key)
                existing = database.get_movie_by_path(key)
                if _index_video(video, root, existing, ffmpeg_ok) and existing is None:
                    added += 1
            if walk_errors:
                log.warning("Incomplete scan of %s, keeping its movies", root)
            else:
                verified_roots.append(root)

        stale = [
            movie_id
            for movie_id, path in database.all_movie_paths()
            if path not in seen and _under_verified_root(path, verified_roots)
        ]
        database.delete_movies(stale)
        return ScanResult(found=len(seen), added=added, removed=len(stale))
    finally:
        _scan_lock.release()


def _index_video(video: Path, root: Path, existing: dict | None, ffmpeg_ok: bool) -> bool:
    try:
        duration = existing["duration"] if existing else None
        resolution = existing["resolution"] if existing else None
        thumb_rel = existing["thumbnail_path"] if existing else None

        if ffmpeg_ok and (duration is None or resolution is None):
            probed_duration, probed_resolution = probe(video)
            duration = duration if duration is not None else probed_duration
            resolution = resolution or probed_resolution

        thumb_file = config.THUMB_DIR / thumb_name(video)
        if ffmpeg_ok and not thumb_file.exists():
            make_thumbnail(video, thumb_file, duration)
        thumb_rel = f"thumbnails/{thumb_file.name}" if thumb_file.exists() else thumb_rel

        movie_id = database.upsert_movie(
            title=make_title(video),
            file_path=str(video),
            thumbnail_path=thumb_rel,
            duration=duration,
            resolution=resolution,
            folder_category=category_of(video, root),
        )
        tracks = subtitle.discover(video, root)
        database.set_subtitles(movie_id, [(t.lang, t.label, str(t.path), t.is_default) for t in tracks])
        return True
    except Exception:  # một file lỗi không được làm hỏng cả lần quét
        log.exception("Failed to index %s", video)
        return False
