#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Общие функции для all_good: поиск файлов, ffmpeg, статусы, логирование."""

from __future__ import annotations

import logging
import os
import subprocess
import time
from datetime import timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

from all_good_config import (
    LOG_BACKUP_COUNT,
    LOG_FORMAT,
    LOG_MAX_BYTES,
    OUTPUT_SUFFIX_TEMPLATE,
    VIDEO_EXTENSIONS,
)
from all_good_dto import ProcessingStatus, SegmentTranscript

_START_TIME = time.monotonic()


class _ElapsedFormatter(logging.Formatter):
    """Добавляет в запись лога время от старта скрипта в формате +HH:MM:SS.sss."""

    def format(self, record: logging.LogRecord) -> str:
        elapsed = timedelta(seconds=time.monotonic() - _START_TIME)
        total_ms = int(elapsed.total_seconds() * 1000)
        h, rem_ms = divmod(total_ms, 3_600_000)
        m, rem_ms = divmod(rem_ms, 60_000)
        s, ms = divmod(rem_ms, 1000)
        record.elapsed = f"+{h:02d}:{m:02d}:{s:02d}.{ms:03d}"
        return super().format(record)


def setup_logging(script_name: str, level: str) -> logging.Logger:
    """Настраивает логирование в консоль и в logs/<script_name>.log с ротацией."""
    logs_dir = Path(__file__).parent / "logs"
    logs_dir.mkdir(exist_ok=True)

    formatter = _ElapsedFormatter(LOG_FORMAT)

    file_handler = RotatingFileHandler(
        logs_dir / f"{script_name}.log",
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    root_logger.handlers.clear()
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    return logging.getLogger(script_name)


def seconds_to_timestamp(seconds: float) -> str:
    """Конвертирует секунды в HH:MM:SS.mmm."""
    total_ms = int(round(seconds * 1000))
    h, rem_ms = divmod(total_ms, 3_600_000)
    m, rem_ms = divmod(rem_ms, 60_000)
    s, ms = divmod(rem_ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def is_video_file(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTENSIONS


def collect_video_files(inputs: list[str], recursive: bool) -> list[Path]:
    """Собирает список видеофайлов из переданных путей (файлы или папки)."""
    result: list[Path] = []
    for raw in inputs:
        path = Path(raw).expanduser().resolve()
        if path.is_dir():
            for ext in VIDEO_EXTENSIONS:
                pattern = f"**/*{ext}" if recursive else f"*{ext}"
                result.extend(path.glob(pattern))
        elif path.is_file() and is_video_file(path):
            result.append(path)
    return sorted(set(result))


def get_output_path(video_path: Path, revision: str) -> Path:
    return video_path.with_name(video_path.stem + OUTPUT_SUFFIX_TEMPLATE.format(revision=revision))


def get_processing_status(video_path: Path, revision: str) -> ProcessingStatus:
    """Статус по наличию/размеру файла результата рядом с видео (без отдельной БД)."""
    output_path = get_output_path(video_path, revision)
    if not output_path.exists():
        return ProcessingStatus.NOT_ATTEMPTED
    try:
        return ProcessingStatus.SUCCESS if output_path.stat().st_size > 0 else ProcessingStatus.FAILED
    except OSError:
        return ProcessingStatus.NOT_ATTEMPTED


def extract_audio_from_video(video_path: Path, output_dir: Path, sample_rate: int) -> Path:
    """Извлекает полную звуковую дорожку видео в WAV (mono, PCM16)."""
    audio_path = output_dir / f"{video_path.stem}.wav"
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", str(sample_rate),
        "-ac", "1",
        str(audio_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg не смог извлечь звук из {video_path}: {result.stderr[-2000:]}")
    return audio_path


def cleanup_temp_file(path: Path) -> None:
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass


def format_segment_line(seg: SegmentTranscript) -> str:
    return f"[{seconds_to_timestamp(seg.start_sec)} - {seconds_to_timestamp(seg.end_sec)}] {seg.text}"


def get_partial_output_path(video_path: Path, revision: str) -> Path:
    """Путь к файлу результата, пока серия ещё обрабатывается (не считается готовым)."""
    output_path = get_output_path(video_path, revision)
    return output_path.with_name(output_path.name + ".partial")


def finalize_segments_file(partial_path: Path, video_path: Path, revision: str) -> Path:
    """Переименовывает готовый .partial в финальный файл, время модификации — как у исходника."""
    output_path = get_output_path(video_path, revision)
    os.replace(partial_path, output_path)
    try:
        original_mtime = video_path.stat().st_mtime
        os.utime(output_path, (original_mtime, original_mtime))
    except OSError:
        pass
    return output_path
