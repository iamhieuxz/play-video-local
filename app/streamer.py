"""Truyền tải video theo HTTP Range (206 Partial Content) để trình duyệt tua được ngay."""
from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from typing import AsyncIterator

import aiofiles
from fastapi.responses import Response, StreamingResponse

from . import config

# Chỉ hỗ trợ 1 khoảng `bytes=start-end`, `bytes=start-` hoặc `bytes=-suffix`.
# Giới hạn 18 chữ số để tránh số nguyên khổng lồ.
_RANGE_RE = re.compile(r"^bytes=(\d{0,18})-(\d{0,18})$")


class RangeNotSatisfiable(Exception):
    """Khoảng byte nằm ngoài file -> trả 416."""


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """Phân tích header Range thành (start, end) gồm cả hai đầu.

    - None: không có Range, sai cú pháp hoặc nhiều khoảng -> phục vụ cả file (200), theo RFC 9110.
    - Raise RangeNotSatisfiable: khoảng hợp lệ nhưng vượt quá kích thước file.
    """
    if not header:
        return None
    match = _RANGE_RE.match(header.strip())
    if not match:
        return None
    start_s, end_s = match.groups()
    if not start_s and not end_s:
        return None

    if not start_s:  # bytes=-N : N byte cuối
        suffix = int(end_s)
        if suffix == 0 or size == 0:
            raise RangeNotSatisfiable
        return max(size - suffix, 0), size - 1

    start = int(start_s)
    end = int(end_s) if end_s else size - 1
    if end_s and end < start:
        return None  # khoảng sai thứ tự -> bỏ qua header
    if start >= size:
        raise RangeNotSatisfiable
    return start, min(end, size - 1)


def guess_media_type(path: Path) -> str:
    ext = path.suffix.lower()
    return config.MIME_OVERRIDES.get(ext) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"


async def iter_file(path: Path, start: int, end: int, chunk_size: int = config.CHUNK_SIZE) -> AsyncIterator[bytes]:
    """Đọc không chặn từ `start` đến `end` (gồm cả hai đầu), mỗi lần tối đa `chunk_size` byte."""
    remaining = end - start + 1
    async with aiofiles.open(path, "rb") as f:
        await f.seek(start)
        while remaining > 0:
            data = await f.read(min(chunk_size, remaining))
            if not data:
                break
            remaining -= len(data)
            yield data


def build_response(path: Path, size: int, range_header: str | None, method: str) -> Response:
    """Tạo response 200/206/416 cho file video; HEAD chỉ trả header."""
    media_type = guess_media_type(path)
    try:
        byte_range = parse_range(range_header, size)
    except RangeNotSatisfiable:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"})

    headers = {"Accept-Ranges": "bytes"}
    if byte_range is None:
        status_code, start, end = 200, 0, size - 1
    else:
        status_code = 206
        start, end = byte_range
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    length = max(end - start + 1, 0)
    headers["Content-Length"] = str(length)

    if method == "HEAD" or length == 0:
        return Response(status_code=status_code, headers=headers, media_type=media_type)
    return StreamingResponse(iter_file(path, start, end), status_code=status_code, headers=headers, media_type=media_type)
