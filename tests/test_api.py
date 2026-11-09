from __future__ import annotations

import time

from app import config, database, scanner

from .conftest import write_fake_video


def _scan_with_fake(media_env):
    data = write_fake_video(media_env / "Hành động" / "Phim_Một.mp4", 5000)
    (media_env / "Hành động" / "Phim_Một.srt").write_text("1\n00:00:01,000 --> 00:00:03,000\nXin chào các bạn\n", encoding="utf-8")
    (media_env / "Hành động" / "Phim_Một_whisper.srt").write_text("1\n00:00:01,000 --> 00:00:03,000\n私は幼少期から競争世界に生きてきた\n", encoding="utf-8")
    scanner.scan_all()
    return data


def test_range_requests(client, media_env):
    data = _scan_with_fake(media_env)
    movie_id = client.get("/api/movies").json()["items"][0]["id"]

    full = client.get(f"/stream/{movie_id}")
    assert full.status_code == 200
    assert full.headers["accept-ranges"] == "bytes"
    assert full.content == data

    part = client.get(f"/stream/{movie_id}", headers={"Range": "bytes=100-199"})
    assert part.status_code == 206
    assert part.headers["content-range"] == "bytes 100-199/5000"
    assert part.headers["content-length"] == "100"
    assert part.content == data[100:200]

    tail = client.get(f"/stream/{movie_id}", headers={"Range": "bytes=4900-"})
    assert tail.status_code == 206 and tail.content == data[4900:]

    bad = client.get(f"/stream/{movie_id}", headers={"Range": "bytes=9000-"})
    assert bad.status_code == 416
    assert bad.headers["content-range"] == "bytes */5000"

    head = client.head(f"/stream/{movie_id}")
    assert head.status_code == 200 and head.headers["content-length"] == "5000" and head.content == b""


def test_stream_unknown_movie_404(client):
    assert client.get("/stream/9999").status_code == 404


def test_listing_hides_file_paths_and_search_ignores_accents(client, media_env):
    _scan_with_fake(media_env)
    body = client.get("/api/movies").json()
    assert body["total"] == 1
    item = body["items"][0]
    assert "file_path" not in item and "subtitle_path" not in item
    assert item["title"] == "Phim Một" and item["folder_category"] == "Hành động" and item["has_subtitle"]

    assert client.get("/api/movies", params={"q": "phim mot"}).json()["total"] == 1
    assert client.get("/api/movies", params={"q": "hanh dong"}).json()["total"] == 1
    assert client.get("/api/movies", params={"q": "khong co"}).json()["total"] == 0
    # ký tự đại diện của LIKE phải được escape
    assert client.get("/api/movies", params={"q": "%"}).json()["total"] == 0
    assert client.get("/api/movies", params={"q": "_"}).json()["total"] == 0


def test_subtitles_served_as_vtt_per_language(client, media_env):
    _scan_with_fake(media_env)
    movie_id = client.get("/api/movies").json()["items"][0]["id"]

    detail = client.get(f"/api/movies/{movie_id}").json()
    assert [(s["lang"], s["default"]) for s in detail["subtitles"]] == [("vi", True), ("ja", False)]
    assert all("path" not in s for s in detail["subtitles"])

    default = client.get(f"/subtitles/{movie_id}")
    assert default.status_code == 200
    assert default.headers["content-type"].startswith("text/vtt")
    assert default.text.startswith("WEBVTT") and "00:00:01.000 --> 00:00:03.000" in default.text
    assert "Xin chào các bạn" in default.text

    ja = client.get(detail["subtitles"][1]["url"])
    assert ja.status_code == 200 and "競争世界" in ja.text
    assert client.get(f"/subtitles/{movie_id}/en").status_code == 404
    assert client.get(f"/subtitles/{movie_id}/..%2f..%2fetc").status_code == 404


