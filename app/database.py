"""Kết nối SQLite, tạo bảng và các truy vấn (toàn bộ SQL của dự án nằm ở đây)."""
from __future__ import annotations

import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS movies (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT NOT NULL,
    file_path       TEXT NOT NULL UNIQUE,
    thumbnail_path  TEXT,
    duration        REAL,
    resolution      TEXT,
    folder_category TEXT
);

-- Cột vị trí đặt tên `position` vì CURRENT_TIME là từ khoá của SQLite.
CREATE TABLE IF NOT EXISTS watch_progress (
    movie_id   INTEGER PRIMARY KEY REFERENCES movies(id) ON DELETE CASCADE,
    position   REAL NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_progress_updated ON watch_progress(updated_at DESC);

-- Mỗi phim có thể có nhiều track phụ đề, mỗi ngôn ngữ một file.
CREATE TABLE IF NOT EXISTS subtitles (
    movie_id   INTEGER NOT NULL REFERENCES movies(id) ON DELETE CASCADE,
    lang       TEXT NOT NULL,
    label      TEXT NOT NULL,
    path       TEXT NOT NULL,
    is_default INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (movie_id, lang)
);
"""

# SELECT chung: ghép phim với tiến độ xem (nếu có) và số track phụ đề.
_MOVIE_SELECT = """
SELECT m.*, COALESCE(p.position, 0) AS position, p.updated_at AS watched_at,
       (SELECT COUNT(*) FROM subtitles s WHERE s.movie_id = m.id) AS subtitle_count
FROM movies m
LEFT JOIN watch_progress p ON p.movie_id = m.id
"""


def normalize_text(value: str | None) -> str:
    """Bỏ dấu tiếng Việt, đổi đ -> d và hạ chữ thường để tìm kiếm không phân biệt dấu."""
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFD", value.replace("đ", "d").replace("Đ", "D"))
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return stripped.casefold()


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Mở kết nối mới cho mỗi lần dùng (an toàn khi FastAPI chạy route `def` trong threadpool)."""
    conn = sqlite3.connect(config.get_db_path(), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.create_function("norm", 1, normalize_text, deterministic=True)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    db_path = config.get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)


# --------------------------------------------------------------------------- movies
def upsert_movie(
    *,
    title: str,
    file_path: str,
    thumbnail_path: str | None,
    duration: float | None,
    resolution: str | None,
    folder_category: str | None,
) -> int:
    """Thêm phim mới hoặc cập nhật metadata theo `file_path`; giữ nguyên id và tiến độ xem."""
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO movies (title, file_path, thumbnail_path,
                                duration, resolution, folder_category)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(file_path) DO UPDATE SET
                title = excluded.title,
                thumbnail_path = COALESCE(excluded.thumbnail_path, movies.thumbnail_path),
                duration = COALESCE(excluded.duration, movies.duration),
                resolution = COALESCE(excluded.resolution, movies.resolution),
                folder_category = excluded.folder_category
            """,
            (title, file_path, thumbnail_path, duration, resolution, folder_category),
        )
        row = conn.execute("SELECT id FROM movies WHERE file_path = ?", (file_path,)).fetchone()
        return int(row["id"])


def set_subtitles(movie_id: int, tracks: list[tuple[str, str, str, bool]]) -> None:
    """Thay toàn bộ track phụ đề của phim. Mỗi phần tử: (lang, label, path, is_default)."""
    with connect() as conn:
        conn.execute("DELETE FROM subtitles WHERE movie_id = ?", (movie_id,))
        conn.executemany(
            "INSERT INTO subtitles (movie_id, lang, label, path, is_default) VALUES (?, ?, ?, ?, ?)",
            [(movie_id, lang, label, path, int(is_default)) for lang, label, path, is_default in tracks],
        )


def list_subtitles(movie_id: int) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT lang, label, path, is_default FROM subtitles WHERE movie_id = ? ORDER BY is_default DESC, lang",
            (movie_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_subtitle(movie_id: int, lang: str | None = None) -> dict[str, Any] | None:
    """Track theo ngôn ngữ; không truyền `lang` thì lấy track mặc định."""
    with connect() as conn:
        if lang is None:
            row = conn.execute(
                "SELECT lang, label, path, is_default FROM subtitles WHERE movie_id = ? ORDER BY is_default DESC LIMIT 1",
                (movie_id,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT lang, label, path, is_default FROM subtitles WHERE movie_id = ? AND lang = ?",
                (movie_id, lang),
            ).fetchone()
    return dict(row) if row else None


def get_movie(movie_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(_MOVIE_SELECT + " WHERE m.id = ?", (movie_id,)).fetchone()
    return dict(row) if row else None


def get_movie_by_path(file_path: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(_MOVIE_SELECT + " WHERE m.file_path = ?", (file_path,)).fetchone()
    return dict(row) if row else None


def list_movies(query: str = "", limit: int = 100, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
    """Trả về (danh sách phim, tổng số khớp). Tìm theo tên hoặc thư mục, bỏ qua dấu."""
    where = ""
    params: list[Any] = []
    needle = normalize_text(query.strip())
    if needle:
        pattern = f"%{_escape_like(needle)}%"
        where = " WHERE norm(m.title) LIKE ? ESCAPE '\\' OR norm(m.folder_category) LIKE ? ESCAPE '\\'"
        params = [pattern, pattern]
    with connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM movies m" + where, params).fetchone()[0]
        rows = conn.execute(
            _MOVIE_SELECT + where + " ORDER BY m.title COLLATE NOCASE, m.id LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
    return [dict(r) for r in rows], int(total)


def all_movie_paths() -> list[tuple[int, str]]:
    with connect() as conn:
        rows = conn.execute("SELECT id, file_path FROM movies").fetchall()
    return [(int(r["id"]), r["file_path"]) for r in rows]


def delete_movies(ids: list[int]) -> None:
    if not ids:
        return
    with connect() as conn:
        conn.executemany("DELETE FROM movies WHERE id = ?", [(i,) for i in ids])


# ------------------------------------------------------------------------- progress
def save_progress(movie_id: int, position: float, duration: float | None = None) -> bool:
    """Lưu vị trí xem. Trả về False nếu phim không tồn tại.

    - Nếu DB chưa có thời lượng mà client biết thì bổ sung.
    - Xem gần hết (>= FINISHED_RATIO) thì xoá tiến độ để phim không còn ở "Xem tiếp".
    """
    with connect() as conn:
        row = conn.execute("SELECT duration FROM movies WHERE id = ?", (movie_id,)).fetchone()
        if row is None:
            return False
        known = row["duration"]
        if known is None and duration:
            conn.execute("UPDATE movies SET duration = ? WHERE id = ?", (duration, movie_id))
            known = duration
        if known and position >= known * config.FINISHED_RATIO:
            conn.execute("DELETE FROM watch_progress WHERE movie_id = ?", (movie_id,))
            return True
        conn.execute(
            """
            INSERT INTO watch_progress (movie_id, position, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(movie_id) DO UPDATE SET
                position = excluded.position, updated_at = excluded.updated_at
            """,
            (movie_id, position, _now()),
        )
    return True


def continue_watching(limit: int = 12) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            _MOVIE_SELECT
            + " WHERE p.position >= ? ORDER BY p.updated_at DESC, m.id LIMIT ?",
            (config.MIN_RESUME_SECONDS, limit),
        ).fetchall()
    return [dict(r) for r in rows]
