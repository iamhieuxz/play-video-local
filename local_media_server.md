# 🎬 Tài liệu Kiến trúc & Kế hoạch Dự án: Local Media Server (Web Xem Phim Nội Bộ)

Tài liệu này mô tả chi tiết cấu trúc dự án, sơ đồ luồng dữ liệu, bộ công cụ sử dụng cho từng phân hệ và các kỹ năng lập trình cần thiết để xây dựng hệ thống xem phim trực tuyến từ dữ liệu có sẵn trên ổ cứng (Local Storage).

---

## 1. Sơ đồ Kiến trúc Hệ thống (System Architecture)

Sơ đồ dưới đây thể hiện cách các thành phần trong hệ thống tương tác với nhau từ ổ cứng vật lý đến giao diện người dùng (trình duyệt trên PC, Điện thoại, Smart TV trong cùng mạng LAN):

```mermaid
graph TD
    subgraph "1. Tầng Lưu Trữ Nội Bộ (Local Storage)"
        HDD["Ổ cứng chứa Phim<br/>(.mp4, .mkv, .srt, .vtt)"]
        ThumbDir["Thư mục chứa Ảnh thu nhỏ<br/>(/static/thumbnails)"]
        DB[("SQLite Database<br/>(media.db)")]
    end

    subgraph "2. Tầng Xử Lý Ngầm (Scanner & Media Engine)"
        Scanner["Bộ quét ổ cứng<br/>(Python pathlib/os)"]
        FFmpeg["Công cụ FFmpeg / FFprobe<br/>(Tạo ảnh bìa, đọc thời lượng, convert âm thanh)"]
    end

    subgraph "3. Tầng Backend (Python FastAPI + Uvicorn)"
        API_Movies["API Quản lý Phim & Tiến độ<br/>(GET /api/movies, POST /api/progress)"]
        API_Stream["Bộ truyền tải Video (Chunk Streaming)<br/>(GET /stream/{id} - HTTP 206)"]
        API_Sub["Bộ chuyển đổi Phụ đề<br/>(GET /subtitles/{id} - SRT to VTT)"]
    end

    subgraph "4. Tầng Giao Diện (Frontend - LAN Clients)"
        UI["Giao diện Web (HTML5 + Tailwind CSS)"]
        Player["Trình phát Video (Plyr.js + HTML5 Video)"]
    end

    %% Luồng quét dữ liệu
    HDD -->|Đọc đường dẫn file| Scanner
    Scanner -->|Gọi lệnh CLI| FFmpeg
    FFmpeg -->|Xuất ảnh .jpg| ThumbDir
    Scanner -->|Lưu Metadata & Đường dẫn| DB

    %% Luồng phục vụ người dùng
    DB <-->|Truy vấn / Cập nhật lịch sử xem| API_Movies
    HDD -->|Đọc từng mảnh 1MB (Seek/Byte Range)| API_Stream
    HDD -->|Đọc file phụ đề| API_Sub

    API_Movies <-->|JSON Data| UI
    ThumbDir -->|Hiển thị Poster| UI
    API_Stream ==>|Luồng Video (206 Partial Content)| Player
    API_Sub -->|Hiển thị Phụ đề (.vtt)| Player
```

---

## 2. Cấu trúc Thư mục Dự án (Directory Structure)

Dự án được tổ chức theo mô hình gọn nhẹ, tách biệt rõ ràng giữa xử lý Backend, công cụ quét file và giao diện Frontend:

```text
local-movie-web/
│
├── app/
│   ├── __init__.py
│   ├── main.py              # Điểm khởi chạy FastAPI, cấu hình CORS và định tuyến (Routes)
│   ├── database.py          # Kết nối SQLite và tạo bảng (movies, watch_history)
│   ├── scanner.py           # Script quét ổ cứng, gọi FFmpeg lấy thông tin phim & tạo thumbnail
│   ├── streamer.py          # Logic cốt lõi: Đọc file theo Byte Range (HTTP 206) để tua video
│   └── subtitle.py          # Logic chuyển đổi phụ đề từ .srt sang chuẩn .vtt của web
│
├── static/                  # Chứa các tài nguyên tĩnh phục vụ trực tiếp cho trình duyệt
│   ├── thumbnails/          # Ảnh bìa phim được tạo tự động bởi FFmpeg
│   ├── css/                 # File CSS (nếu dùng offline)
│   └── js/
│       └── app.js           # Logic gọi API, tìm kiếm phim, lưu tiến độ xem mỗi 10 giây
│
├── templates/
│   └── index.html           # Giao diện chính (Tích hợp Tailwind CSS + Plyr.js Player)
│
├── media.db                 # File cơ sở dữ liệu SQLite (Tự động sinh ra khi chạy)
├── requirements.txt         # Danh sách các thư viện Python cần cài đặt
└── README.md                # Hướng dẫn chạy server
```

