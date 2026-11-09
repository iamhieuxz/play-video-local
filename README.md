# 🎬 Local Media Server

Web xem phim nội bộ từ ổ cứng, chạy trên mạng LAN (PC, điện thoại, Smart TV). Giao diện tối kiểu Netflix/Plex, hỗ trợ streaming video chunked (HTTP 206), phụ đề SRT/VTT, lưu tiến độ xem, và tối ưu trải nghiệm mobile với tap-to-seek và xoay video.

Kiến trúc chi tiết: [`local_media_server.md`](local_media_server.md)

## ✨ Tính năng

### Phát video
- **Streaming chunked** (HTTP 206 Partial Content) — tua video tức thì mà không cần tải toàn bộ file
- Hỗ trợ `.mp4`, `.mkv`, `.webm`, `.mov`, `.avi`, `.m4v`
- Phụ đề `.srt` tự động chuyển sang `.vtt`, hỗ trợ nhiều ngôn ngữ
- Trình phát Plyr.js chuyên nghiệp: tốc độ phát 0.5×–2×, Picture-in-Picture, AirPlay, fullscreen

### Giao diện
- **Dark mode** cinema-style với Tailwind CSS
- Grid phim responsive (2–5 cột tùy màn hình)
- Rail "Xem tiếp" scroll ngang với thanh tiến độ
- Tìm kiếm không phân biệt dấu tiếng Việt
- Ảnh bìa tự động tạo bằng FFmpeg (phút thứ 3 của phim)

### Mobile & Touch
- **Tap để tua nhanh**: tap bên trái video → lùi 10s, tap bên phải → tiến 10s
- **Cộng dồn**: tap liên tiếp cùng bên hiện 10s → 20s → 30s... (reset sau 1.2s)
- **Overlay + ripple effect** khi tap để phản hồi trực quan
- **Nút xoay video** (90°/180°/270°) — hữu ích khi xem phim dọc trên điện thoại
- Tối ưu responsive cho mọi kích thước: iPhone SE (≤374px), phone, tablet, desktop
- Landscape mode: header tự ẩn để tối đa không gian video
- Không văng khỏi fullscreen/popup khi tap

### Lưu tiến độ
- Tự động lưu vị trí xem mỗi 10 giây
- Lưu khi pause, đóng tab, chuyển app
- "Xem tiếp" hiển thị danh sách phim đang xem dở với thanh tiến độ

### Bảo mật
- Content Security Policy (CSP) chặn script/style ngoài CDN đã ghim
- Security headers: X-Content-Type-Options, X-Frame-Options, Referrer-Policy
- Path traversal protection: chỉ phục vụ file nằm trong `MEDIA_DIRS`
- Không đăng nhập — thiết kế cho mạng LAN nội bộ

## Yêu cầu

- Python 3.10+
- FFmpeg + FFprobe trong `PATH` (không bắt buộc, nhưng cần để tạo ảnh bìa và đọc thời lượng/độ phân giải)
- Trình duyệt cần Internet lần đầu để tải Tailwind CSS và Plyr.js từ CDN (phiên bản đã ghim, Plyr có kiểm tra SRI)

## Cài đặt

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
```

## Chạy

```powershell
# Trỏ tới thư mục phim (nhiều thư mục ngăn cách bằng ';' trên Windows, ':' trên Linux/Mac)
$env:MEDIA_DIRS = "D:/Movies;E:/PhimBo"
.\.venv\Scripts\python run.py
```

Nếu không đặt `MEDIA_DIRS`, server dùng thư mục `media/` cạnh dự án. Khi khởi động, server tự quét ổ cứng ở nền; có thể bấm "Quét lại ổ cứng" trên giao diện bất kỳ lúc nào.

`run.py` in ra hai địa chỉ: một cho máy này và một cho mạng LAN (ví dụ `http://192.168.1.15:8000`). Mở địa chỉ LAN trên điện thoại/TV.

## Biến môi trường

| Biến | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `MEDIA_DIRS` | `./media` | Các thư mục chứa phim |
| `MEDIA_DB` | `./media.db` | File SQLite |
| `AUTO_SCAN` | `1` | `0` để không tự quét khi khởi động |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | Địa chỉ lắng nghe (`HOST=127.0.0.1` để chỉ dùng trên máy này) |
| `CORS_ORIGINS` | rỗng | Danh sách origin được phép, ngăn cách bằng dấu phẩy |

## Cấu trúc dự án

```
local-media-server/
├── app/
│   ├── main.py          # FastAPI entry point, routes, middleware
│   ├── config.py        # Cấu hình trung tâm, đọc .env
│   ├── database.py      # SQLite connection & queries
│   ├── scanner.py       # Quét ổ cứng, gọi FFmpeg tạo thumbnail
│   ├── streamer.py      # HTTP 206 Range Requests streaming
│   ├── subtitle.py      # SRT → VTT conversion
│   └── schemas.py       # Pydantic response models
├── static/
│   ├── css/app.css      # Giao diện cinema-dark + responsive
│   ├── js/app.js        # Frontend logic, Plyr, tap-to-seek
│   └── thumbnails/      # Ảnh bìa phim (auto-generated)
├── templates/
│   └── index.html       # Single-page HTML (Tailwind + Plyr)
├── run.py               # Entry point with LAN IP detection
├── requirements.txt     # fastapi, uvicorn, aiofiles
└── local_media_server.md # Kiến trúc chi tiết
```

## API

| Endpoint | Mô tả |
| --- | --- |
| `GET /api/movies?q=&limit=&offset=` | Danh sách phim, tìm không phân biệt dấu |
| `GET /api/movies/{id}` | Thông tin một phim kèm phụ đề |
| `GET /api/continue` | Phim đang xem dở |
| `POST /api/progress` | `{movie_id, position, duration?}` lưu vị trí xem |
| `POST /api/scan`, `GET /api/scan/status` | Quét lại ổ cứng |
| `GET`/`HEAD /stream/{id}` | Video streaming, hỗ trợ `Range` (206/416) |
| `GET /subtitles/{id}[/{lang}]` | Phụ đề dạng WebVTT |

## Mở tường lửa cho LAN (Windows)

```powershell
# Chạy PowerShell quyền Admin
New-NetFirewallRule -DisplayName "Local Media Server" -Direction Inbound -Protocol TCP -LocalPort 8000 -Profile Private -Action Allow
```

Giới hạn ở profile `Private` để cổng không mở trên mạng công cộng.

## Định dạng video

- `.mp4` (H.264/AAC) phát tốt nhất trên mọi thiết bị
- `.mkv`, `.avi` tuỳ trình duyệt/TV; nếu không phát được, chuyển bằng FFmpeg:
  ```bash
  ffmpeg -i phim.mkv -c copy phim.mp4
  # Hoặc nếu âm thanh là AC3/DTS:
  ffmpeg -i phim.mkv -c:v copy -c:a aac phim.mp4
  ```
- Phụ đề: đặt file `.srt`/`.vtt` cùng tên với phim (`phim.mp4` + `phim.srt`, hoặc `phim.vi.srt`)

## Test

```powershell
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python -m pytest
```

## License

MIT
