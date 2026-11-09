"""Mô hình dữ liệu (Pydantic) cho request/response của API."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SubtitleOut(BaseModel):
    lang: str
    label: str
    url: str
    default: bool = False


class MovieOut(BaseModel):
    id: int
    title: str
    thumbnail_url: str | None = None
    duration: float | None = None
    resolution: str | None = None
    folder_category: str | None = None
    has_subtitle: bool = False
    position: float = 0
    subtitles: list[SubtitleOut] = []  # chỉ có trong GET /api/movies/{id}

    @classmethod
    def from_row(cls, row: dict[str, Any], subtitles: list[dict[str, Any]] | None = None) -> "MovieOut":
        # Cố ý không trả file_path: client chỉ cần biết id.
        thumb = row.get("thumbnail_path")
        return cls(
            id=row["id"],
            title=row["title"],
            thumbnail_url=f"/static/{thumb}" if thumb else None,
            duration=row.get("duration"),
            resolution=row.get("resolution"),
            folder_category=row.get("folder_category"),
            has_subtitle=bool(row.get("subtitle_count")),
            position=row.get("position") or 0,
            subtitles=[
                SubtitleOut(
                    lang=s["lang"],
                    label=s["label"],
                    url=f"/subtitles/{row['id']}/{s['lang']}",
                    default=bool(s["is_default"]),
                )
                for s in subtitles or []
            ],
        )


class MovieList(BaseModel):
    items: list[MovieOut]
    total: int


class ProgressIn(BaseModel):
    movie_id: int = Field(ge=1)
    position: float = Field(ge=0, le=10_000_000, allow_inf_nan=False)
    duration: float | None = Field(default=None, gt=0, le=10_000_000, allow_inf_nan=False)


class ScanStatus(BaseModel):
    running: bool
    started: bool | None = None
