from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config, scanner


@pytest.fixture()
def media_env(tmp_path, monkeypatch):
    """Thư mục phim + DB + thư mục ảnh bìa riêng cho mỗi test."""
    media = tmp_path / "movies"
    media.mkdir()
    monkeypatch.setenv("MEDIA_DIRS", str(media))
    monkeypatch.setenv("MEDIA_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("AUTO_SCAN", "0")
    monkeypatch.setattr(config, "THUMB_DIR", tmp_path / "thumbs")
    return media


@pytest.fixture()
def client(media_env):
    from app.main import app

    with TestClient(app) as c:
        yield c


def write_fake_video(path: Path, size: int = 5000) -> bytes:
    """File .mp4 giả với nội dung byte xác định (đủ để kiểm tra Range)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = bytes(i % 251 for i in range(size))
    path.write_bytes(data)
    return data


@pytest.fixture()
def real_video(tmp_path) -> Path:
    """Video thật 6 giây tạo bằng ffmpeg; bỏ qua test nếu máy không có ffmpeg."""
    if not scanner.has_ffmpeg():
        pytest.skip("ffmpeg not installed")
    out = tmp_path / "sample.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=6:size=320x240:rate=10",
         "-pix_fmt", "yuv420p", str(out)],
        check=True, timeout=60,
    )
    return out
