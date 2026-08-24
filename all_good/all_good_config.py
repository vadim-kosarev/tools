#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Конфигурация для all_good.

Все константы, параметры и настройки. Загружает переменные из .env файла.
"""

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent / '.env'
    load_dotenv(dotenv_path=env_path, override=False)
except ImportError:
    pass


# ============================================================================
# FFmpeg
# ============================================================================

FFMPEG_BIN = os.environ.get("FFMPEG_BIN", r"C:\Tools\ffmpeg-8.0.1-full_build-shared\bin")


def setup_ffmpeg_path() -> None:
    """Настраивает путь к FFmpeg DLL."""
    if hasattr(os, 'add_dll_directory'):
        if os.path.isdir(FFMPEG_BIN):
            os.add_dll_directory(FFMPEG_BIN)
        else:
            print(f"Ошибка: папка {FFMPEG_BIN} не найдена!")
    else:
        os.environ["PATH"] = FFMPEG_BIN + os.pathsep + os.environ.get("PATH", "")


# ============================================================================
# Hugging Face
# ============================================================================

# Нужен для pyannote/segmentation-3.0 (VAD-сегментация внутри GigaAM transcribe_longform)
HF_TOKEN = os.environ.get("HF_TOKEN", "")

# ============================================================================
# GigaAM
# ============================================================================

GIGAAM_MODEL_NAME = "ai-sage/GigaAM-v3"
GIGAAM_AVAILABLE_REVISIONS = ["e2e_rnnt", "e2e_ctc"]

AUDIO_SAMPLE_RATE = 16000

# scan_transcribe.config.* — значения по умолчанию для CLI-флагов scan_transcribe.py
SCAN_TRANSCRIBE_REVISION = os.environ.get("scan_transcribe.config.revision", "e2e_rnnt")
SCAN_TRANSCRIBE_DEVICE = os.environ.get("scan_transcribe.config.device", "auto")
SCAN_TRANSCRIBE_MAX_SEGMENT_SEC = float(os.environ.get("scan_transcribe.config.max-segment-sec", "12.0"))
SCAN_TRANSCRIBE_MIN_SEGMENT_SEC = float(os.environ.get("scan_transcribe.config.min-segment-sec", "3.0"))

# ============================================================================
# Файлы
# ============================================================================

VIDEO_EXTENSIONS = {
    '.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv',
    '.webm', '.m4v', '.mpg', '.mpeg', '.3gp', '.ts',
}

# Суффикс выходного файла с таймстампами: <имя_видео>.gigaam-<revision>.segments.txt
OUTPUT_SUFFIX_TEMPLATE = ".gigaam-{revision}.segments.txt"

# ============================================================================
# Логирование
# ============================================================================

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
LOG_FORMAT = '%(asctime)s %(elapsed)s %(levelname)s %(message)s'
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 7