def test_progress_and_continue_watching(client, media_env):
    _scan_with_fake(media_env)
    movie = client.get("/api/movies").json()["items"][0]
    movie_id = movie["id"]

    assert client.get("/api/continue").json() == []
    assert client.post("/api/progress", json={"movie_id": movie_id, "position": 42.5, "duration": 100}).status_code == 204
    cont = client.get("/api/continue").json()
    assert len(cont) == 1 and cont[0]["position"] == 42.5
    assert client.get(f"/api/movies/{movie_id}").json()["position"] == 42.5

    # xem gần hết -> khỏi danh sách "Xem tiếp"
    client.post("/api/progress", json={"movie_id": movie_id, "position": 99, "duration": 100})
    assert client.get("/api/continue").json() == []

    assert client.post("/api/progress", json={"movie_id": 9999, "position": 1}).status_code == 404
    assert client.post("/api/progress", json={"movie_id": movie_id, "position": -1}).status_code == 422
    assert client.post("/api/progress", json={"movie_id": movie_id, "position": "NaN"}).status_code == 422


def test_rescan_keeps_progress_and_removes_deleted_files(client, media_env):
    _scan_with_fake(media_env)
    movie_id = client.get("/api/movies").json()["items"][0]["id"]
    client.post("/api/progress", json={"movie_id": movie_id, "position": 30, "duration": 100})

    result = scanner.scan_all()
    assert result.found == 1 and result.added == 0
    assert client.get(f"/api/movies/{movie_id}").json()["position"] == 30

    (media_env / "Hành động" / "Phim_Một.mp4").unlink()
    result = scanner.scan_all()
    assert result.removed == 1
    assert client.get("/api/movies").json()["total"] == 0
    assert client.get("/api/continue").json() == []  # tiến độ bị xoá theo (ON DELETE CASCADE)


def test_unavailable_media_dir_keeps_movies_and_progress(client, media_env, tmp_path, monkeypatch):
    _scan_with_fake(media_env)
    movie_id = client.get("/api/movies").json()["items"][0]["id"]
    client.post("/api/progress", json={"movie_id": movie_id, "position": 30, "duration": 100})

    # ổ cứng ngoài bị rút: thư mục mục gốc không tồn tại
    monkeypatch.setenv("MEDIA_DIRS", str(tmp_path / "rong"))
    assert scanner.scan_all().removed == 0
    assert client.get(f"/api/movies/{movie_id}").json()["position"] == 30

    # MEDIA_DIRS trỏ sang thư mục khác đang tồn tại nhưng không chứa phim cũ
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("MEDIA_DIRS", str(other))
    assert scanner.scan_all().removed == 0
    assert client.get("/api/continue").json()[0]["position"] == 30


def test_path_outside_media_dirs_is_refused(client, media_env, tmp_path):
    outside = tmp_path / "secret.mp4"
    write_fake_video(outside)
    movie_id = database.upsert_movie(
        title="evil", file_path=str(outside), thumbnail_path=None,
        duration=None, resolution=None, folder_category=None,
    )
    secret_sub = tmp_path / "secret.srt"
    secret_sub.write_text("1\n00:00:01,000 --> 00:00:02,000\nbí mật\n", encoding="utf-8")
    database.set_subtitles(movie_id, [("vi", "Tiếng Việt", str(secret_sub), True)])
    assert client.get(f"/stream/{movie_id}").status_code == 404
    assert client.get(f"/subtitles/{movie_id}").status_code == 404


def test_security_headers_and_index(client):
    res = client.get("/")
    assert res.status_code == 200
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in res.headers["content-security-policy"]


def test_scan_endpoint(client, media_env):
    write_fake_video(media_env / "a.mp4")
    assert client.post("/api/scan").status_code == 202
    for _ in range(50):
        if not client.get("/api/scan/status").json()["running"]:
            break
        time.sleep(0.1)
    assert client.get("/api/movies").json()["total"] == 1


def test_real_video_probe_and_thumbnail(client, media_env, real_video):
    target = media_env / "real.mp4"
    target.write_bytes(real_video.read_bytes())
    scanner.scan_all()
    item = client.get("/api/movies").json()["items"][0]
    assert item["resolution"] == "240p"
    assert 5 <= item["duration"] <= 7
    assert item["thumbnail_url"].startswith("/static/thumbnails/")
    assert any(config.THUMB_DIR.glob("*.jpg"))
