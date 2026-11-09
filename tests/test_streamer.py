import pytest

from app.streamer import RangeNotSatisfiable, parse_range


@pytest.mark.parametrize(
    "header,size,expected",
    [
        (None, 1000, None),
        ("", 1000, None),
        ("bytes=0-99", 1000, (0, 99)),
        ("bytes=500-", 1000, (500, 999)),
        ("bytes=-100", 1000, (900, 999)),
        ("bytes=-5000", 1000, (0, 999)),          # suffix lớn hơn file -> cả file
        ("bytes=900-5000", 1000, (900, 999)),     # end bị cắt về cuối file
        ("bytes=0-0", 1000, (0, 0)),
        ("bytes=0-1,5-9", 1000, None),            # nhiều khoảng: bỏ qua header
        ("bytes=9-5", 1000, None),                # sai thứ tự: bỏ qua header
        ("items=0-5", 1000, None),
        ("bytes=abc", 1000, None),
        ("bytes=-", 1000, None),
    ],
)
def test_parse_range(header, size, expected):
    assert parse_range(header, size) == expected


@pytest.mark.parametrize("header,size", [("bytes=1000-", 1000), ("bytes=5000-6000", 1000), ("bytes=-0", 1000), ("bytes=-5", 0)])
def test_parse_range_unsatisfiable(header, size):
    with pytest.raises(RangeNotSatisfiable):
        parse_range(header, size)
