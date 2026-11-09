"""Cấu hình trung tâm của Local Media Server.

Các giá trị phụ thuộc môi trường được đọc tại thời điểm gọi hàm (không cache lúc import)
để dễ ghi đè khi chạy test.
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Nạp file `.env` đơn giản (KEY=VALUE). Biến môi trường có sẵn luôn được ưu tiên."""
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(BASE_DIR / ".env")

STATIC_DIR = BASE_DIR / "static"
THUMB_DIR = STATIC_DIR / "thumbnails"
TEMPLATES_DIR = BASE_DIR / "templates"
DEFAULT_MEDIA_DIR = BASE_DIR / "media"

VIDEO_EXTS = frozenset({".mp4", ".m4v", ".webm", ".mkv", ".mov", ".avi"})
SUBTITLE_EXTS = (".vtt", ".srt")

# Mỗi lần đọc file video là 1 MB.
CHUNK_SIZE = 1024 * 1024

# Giây thứ 180 (phút thứ 3) được dùng để cắt ảnh bìa; phim ngắn hơn sẽ lấy giữa phim.
THUMB_SECONDS = 180
FFPROBE_TIMEOUT = 30
FFMPEG_TIMEOUT = 90

# Giới hạn kích thước file phụ đề được đọc vào RAM (5 MB).
MAX_SUBTITLE_BYTES = 5 * 1024 * 1024

# Xem >= 95% thời lượng thì coi như đã xem xong và xoá khỏi "Xem tiếp".
FINISHED_RATIO = 0.95
# Vị trí dưới ngưỡng này (giây) không đáng để hiển thị trong "Xem tiếp".
MIN_RESUME_SECONDS = 5

MIME_OVERRIDES = {
    ".mkv": "video/x-matroska",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
    ".webm": "video/webm",
    ".mp4": "video/mp4",
}


def get_db_path() -> Path:
    """Đường dẫn file SQLite (đổi bằng biến môi trường MEDIA_DB)."""
    return Path(os.getenv("MEDIA_DB", str(BASE_DIR / "media.db")))


def auto_scan_enabled() -> bool:
    """Tự quét ổ cứng khi khởi động server (tắt bằng AUTO_SCAN=0)."""
    return os.getenv("AUTO_SCAN", "1") != "0"


def get_media_dirs() -> list[Path]:
    """Danh sách thư mục phim. Ngăn cách bằng `;` trên Windows (`:` trên Linux/Mac).

    Ví dụ: MEDIA_DIRS="D:/Movies;E:/PhimBo"
    """
    raw = os.getenv("MEDIA_DIRS", "")
    dirs = [Path(p.strip()).expanduser() for p in raw.split(os.pathsep) if p.strip()]
    return dirs or [DEFAULT_MEDIA_DIR]


def get_cors_origins() -> list[str]:
    """Origin được phép gọi API từ trang khác. Mặc định rỗng vì frontend cùng origin."""
    raw = os.getenv("CORS_ORIGINS", "")
    return [o.strip() for o in raw.split(",") if o.strip()]


def resolve_within_media_dirs(path: Path) -> Path | None:
    """Trả về đường dẫn đã resolve nếu nằm trong một thư mục phim, ngược lại None.

    Đây là hàng rào chống path traversal / symlink thoát khỏi thư mục phim.
    """
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError):
        return None
    for root in get_media_dirs():
        try:
            if resolved.is_relative_to(root.resolve()):
                return resolved
        except (OSError, RuntimeError):
            continue
    return None
