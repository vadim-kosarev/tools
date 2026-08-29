#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Общие функции для all_good: поиск файлов, ffmpeg, статусы, логирование."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile
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


_SEGMENT_LINE_RE = re.compile(
    r"^\[(\d{2}):(\d{2}):(\d{2})\.(\d{3}) - (\d{2}):(\d{2}):(\d{2})\.(\d{3})\]\s*(.*)$"
)


def _hhmmssms_to_seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def collect_segments_files(inputs: list[str], recursive: bool) -> list[Path]:
    """Собирает список файлов *.gigaam-*.segments.txt (результаты прохода 1)."""
    result: list[Path] = []
    for raw in inputs:
        path = Path(raw).expanduser().resolve()
        if path.is_dir():
            pattern = "**/*.gigaam-*.segments.txt" if recursive else "*.gigaam-*.segments.txt"
            result.extend(path.glob(pattern))
        elif path.is_file() and path.name.endswith(".segments.txt"):
            result.append(path)
    return sorted(set(result))


def parse_segments_file(path: Path) -> list[SegmentTranscript]:
    """Разбирает файл с сегментами речи в список SegmentTranscript."""
    segments = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _SEGMENT_LINE_RE.match(line.strip())
        if not match:
            continue
        h1, m1, s1, ms1, h2, m2, s2, ms2, text = match.groups()
        segments.append(SegmentTranscript(
            start_sec=_hhmmssms_to_seconds(h1, m1, s1, ms1),
            end_sec=_hhmmssms_to_seconds(h2, m2, s2, ms2),
            text=text,
        ))
    return segments


def find_source_video(segments_path: Path) -> Path | None:
    """Находит исходный видеофайл рядом с файлом сегментов по общему имени-основе."""
    idx = segments_path.name.find(".gigaam-")
    if idx == -1:
        return None
    stem = segments_path.name[:idx]
    for ext in VIDEO_EXTENSIONS:
        candidate = segments_path.with_name(stem + ext)
        if candidate.exists():
            return candidate
    return None


def normalize_for_match(text: str) -> str:
    """Нормализует текст для поиска фразы: нижний регистр, без пунктуации, один пробел."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def estimate_phrase_start_fraction(text: str, phrase: str) -> float:
    """Оценивает относительную позицию начала фразы внутри текста сегмента (0..1) по числу символов."""
    norm_text = normalize_for_match(text)
    norm_phrase = normalize_for_match(phrase)
    if not norm_text:
        return 0.0
    idx = norm_text.find(norm_phrase)
    if idx == -1:
        return 0.0
    return idx / len(norm_text)


def is_edge_match(text: str, phrase: str) -> bool:
    """True, если фраза стоит в начале или в конце текста сегмента (не в середине)."""
    norm_text = normalize_for_match(text)
    norm_phrase = normalize_for_match(phrase)
    return norm_text.startswith(norm_phrase) or norm_text.endswith(norm_phrase)


def safe_filename(phrase: str) -> str:
    """Превращает фразу в безопасное имя файла/папки."""
    normalized = normalize_for_match(phrase).replace(" ", "_")
    return normalized or "phrase"


_SILENCE_START_RE = re.compile(r"silence_start:\s*([\d.]+)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*([\d.]+)\s*\|\s*silence_duration:\s*([\d.]+)")


def trim_leading_silence(
        video_path: Path, start_sec: float, max_probe_sec: float,
        noise_db: float, min_silence_sec: float) -> float:
    """Сдвигает start_sec вперёд, если сразу за ним идёт тишина: VAD-сегмент иногда захватывает
    соседний беззвучный/невнятный спан, для которого ASR не выдал текста. Если тишины нет —
    возвращает start_sec без изменений."""
    if max_probe_sec <= 0:
        return start_sec
    tmp_dir = Path(tempfile.mkdtemp(prefix="all_good_probe_"))
    probe_path = tmp_dir / "probe.wav"
    try:
        cmd = [
            "ffmpeg", "-y", "-ss", f"{start_sec:.3f}", "-i", str(video_path),
            "-t", f"{max_probe_sec:.3f}", "-vn", "-ac", "1", "-ar", "16000",
            "-acodec", "pcm_s16le", str(probe_path),
        ]
        subprocess.run(cmd, capture_output=True)
        if not probe_path.exists() or probe_path.stat().st_size == 0:
            return start_sec

        result = subprocess.run(
            ["ffmpeg", "-i", str(probe_path), "-af", f"silencedetect=noise={noise_db}dB:d=0.1", "-f", "null", "-"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        start_match = _SILENCE_START_RE.search(result.stderr)
        if not start_match or float(start_match.group(1)) > 0.05:
            return start_sec
        end_match = _SILENCE_END_RE.search(result.stderr)
        if not end_match:
            return start_sec
        silence_duration = float(end_match.group(2))
        if silence_duration < min_silence_sec:
            return start_sec
        return start_sec + silence_duration
    finally:
        cleanup_temp_file(probe_path)
        try:
            tmp_dir.rmdir()
        except OSError:
            pass


_SILENCE_START_ALL_RE = re.compile(r"silence_start:\s*([\d.]+)")
_SILENCE_END_ALL_RE = re.compile(r"silence_end:\s*([\d.]+)")


def detect_speech_spans(
        video_path: Path, probe_start_sec: float, probe_duration_sec: float,
        noise_db: float, min_silence_sec: float) -> list[tuple[float, float]]:
    """Находит границы речевых кусков (обособленных тишиной) внутри окна [probe_start_sec,
    probe_start_sec + probe_duration_sec). Возвращает список (start_sec, end_sec) в абсолютном
    времени видео — это разбиение окна на речь, полученное обращением тишины из silencedetect."""
    if probe_duration_sec <= 0:
        return []
    tmp_dir = Path(tempfile.mkdtemp(prefix="all_good_probe_"))
    probe_path = tmp_dir / "probe.wav"
    try:
        cmd = [
            "ffmpeg", "-y", "-ss", f"{probe_start_sec:.3f}", "-i", str(video_path),
            "-t", f"{probe_duration_sec:.3f}", "-vn", "-ac", "1", "-ar", "16000",
            "-acodec", "pcm_s16le", str(probe_path),
        ]
        subprocess.run(cmd, capture_output=True)
        if not probe_path.exists() or probe_path.stat().st_size == 0:
            return []

        result = subprocess.run(
            ["ffmpeg", "-i", str(probe_path), "-af",
             f"silencedetect=noise={noise_db}dB:d={min_silence_sec}", "-f", "null", "-"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        starts = [float(x) for x in _SILENCE_START_ALL_RE.findall(result.stderr)]
        ends = [float(x) for x in _SILENCE_END_ALL_RE.findall(result.stderr)]
        silences = list(zip(starts, ends))
        if len(starts) > len(ends):
            silences.append((starts[-1], probe_duration_sec))

        spans = []
        pos = 0.0
        for silence_start, silence_end in silences:
            if silence_start > pos:
                spans.append((pos, silence_start))
            pos = max(pos, silence_end)
        if pos < probe_duration_sec:
            spans.append((pos, probe_duration_sec))

        return [(probe_start_sec + a, probe_start_sec + b) for a, b in spans]
    finally:
        cleanup_temp_file(probe_path)
        try:
            tmp_dir.rmdir()
        except OSError:
            pass
