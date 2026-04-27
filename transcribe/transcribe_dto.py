#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Data Transfer Objects (DTO) для системы транскрибации.

Все Pydantic модели, используемые в скриптах транскрибации.
"""

from pathlib import Path
from typing import List
from pydantic import BaseModel, Field


# ============================================================================
# Аудио обработка
# ============================================================================

class ChunkInfo(BaseModel):
    """Информация об аудио-чанке"""
    start_sec: float = Field(description="Начало чанка в секундах")
    file_path: Path = Field(description="Путь к файлу чанка")


class ChunkBoundary(BaseModel):
    """Границы чанка"""
    start_sec: float = Field(description="Начало в секундах")
    end_sec: float = Field(description="Конец в секундах")


class AudioChunkingResult(BaseModel):
    """Результат нарезки аудио на чанки"""
    chunks: List[ChunkInfo] = Field(description="Список чанков")
    total_duration_sec: float = Field(description="Общая длительность аудио в секундах")


# ============================================================================
# Транскрипция и временные метки
# ============================================================================

class SentenceWithTimestamp(BaseModel):
    """Предложение с временными метками"""
    text: str = Field(description="Текст предложения")
    start: float = Field(description="Начало в секундах")
    end: float = Field(description="Конец в секундах")
    speaker: str = Field(default="UNKNOWN", description="Спикер")


class TextBlock(BaseModel):
    """Блок текста с временной меткой и спикером"""
    start_sec: float = Field(description="Начало блока в секундах")
    speaker: str = Field(description="Идентификатор спикера")
    text: str = Field(description="Текст блока")


# ============================================================================
# Диаризация спикеров
# ============================================================================

class SpeakerSegment(BaseModel):
    """Сегмент одного спикера"""
    start: float = Field(description="Начало сегмента в секундах")
    end: float = Field(description="Конец сегмента в секундах")
    speaker: str = Field(description="Идентификатор спикера")
    duration: float = Field(description="Длительность сегмента в секундах")