---

## 3. Chi tiết Công cụ cho Từng Phân hệ (Tools & Libraries)

Để không phải cài đặt rườm rà nhưng vẫn đạt hiệu năng cao nhất, dưới đây là bộ công cụ chính xác cho từng phần:

### A. Phân hệ Quét File & Xử lý Đa phương tiện (Media Processing)
*   **Thư viện `pathlib` & `os` (Có sẵn trong Python):** Dùng để duyệt đệ quy qua tất cả các thư mục con trong ổ cứng (`D:/Movies`, `E:/PhimBo`,...) nhằm tìm kiếm file video và phụ đề cùng tên.
*   **Công cụ `FFmpeg` & `FFprobe` (Cài trên hệ điều hành):**
    *   `ffprobe`: Đọc thông số kỹ thuật của file video (thời lượng dài bao nhiêu giây, độ phân giải 1080p hay 4K, chuẩn âm thanh là AAC hay AC3).
    *   `ffmpeg`: Tự động cắt 1 khung hình ở phút thứ 3 của bộ phim để làm ảnh bìa (`.jpg`) nếu phim chưa có poster; hoặc remux nhanh file `.mkv` sang `.mp4`.
*   **Thư viện `subprocess` (Python):** Dùng để gọi lệnh `ffmpeg` ngầm từ trong code Python mà không cần mở cửa sổ dòng lệnh thủ công.

### B. Phân hệ Lưu trữ Dữ liệu (Database)
*   **Hệ quản trị `SQLite3` (Có sẵn trong Python):** Lưu trữ toàn bộ danh sách phim vào một file `media.db` duy nhất.
*   **Cấu trúc bảng dữ liệu chính:**
    *   Bảng `movies`: `id`, `title`, `file_path`, `subtitle_path`, `thumbnail_path`, `duration`, `resolution`, `folder_category`.
    *   Bảng `watch_progress`: `movie_id`, `current_time` (giây đang xem dở), `updated_at`.

### C. Phân hệ Backend & Streaming Server
*   **Framework `FastAPI`:** Xây dựng RESTful API tốc độ cao.
*   **ASGI Server `Uvicorn`:** Chạy server bất đồng bộ, cho phép nhiều thiết bị trong nhà (TV, điện thoại, laptop) cùng xem phim một lúc mà không bị nghẽn.
*   **Thư viện `aiofiles`:** Hỗ trợ đọc file video từ ổ cứng theo cơ chế bất đồng bộ (Non-blocking File I/O), tránh làm đơ server khi đọc file dung lượng lớn (5GB - 20GB).

### D. Phân hệ Giao diện & Trình phát (Frontend)
*   **HTML5 & `Tailwind CSS` (qua CDN):** Dựng giao diện màu tối (Dark Mode) giống Netflix/Plex cực nhanh mà không cần viết nhiều file CSS.
*   **Thư viện `Plyr.js`:** Thay thế trình phát mặc định thô sơ của trình duyệt bằng một trình phát chuyên nghiệp (hỗ trợ phím tắt Space, mũi tên tua 10s, chọn phụ đề, chỉnh tốc độ phát $0.5\times \to 2.0\times$, Picture-in-Picture).
*   **`Fetch API` (Vanilla JavaScript):** Tìm kiếm phim tức thì không cần tải lại trang và gửi ngầm vị trí thời gian đang xem về server.

---

## 4. Các Kỹ năng (Skills) Cần Trang bị & Đề xuất Học tập

Để tự tay xây dựng và làm chủ hoàn toàn dự án này, bạn chỉ cần tập trung vào **5 kỹ năng cốt lõi** dưới đây (được sắp xếp theo thứ tự từ quan trọng nhất đến bổ trợ):

