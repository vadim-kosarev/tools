#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pydantic-модели для all_good."""

from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field


class ProcessingStatus(str, Enum):
    """Статус обработки видеофайла."""

    NOT_ATTEMPTED = "not_attempted"
    FAILED = "failed"
    SUCCESS = "success"


class SegmentTranscript(BaseModel):
    """Один сегмент распознанной речи с границами по VAD."""

    start_sec: float = Field(description="Начало сегмента в секундах")
    end_sec: float = Field(description="Конец сегмента в секундах")
    text: str = Field(description="Распознанный текст сегмента")


class VideoTranscriptResult(BaseModel):
    """Результат распознавания одного видеофайла."""

    video_path: Path = Field(description="Путь к исходному видеофайлу")
    segments: list[SegmentTranscript] = Field(default_factory=list, description="Сегменты речи")


class ScanReport(BaseModel):
    """Итоговая статистика прохода по папкам."""

    total_found: int = 0
    processed: int = 0
    skipped_success: int = 0
    failed: int = 0
