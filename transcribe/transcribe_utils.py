#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Утилиты для системы транскрибации.

Общие функции для работы с:
- FFmpeg (извлечение аудио, получение длительности, нарезка)
- Файлами (проверка типов, временные директории)
- Временем (форматирование)
- Текстом (разбиение на предложения, дедупликация)
"""

import subprocess
import tempfile
import re
import random
import logging
from pathlib import Path
from typing import List, Dict
from datetime import timedelta

from transcribe_config import (
    AUDIO_SAMPLE_RATE,
    AUDIO_CHANNELS,
    AUDIO_CODEC,
    VIDEO_EXTENSIONS,
    AUDIO_EXTENSIONS,
    MEDIA_EXTENSIONS,
    OVERLAP_SEC,
    FUNNY_SPEAKER_NAMES
)
from transcribe_dto import (
    ChunkInfo,
    ChunkBoundary,
    AudioChunkingResult
)

logger = logging.getLogger(__name__)


# ============================================================================
# Время и форматирование
# ============================================================================

def seconds_to_hhmmss(total_sec: float) -> str:
    """Конвертирует секунды в формат [HH:mm:ss]"""
    td = timedelta(seconds=int(total_sec))
    return f"[{str(td).zfill(8)}]"


# ============================================================================
# Проверка типов файлов
# ============================================================================

def is_video_file(file_path: Path) -> bool:
    """Проверяет, является ли файл видео"""
    return file_path.suffix.lower() in VIDEO_EXTENSIONS


def is_audio_file(file_path: Path) -> bool:
    """Проверяет, является ли файл аудио"""
    return file_path.suffix.lower() in AUDIO_EXTENSIONS


def needs_audio_conversion(file_path: Path) -> bool:
    """
    Проверяет, нужна ли конвертация аудио для pyannote.
    AMR, M4A и некоторые другие форматы вызывают проблемы с сэмплами.

    Args:
        file_path: Путь к аудиофайлу

    Returns:
        True если файл нужно конвертировать в WAV для pyannote
    """
    problematic_formats = {'.amr', '.m4a', '.aac', '.3gp', '.opus', '.wma'}
    return file_path.suffix.lower() in problematic_formats


def is_media_file(file_path: Path) -> bool:
    """Проверяет, является ли файл медиафайлом (аудио или видео)"""
    return file_path.suffix.lower() in MEDIA_EXTENSIONS


# ============================================================================
# FFmpeg: Получение информации
# ============================================================================

def get_audio_duration_from_ffmpeg(input_path: Path) -> float:
    """
    Извлекает длительность аудио/видео через ffmpeg.

    Returns:
        Длительность в секундах, или 0.0 если не удалось определить
    """
    logger.debug(f"Получение длительности: {input_path.name}")

    duration_cmd = ["ffmpeg", "-i", str(input_path), "-f", "null", "-"]
    logger.debug(f"Команда: {' '.join(duration_cmd)}")

    result = subprocess.run(
        duration_cmd,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace"
    )

    for line in result.stderr.splitlines():
        if "Duration:" in line:
            dur_str = line.split("Duration:")[1].split(",")[0].strip()
            h, m, s_ms = dur_str.split(":")
            s, _ = s_ms.split(".")
            total_sec = int(h) * 3600 + int(m) * 60 + int(s)
            logger.info(f"Длительность аудио: {total_sec:.1f} сек")
            return float(total_sec)

    logger.warning(f"Не удалось определить длительность: {input_path.name}")
    return 0.0


# ============================================================================
# FFmpeg: Извлечение аудио из видео
# ============================================================================

def extract_audio_from_video(video_path: Path, output_dir: Path) -> Path:
    """
    Извлекает аудио из видеофайла в WAV формат.

    Args:
        video_path: Путь к видеофайлу
        output_dir: Директория для сохранения аудио

    Returns:
        Путь к извлечённому аудиофайлу

    Raises:
        RuntimeError: Если FFmpeg завершился с ошибкой
    """
    audio_path = output_dir / f"{video_path.stem}_audio.wav"

    logger.info(f"Извлечение аудио из видео: {video_path.name}")

    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vn",  # Без видео
        "-acodec", AUDIO_CODEC,
        "-ar", str(AUDIO_SAMPLE_RATE),
        "-ac", str(AUDIO_CHANNELS),
        str(audio_path)
    ]

    logger.info(f"Команда: {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace"
    )

    if result.returncode != 0:
        logger.error(f"Ошибка извлечения аудио: {result.stderr}")
        raise RuntimeError(f"FFmpeg failed to extract audio from {video_path}")

    file_size_mb = audio_path.stat().st_size / 1024 / 1024
    logger.info(f"✓ Аудио извлечено: {audio_path.name} ({file_size_mb:.2f} MB)")

    return audio_path


def convert_audio_to_wav(audio_path: Path, output_dir: Path) -> Path:
    """
    Конвертирует аудиофайл в WAV формат для корректной работы pyannote.
    Используется для проблемных форматов (AMR, M4A, AAC и др.).

    Args:
        audio_path: Путь к исходному аудиофайлу
        output_dir: Директория для сохранения WAV

    Returns:
        Путь к сконвертированному WAV файлу

    Raises:
        RuntimeError: Если FFmpeg завершился с ошибкой
    """
    wav_path = output_dir / f"{audio_path.stem}_converted.wav"

    logger.info(f"Конвертация аудио в WAV для pyannote: {audio_path.name}")

    cmd = [
        "ffmpeg", "-y", "-i", str(audio_path),
        "-acodec", AUDIO_CODEC,
        "-ar", str(AUDIO_SAMPLE_RATE),
        "-ac", str(AUDIO_CHANNELS),
        str(wav_path)
    ]

    logger.info(f"Команда: {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace"
    )

    if result.returncode != 0:
        logger.error(f"Ошибка конвертации аудио: {result.stderr}")
        raise RuntimeError(f"FFmpeg failed to convert audio from {audio_path}")

    file_size_mb = wav_path.stat().st_size / 1024 / 1024
    logger.info(f"✓ Аудио сконвертировано: {wav_path.name} ({file_size_mb:.2f} MB)")

    return wav_path


# ============================================================================
# FFmpeg: Извлечение чанка
# ============================================================================

def extract_audio_chunk_with_ffmpeg(
        input_path: Path,
        start_sec: float,
        end_sec: float,
        output_path: Path
) -> None:
    """
    Извлекает один чанк аудио через ffmpeg.

    Args:
        input_path: Путь к исходному аудиофайлу
        start_sec: Начало чанка в секундах
        end_sec: Конец чанка в секундах
        output_path: Путь для сохранения чанка
    """
    cmd = [
        "ffmpeg", "-y", "-i", str(input_path),
        "-ss", str(start_sec),
        "-t", str(end_sec - start_sec),
        "-ar", str(AUDIO_SAMPLE_RATE),
        "-ac", str(AUDIO_CHANNELS),
        "-c:a", AUDIO_CODEC,
        str(output_path)
    ]

    logger.debug(f"Команда извлечения чанка: {' '.join(cmd)}")
    subprocess.run(cmd, check=True, capture_output=True)


# ============================================================================
# Нарезка аудио на чанки
# ============================================================================

def calculate_chunk_boundaries(
        total_sec: float,
        chunk_sec: float,
        overlap_sec: float
) -> List[ChunkBoundary]:
    """
    Вычисляет границы всех чанков.

    Args:
        total_sec: Общая длительность аудио
        chunk_sec: Длина одного чанка
        overlap_sec: Перекрытие между чанками

    Returns:
        Список границ чанков
    """
    boundaries = []
    step = chunk_sec - overlap_sec
    start_sec = 0.0

    while start_sec < total_sec:
        end_sec = min(start_sec + chunk_sec, total_sec)
        if end_sec - start_sec < 5:  # Пропускаем слишком короткие чанки
            break
        boundaries.append(ChunkBoundary(start_sec=start_sec, end_sec=end_sec))
        start_sec += step

    return boundaries


def generate_chunk_filename(start_sec: float, tmp_dir: Path) -> Path:
    """Генерирует имя файла для чанка"""
    return tmp_dir / f"chunk_{int(start_sec):06d}.wav"


def create_temp_directory_for_chunks() -> Path:
    """Создает временную директорию для чанков"""
    return Path(tempfile.mkdtemp(prefix="gigaam_chunks_"))


def cut_audio_to_chunks(
        input_path: str,
        chunk_sec: float,
        overlap_sec: float = OVERLAP_SEC
) -> AudioChunkingResult:
    """
    Нарезает аудио на чанки с перекрытием.

    Args:
        input_path: Путь к аудиофайлу
        chunk_sec: Длина чанка в секундах
        overlap_sec: Перекрытие между чанками

    Returns:
        Результат нарезки с информацией о чанках
    """
    input_path = Path(input_path)
    logger.debug(f"Нарезка аудио на чанки: {input_path}")

    total_sec = get_audio_duration_from_ffmpeg(input_path)
    if total_sec == 0:
        raise RuntimeError(f"Не удалось определить длительность: {input_path}")

    tmp_dir = create_temp_directory_for_chunks()
    boundaries = calculate_chunk_boundaries(total_sec, chunk_sec, overlap_sec)

    chunks = []
    for boundary in boundaries:
        chunk_file = generate_chunk_filename(boundary.start_sec, tmp_dir)
        extract_audio_chunk_with_ffmpeg(
            input_path,
            boundary.start_sec,
            boundary.end_sec,
            chunk_file
        )
        chunks.append(ChunkInfo(start_sec=boundary.start_sec, file_path=chunk_file))

    logger.info(f"Создано {len(chunks)} чанков")
    return AudioChunkingResult(chunks=chunks, total_duration_sec=total_sec)


# ============================================================================
# Работа с текстом
# ============================================================================

def split_into_sentences(text: str) -> List[str]:
    """
    Разбивает текст на предложения по пунктуации.

    Args:
        text: Исходный текст

    Returns:
        Список предложений
    """
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    return [s.strip() for s in sentences if s.strip()]


def calculate_text_similarity(text1: str, text2: str) -> float:
    """
    Вычисляет похожесть двух текстов (0.0 - 1.0).
    Использует алгоритм Jaccard similarity для слов.

    Args:
        text1: Первый текст
        text2: Второй текст

    Returns:
        Коэффициент похожести от 0.0 до 1.0
    """
    words1 = set(text1.lower().split())
    words2 = set(text2.lower().split())

    if not words1 or not words2:
        return 0.0

    intersection = words1.intersection(words2)
    union = words1.union(words2)

    return len(intersection) / len(union) if union else 0.0


# ============================================================================
# Маппинг спикеров на прикольные имена
# ============================================================================

def create_speaker_name_mapping(speaker_ids: List[str]) -> Dict[str, str]:
    """
    Создает маппинг от SPEAKER_XX к прикольным именам.

    Args:
        speaker_ids: Список оригинальных ID спикеров

    Returns:
        Словарь с маппингом {original_id: funny_name}
    """
    # Создаём детерминированный рандом для стабильности
    seed = hash("".join(sorted(speaker_ids)))
    rng = random.Random(seed)

    # Перемешиваем имена
    shuffled_names = FUNNY_SPEAKER_NAMES.copy()
    rng.shuffle(shuffled_names)

    # Создаём маппинг
    mapping = {}
    for idx, speaker_id in enumerate(sorted(speaker_ids)):
        if idx < len(shuffled_names):
            mapping[speaker_id] = shuffled_names[idx]
        else:
            # Если спикеров больше чем имён - добавляем номер
            name_idx = idx % len(shuffled_names)
            suffix = idx // len(shuffled_names) + 1
            mapping[speaker_id] = f"{shuffled_names[name_idx]}-{suffix}"

    logger.info(
        f"Создан маппинг имён спикеров:\n" +
        "\n".join(f"  {k} → {v}" for k, v in mapping.items())
    )

    return mapping


# ============================================================================
# Очистка временных файлов
# ============================================================================

def cleanup_chunk_files(chunks: List[ChunkInfo]) -> None:
    """Удаляет временные файлы чанков"""
    for chunk in chunks:
        if chunk.file_path.exists():
            chunk.file_path.unlink()

    if chunks:
        tmp_dir = chunks[0].file_path.parent
        if tmp_dir.exists():
            try:
                tmp_dir.rmdir()
            except Exception as e:
                logger.warning(f"Не удалось удалить временную директорию {tmp_dir}: {e}")


def cleanup_temp_file(file_path: Path) -> None:
    """Удаляет временный файл"""
    if file_path and file_path.exists():
        try:
            file_path.unlink()
            logger.debug(f"Удалён временный файл: {file_path.name}")
        except Exception as e:
            logger.warning(f"Не удалось удалить файл {file_path}: {e}")


# ============================================================================
# Сохранение результатов
# ============================================================================

def generate_transcription_filename(
        input_path: Path,
        revision: str,
        suffix: str = "blocks"
) -> Path:
    """
    Генерирует имя файла транскрипции по стандартному паттерну.

    Args:
        input_path: Путь к исходному файлу
        revision: Ревизия модели (e2e_rnnt, e2e_ctc и т.д.)
        suffix: Суффикс типа транскрипции (simple, blocks, speakers)

    Returns:
        Path к файлу транскрипции

    Примеры:
        recording.amr → recording.gigaam-e2e_rnnt-simple.txt
        video.mp4 → video.gigaam-e2e_rnnt-speakers.txt
    """
    stem = input_path.stem
    parent = input_path.parent
    return parent / f"{stem}.gigaam-{revision}-{suffix}.txt"


def find_existing_transcription(
        input_path: Path,
        revision: str
) -> Path | None:
    """
    Ищет существующий файл транскрипции с любым суффиксом по паттерну.

    Args:
        input_path: Путь к исходному медиафайлу
        revision: Ревизия модели

    Returns:
        Path к найденному файлу транскрипции или None

    Ищет файлы по паттерну: <имя>.gigaam-<revision>-*.txt
    Примеры: recording.gigaam-e2e_rnnt-speakers.txt
             recording.gigaam-e2e_rnnt-blocks.txt
             recording.gigaam-e2e_rnnt-simple.txt
    """
    stem = input_path.stem
    parent = input_path.parent

    # Паттерн поиска: <имя>.gigaam-<revision>-*.txt
    pattern = f"{stem}.gigaam-{revision}-*.txt"

    # Ищем все файлы, соответствующие паттерну
    matching_files = list(parent.glob(pattern))

    # Проверяем найденные файлы на ненулевой размер
    for transcription_path in matching_files:
        try:
            file_size = transcription_path.stat().st_size
            if file_size > 0:
                return transcription_path  # Возвращаем первый непустой
        except OSError:
            continue

    return None


def save_transcription_to_file(
        full_text: str,
        input_path: Path,
        revision: str,
        suffix: str = "blocks"
) -> Path:
    """
    Сохраняет транскрипцию в текстовый файл.
    Устанавливает дату модификации результата равной дате исходного файла.

    Args:
        full_text: Текст для сохранения
        input_path: Путь к исходному файлу
        revision: Ревизия модели
        suffix: Суффикс имени файла (blocks, speakers, simple и т.д.)

    Returns:
        Путь к созданному файлу
    """
    # Используем единую функцию генерации имени
    out_path = generate_transcription_filename(input_path, revision, suffix)

    out_path.write_text(full_text, encoding="utf-8")

    # Копируем время модификации исходного файла
    original_mtime = input_path.stat().st_mtime
    import os
    os.utime(out_path, (original_mtime, original_mtime))

    logger.info(f"Результат сохранён: {out_path}")

    return out_path


