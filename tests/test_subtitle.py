from __future__ import annotations

import os
import time

import pytest

from app.subtitle import (
    Cue,
    decode_subtitle,
    detect_language,
    discover,
    normalize_cues,
    parse_cues,
    srt_to_vtt,
)

SRT = (
    "1\r\n00:00:01,500 --> 00:00:03,000\r\nXin chào {\\an8}thế giới\r\n\r\n"
    "2\r\n0:00:04,5 --> 0:00:05,25\r\nTạm biệt\r\n"
)


def test_srt_to_vtt_converts_header_and_timestamps():
    vtt = srt_to_vtt(SRT)
    assert vtt.startswith("WEBVTT\n")
    assert "00:00:01.500 --> 00:00:03.000" in vtt
    assert "00:00:04.500 --> 00:00:05.500" in vtt  # cue ngắn 0.75s được kéo dài đủ để đọc
    assert "{\\an8}" not in vtt and "\r" not in vtt


def test_vtt_input_is_accepted_and_normalized():
    out = srt_to_vtt("WEBVTT\n\n00:00:01.000 --> 00:00:04.000\nHi\n")
    assert out.startswith("WEBVTT") and "00:00:01.000 --> 00:00:04.000" in out


def test_decode_handles_bom_and_utf16():
    assert decode_subtitle("\ufeffXin chào".encode("utf-8")) == "Xin chào"
    assert decode_subtitle("Xin chào".encode("utf-16")) == "Xin chào"


def test_parse_handles_missing_blank_line_and_empty_cues():
    text = "1\n00:00:01,000 --> 00:00:02,000\nA\n2\n00:00:03,000 --> 00:00:04,000\nB\n\n3\n00:00:05,000 --> 00:00:06,000\n\n"
    assert [c.text for c in parse_cues(text)] == ["A", "B"]


def test_text_is_sanitized_for_webvtt():
    cues = normalize_cues([Cue(0, 3, "<font color=red>a & b < c</font>\n<i>nghiêng</i>\n\n")])
    assert cues[0].text == "a &amp; b &lt; c\n<i>nghiêng</i>"


def test_short_cue_is_extended_but_never_overlaps_next():
    cues = normalize_cues([Cue(10.0, 10.1, "Đây là một câu khá dài cần thời gian đọc"), Cue(11.0, 13.0, "Tiếp")])
    assert cues[0].end > 10.9 and cues[0].end <= 11.0 - 0.04
    assert cues[1] == Cue(11.0, 13.0, "Tiếp")


def test_original_duration_is_never_shortened():
    assert normalize_cues([Cue(0, 20, "Hi")])[0].end == 20


def test_invalid_cue_end_before_start_is_repaired_and_order_fixed():
    cues = normalize_cues([Cue(5, 4, "B"), Cue(1, 2, "A")])
    assert [c.text for c in cues] == ["A", "B"]
    assert cues[1].end > cues[1].start


def test_detect_language():
    assert detect_language([Cue(0, 1, "Tớ đã sinh ra trong thế giới")]) == "vi"
    assert detect_language([Cue(0, 1, "私は幼少期から競争世界に生きてきた")]) == "ja"
    assert detect_language([Cue(0, 1, "Hello there, how are you doing")]) == "en"
    assert detect_language([]) == "und"


def _write(path, text="1\n00:00:01,000 --> 00:00:03,000\n" + "Xin chào\n", mtime=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if mtime:
        os.utime(path, (mtime, mtime))


JA = "1\n00:00:01,000 --> 00:00:03,000\n私は幼少期から競争世界に生きてきた\n"


def test_discover_splits_languages_by_content_and_prefers_vietnamese(tmp_path):
    folder = tmp_path / "ABC-123"
    video = folder / "ABC-123.mp4"
    video.parent.mkdir()
    video.write_bytes(b"x")
    _write(folder / "ABC-123.srt")
    _write(folder / "ABC-123_whisper.srt", JA)
    _write(folder / "ABC-123_whisper.srt.timestamp_review.txt", "ghi chú")
    _write(folder / "ABC-123_empty.srt", "")

    tracks = discover(video, tmp_path)
    assert [(t.lang, t.is_default) for t in tracks] == [("vi", True), ("ja", False)]
    assert tracks[0].path.name == "ABC-123.srt" and tracks[1].path.name == "ABC-123_whisper.srt"


def test_discover_prefers_exact_name_then_same_folder_then_newest(tmp_path):
    folder = tmp_path / "X-1"
    video = folder / "X-1.mp4"
    video.parent.mkdir()
    video.write_bytes(b"x")
    now = time.time()
    _write(folder / "X-1-vn.srt", mtime=now - 100)
    _write(folder / "X-1-vn2.srt", mtime=now)             # mới nhất trong số tên không khớp hẳn
    _write(folder / "X-1" / "X-1_whisper.srt", JA)        # bản sao lồng trong thư mục con
    _write(folder / "X-1_whisper.srt", JA)

    by_lang = {t.lang: t.path for t in discover(video, tmp_path)}
    assert by_lang["vi"].name == "X-1-vn2.srt"
    assert by_lang["ja"] == folder / "X-1_whisper.srt"    # cùng thư mục với video thắng bản lồng

    _write(folder / "X-1.srt", mtime=now - 500)
    assert {t.lang: t.path for t in discover(video, tmp_path)}["vi"].name == "X-1.srt"  # tên trùng hẳn thắng


def test_discover_finds_subtitles_in_nested_folder_and_ignores_other_movies(tmp_path):
    folder = tmp_path / "JUX-1"
    video = folder / "JUX-1" / "JUX-1.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"x")
    _write(video.with_suffix(".srt"))
    _write(tmp_path / "JUX-1" / "JUX-10.srt")  # phim khác

    tracks = discover(video, tmp_path)
    assert [t.path.name for t in tracks] == ["JUX-1.srt"]


def test_discover_does_not_mix_up_similar_names(tmp_path):
    video = tmp_path / "JUR-58.mp4"
    video.write_bytes(b"x")
    _write(tmp_path / "JUR-581.srt")
    _write(tmp_path / "JUR-58_whisper.srt", JA)
    assert [t.lang for t in discover(video, tmp_path)] == ["ja"]


def test_discover_ignores_empty_and_unreadable(tmp_path):
    folder = tmp_path / "E-1"
    video = folder / "E-1.mp4"
    video.parent.mkdir()
    video.write_bytes(b"x")
    _write(folder / "E-1.srt", "")
    _write(folder / "E-1_x.srt", "không có dòng thời gian nào")
    assert discover(video, tmp_path) == []


@pytest.mark.parametrize("lang_name,expected", [("E-2-en.srt", "en"), ("E-2.vn.srt", "vi")])
def test_language_falls_back_to_file_name_when_content_is_unclear(tmp_path, lang_name, expected):
    video = tmp_path / "E-2.mp4"
    video.write_bytes(b"x")
    _write(tmp_path / lang_name, "1\n00:00:01,000 --> 00:00:02,000\n123 456\n")
    assert [t.lang for t in discover(video, tmp_path)] == [expected]
