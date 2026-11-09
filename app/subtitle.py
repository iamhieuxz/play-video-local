"""Phụ đề: tìm file, nhận diện ngôn ngữ, chuẩn hoá thời gian hiển thị và xuất WebVTT.

Quy ước thư mục phim (khớp với dữ liệu thực tế):
    CODE/CODE.mp4
    CODE/CODE.srt            -> tiếng Việt
    CODE/CODE_whisper.srt    -> tiếng Nhật (bản ghi âm do Whisper tạo)
Ngôn ngữ được nhận diện theo NỘI DUNG file, tên file chỉ dùng làm gợi ý phụ.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Iterator

from . import config

# ------------------------------------------------------------------------- hằng số
# Thứ tự ưu tiên khi chọn phụ đề mặc định và khi liệt kê trong menu.
LANG_ORDER = ("vi", "en", "ja", "zh")
LANG_LABELS = {"vi": "Tiếng Việt", "en": "Tiếng Anh", "ja": "Tiếng Nhật", "zh": "Tiếng Trung"}

# Phụ đề có thể nằm trong tối đa 2 cấp thư mục con bên dưới thư mục chứa video.
MAX_SUBTITLE_DEPTH = 2

# Tốc độ đọc (ký tự/giây): chữ Latin ~17, chữ Hán/Kana ~5. Dùng để đảm bảo cue hiện đủ lâu.
CPS_LATIN = 17.0
CPS_CJK = 5.0
MIN_DURATION = 1.0   # một cue không nên hiện ngắn hơn mức này (nếu còn chỗ trống)
MAX_DURATION = 6.0   # chỉ kéo dài tới mức này, không kéo dài hơn
CUE_GAP = 0.05       # khoảng hở giữa 2 cue liền kề khi kéo dài
HARD_MIN = 0.3       # cue lỗi (end <= start) ít nhất phải hiện chừng này

_TS_PART = r"(?:\d{1,3}:)?\d{1,2}:\d{2}[,.]\d{1,3}"
_TS = re.compile(r"(?:(\d{1,3}):)?(\d{1,2}):(\d{2})[,.](\d{1,3})")
_CUE_LINE = re.compile(rf"^\s*({_TS_PART})\s*-->\s*({_TS_PART})")

_ASS_TAG = re.compile(r"\{\\[^}]*\}")                 # {\an8}
_KEEP_TAG = re.compile(r"(</?[ibu]>)", re.IGNORECASE)  # chỉ cho phép <i> <b> <u>
_OTHER_TAG = re.compile(r"</?[A-Za-z][^>]*>")

_VI = re.compile("[ăâđêôơưáàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ]", re.IGNORECASE)
_KANA = re.compile("[\u3040-\u30ff]")
_HAN = re.compile("[\u4e00-\u9fff]")
_LATIN_WORD = re.compile("[A-Za-z]{3,}")
_LANG_IN_NAME = re.compile(r"(?:^|[._\- ])(vi|vn|ja|jp|en|zh)\d*(?:[._\- ]|$)")
_NAME_ALIASES = {"vn": "vi", "jp": "ja"}


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Track:
    lang: str
    label: str
    path: Path
    is_default: bool = False


def lang_sort_key(lang: str) -> tuple[int, str]:
    return (LANG_ORDER.index(lang) if lang in LANG_ORDER else len(LANG_ORDER), lang)


# --------------------------------------------------------------------------- đọc file
def decode_subtitle(raw: bytes) -> str:
    """Đoán bảng mã: UTF-16 (có BOM) -> UTF-8 (có/không BOM) -> CP1258 (tiếng Việt cũ) -> Latin-1."""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    for encoding in ("utf-8-sig", "cp1258"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def _parse_ts(value: str) -> float:
    match = _TS.fullmatch(value)
    assert match is not None  # _CUE_LINE đã kiểm tra định dạng
    hours, minutes, seconds, millis = match.groups()
    return int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds) + int(millis.ljust(3, "0")) / 1000


def _format_ts(seconds: float) -> str:
    total_ms = max(0, round(seconds * 1000))
    hours, rest = divmod(total_ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, ms = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{ms:03d}"


def parse_cues(text: str) -> list[Cue]:
    """Đọc SRT hoặc VTT thành danh sách cue. Bỏ qua số thứ tự, header, ghi chú và cue rỗng."""
    cues: list[Cue] = []
    start = end = None
    lines: list[str] = []

    def flush() -> None:
        nonlocal start, end, lines
        if start is not None and end is not None:
            cues.append(Cue(start, end, "\n".join(lines)))
        start = end = None
        lines = []

    for line in text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        match = _CUE_LINE.match(line)
        if match:
            # Thiếu dòng trống giữa 2 cue: dòng số thứ tự của cue sau bị dính vào cue trước.
            if start is not None and lines and lines[-1].strip().isdigit():
                lines.pop()
            flush()
            start, end = _parse_ts(match.group(1)), _parse_ts(match.group(2))
        elif not line.strip():
            flush()
        elif start is not None:
            lines.append(line)
    flush()
    return [c for c in cues if c.text.strip()]


# ----------------------------------------------------------------------- chuẩn hoá
def clean_text(raw: str) -> str:
    """Làm sạch nội dung cue: bỏ thẻ ASS/HTML lạ, giữ <i><b><u>, escape ký tự đặc biệt của WebVTT."""
    raw = _ASS_TAG.sub("", raw)
    pieces: list[str] = []
    for part in _KEEP_TAG.split(raw):
        if _KEEP_TAG.fullmatch(part):
            pieces.append(part.lower())
        else:
            pieces.append(escape(_OTHER_TAG.sub("", part), quote=False))
    lines = (line.strip() for line in "".join(pieces).split("\n"))
    return "\n".join(line for line in lines if line)


def reading_time(text: str) -> float:
    """Thời gian tối thiểu để người xem đọc hết cue."""
    plain = re.sub(r"<[^>]+>|&\w+;|\s", "", text)
    if not plain:
        return MIN_DURATION
    cjk = len(_KANA.findall(plain)) + len(_HAN.findall(plain))
    cps = CPS_CJK if cjk / len(plain) > 0.5 else CPS_LATIN
    return min(max(len(plain) / cps, MIN_DURATION), MAX_DURATION)


def normalize_cues(cues: list[Cue]) -> list[Cue]:
    """Sắp xếp theo thời gian, làm sạch chữ và sửa thời gian để phụ đề hiện tự nhiên.

    - Cue hiện quá ngắn (chớp tắt) được kéo dài theo tốc độ đọc, nhưng không bao giờ chạm cue kế tiếp.
    - Cue lỗi (end <= start) được sửa thành khoảng hợp lệ.
    - Không bao giờ rút ngắn cue gốc.
    """
    cleaned = [Cue(c.start, c.end, clean_text(c.text)) for c in cues]
    cleaned = sorted((c for c in cleaned if c.text), key=lambda c: (c.start, c.end))

    result: list[Cue] = []
    for index, cue in enumerate(cleaned):
        end = cue.end
        wanted = cue.start + reading_time(cue.text)
        if end < wanted:
            following = cleaned[index + 1].start if index + 1 < len(cleaned) else None
            limit = wanted if following is None else following - CUE_GAP
            end = max(end, min(wanted, limit))
        if end <= cue.start:
            end = cue.start + HARD_MIN
        result.append(Cue(cue.start, end, cue.text))
    return result


def to_vtt(cues: list[Cue]) -> str:
    out = ["WEBVTT", ""]
    for cue in cues:
        out.append(f"{_format_ts(cue.start)} --> {_format_ts(cue.end)}")
        out.append(cue.text)
        out.append("")
    return "\n".join(out)


def srt_to_vtt(text: str) -> str:
    """Nội dung SRT (hoặc VTT) -> WebVTT đã chuẩn hoá."""
    return to_vtt(normalize_cues(parse_cues(text)))


def load_as_vtt(path: Path) -> str:
    """Đọc file phụ đề (có giới hạn kích thước) và trả về WebVTT."""
    if path.stat().st_size > config.MAX_SUBTITLE_BYTES:
        raise ValueError("Subtitle file is too large")
    return srt_to_vtt(decode_subtitle(path.read_bytes()))


# ------------------------------------------------------------------- nhận diện ngôn ngữ
def detect_language(cues: list[Cue]) -> str:
    """Nhận diện theo tỉ lệ cue có ký tự đặc trưng. Trả 'und' nếu không chắc."""
    total = len(cues)
    if total == 0:
        return "und"
    vi = sum(1 for c in cues if _VI.search(c.text))
    kana = sum(1 for c in cues if _KANA.search(c.text))
    han = sum(1 for c in cues if _HAN.search(c.text))
    latin = sum(1 for c in cues if _LATIN_WORD.search(c.text))
    # Phụ đề Việt dịch dở dang vẫn lẫn nhiều cue tiếng Nhật, nên ngưỡng "vi" đặt thấp.
    if vi / total >= 0.15:
        return "vi"
    if kana / total >= 0.2:
        return "ja"
    if han / total >= 0.5:
        return "zh"
    if latin / total >= 0.5:
        return "en"
    return "und"


def _lang_from_name(name: str) -> str | None:
    match = _LANG_IN_NAME.search(name)
    return _NAME_ALIASES.get(match.group(1), match.group(1)) if match else None


# ------------------------------------------------------------------------ tìm phụ đề
def _iter_files(folder: Path, recursive: bool) -> Iterator[Path]:
    if not recursive:
        with os.scandir(folder) as entries:
            for entry in entries:
                if entry.is_file():
                    yield Path(entry.path)
        return
    base_depth = len(folder.parts)
    for dirpath, dirnames, filenames in os.walk(folder):
        if len(Path(dirpath).parts) - base_depth >= MAX_SUBTITLE_DEPTH:
            dirnames[:] = []
        else:
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            yield Path(dirpath) / name


def _belongs_to(video_stem: str, file_name: str) -> bool:
    """File phụ đề thuộc về video nếu tên bắt đầu bằng tên video và ngay sau đó không phải chữ/số.

    Nhờ vậy `JUR-58.mp4` không nhận nhầm `JUR-581.srt`, nhưng vẫn nhận `JUR-58_whisper.srt`.
    """
    if not file_name.startswith(video_stem):
        return False
    return not file_name[len(video_stem) : len(video_stem) + 1].isalnum()


def discover(video: Path, root: Path) -> list[Track]:
    """Tìm phụ đề của một video và chọn MỖI NGÔN NGỮ MỘT FILE tốt nhất.

    Thứ tự ưu tiên khi một ngôn ngữ có nhiều file: tên trùng hẳn tên video > cùng thư mục với video > mới sửa gần nhất.
    File rỗng, quá lớn hoặc không có cue nào bị bỏ qua. Video nằm thẳng trong thư mục gốc chỉ tìm cùng thư mục.
    """
    stem = video.stem.lower()
    best: dict[str, tuple[tuple[int, int, float], Path]] = {}
    try:
        files = list(_iter_files(video.parent, recursive=video.parent != root))
    except OSError:
        return []

    for path in files:
        name = path.name.lower()
        if path.suffix.lower() not in config.SUBTITLE_EXTS or not _belongs_to(stem, name):
            continue
        try:
            stat = path.stat()
            if stat.st_size == 0 or stat.st_size > config.MAX_SUBTITLE_BYTES:
                continue
            cues = parse_cues(decode_subtitle(path.read_bytes()))
        except OSError:
            continue
        if not cues:
            continue
        lang = detect_language(cues)
        if lang == "und":
            lang = _lang_from_name(name[len(stem) :]) or "und"
        rank = (0 if path.stem.lower() == stem else 1, 0 if path.parent == video.parent else 1, -stat.st_mtime)
        if lang not in best or rank < best[lang][0]:
            best[lang] = (rank, path)

    langs = sorted(best, key=lang_sort_key)
    return [
        Track(lang=lang, label=LANG_LABELS.get(lang, "Phụ đề"), path=best[lang][1], is_default=index == 0)
        for index, lang in enumerate(langs)
    ]