### Kỹ năng 1: Kỹ thuật HTTP Range Requests & File Seek (Quan trọng nhất)
*   **Tại sao cần?** Nếu bạn trả về toàn bộ file video 4GB qua API thông thường, trình duyệt sẽ phải tải hết 4GB mới phát được và làm tràn RAM.
*   **Cần nắm vững:**
    *   Cách đọc Header `Range: bytes=start-end` từ trình duyệt gửi lên.
    *   Cách dùng hàm `file.seek(start_byte)` và `file.read(chunk_size)` trong Python để chỉ lấy đúng đoạn dữ liệu tại vị trí người dùng vừa bấm tua.
    *   Cách trả về mã trạng thái **HTTP 206 (Partial Content)** kèm theo Header `Content-Range` và `Accept-Ranges: bytes`.

### Kỹ năng 2: Quản lý File & Thư mục bằng Python (`pathlib`)
*   **Tại sao cần?** Dữ liệu của bạn nằm rải rác trên ổ cứng với nhiều tên file, định dạng và thư mục con khác nhau.
*   **Cần nắm vững:**
    *   Sử dụng `Path.rglob("*")` để quét toàn bộ thư mục con.
    *   Tách tên file, phần mở rộng (`.mp4`, `.mkv`, `.srt`) và kiểm tra sự tồn tại của file trên ổ cứng trước khi phát.

### Kỹ năng 3: Thao tác Cơ sở dữ liệu SQLite cơ bản
*   **Tại sao cần?** Bạn không thể bắt máy tính quét lại toàn bộ ổ cứng mỗi lần F5 trang web. Quét 1 lần rồi lưu vào SQLite sẽ giúp web tải danh sách phim trong $0.01$ giây.
*   **Cần nắm vững:** Các câu lệnh SQL cơ bản (`CREATE TABLE`, `INSERT OR IGNORE`, `SELECT ... WHERE title LIKE ...`, `UPDATE`) thông qua module `sqlite3` của Python.

### Kỹ năng 4: Điều khiển HTML5 Video API & Giao tiếp Bất đồng bộ (JavaScript)
*   **Tại sao cần?** Để tạo trải nghiệm mượt mà như Netflix (bấm vào phim là phát, tự động nhớ vị trí đang xem dở).
*   **Cần nắm vững:**
    *   Lắng nghe sự kiện `timeupdate` của thẻ `<video>` để lấy số giây hiện tại (`video.currentTime`).
    *   Sử dụng `fetch()` với `async/await` để lấy danh sách phim và cập nhật tiến độ xem.

### Kỹ năng 5: Mạng Nội bộ (Local Area Network - LAN) & Dòng lệnh FFmpeg cơ bản
*   **Tại sao cần?** Giúp bạn mở rộng trang web từ máy cá nhân ra toàn bộ các thiết bị trong gia đình và xử lý các file video "kén" trình duyệt.
*   **Cần nắm vững:**
    *   Cách tìm địa chỉ IPv4 nội bộ của máy tính (ví dụ: `192.168.1.15` bằng lệnh `ipconfig` trên Windows hoặc `ifconfig` trên Mac/Linux) và mở tường lửa (Firewall) cho cổng `8000`.
    *   Lệnh FFmpeg cơ bản để cắt ảnh thumbnail và chuyển đổi âm thanh sang chuẩn AAC.

---

## 5. Lộ trình Thực hành Đề xuất (Step-by-Step)

1.  **Bước 1 (MVP - Chạy được ngay):** Chỉ dùng **1 file Python (`main.py`)** và **1 file HTML (`index.html`)**. Chưa cần SQLite hay FFmpeg. Quét trực tiếp 1 thư mục chứa các file `.mp4` và phát thử lên trình duyệt, tua thử xem có mượt không.
2.  **Bước 2 (Nâng cấp dữ liệu):** Thêm `SQLite` và `scanner.py` để lưu danh sách phim, hỗ trợ phụ đề `.srt` (tự động đổi sang `.vtt` bằng code Python).
3.  **Bước 3 (Hoàn thiện trải nghiệm):** Tích hợp `FFmpeg` tự tạo ảnh bìa, thêm tính năng *"Xem tiếp"* (Continue Watching) và mở mạng LAN để xem trên điện thoại/TV.