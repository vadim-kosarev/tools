#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Конфигурация для системы транскрибации.

Все константы, параметры и настройки.
Загружает переменные из .env файла.
"""

import os
from pathlib import Path

# Загрузка переменных окружения из .env файла
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent / '.env'
    load_dotenv(dotenv_path=env_path, override=False)
except ImportError:
    # python-dotenv не установлен - используем os.environ напрямую
    pass


# ============================================================================
# FFmpeg Configuration
# ============================================================================

FFMPEG_BIN = os.environ.get("FFMPEG_BIN", r"C:\Tools\ffmpeg-8.0.1-full_build-shared\bin")


def setup_ffmpeg_path():
    """Настраивает путь к FFmpeg DLLs"""
    if hasattr(os, 'add_dll_directory'):
        if os.path.isdir(FFMPEG_BIN):
            os.add_dll_directory(FFMPEG_BIN)
            print(f"FFmpeg DLL path added: {FFMPEG_BIN}")
        else:
            print(f"Ошибка: папка {FFMPEG_BIN} не найдена!")
    else:
        os.environ["PATH"] = FFMPEG_BIN + os.pathsep + os.environ.get("PATH", "")


# ============================================================================
# Hugging Face Token
# ============================================================================

# Токен для доступа к моделям (pyannote и др.)
HF_TOKEN = os.environ.get("HF_TOKEN", "")

# ============================================================================
# Audio Processing Parameters
# ============================================================================

# Параметры нарезки аудио на чанки
CHUNK_SEC = float(os.environ.get("CHUNK_SEC", "20.0"))
OVERLAP_SEC = float(os.environ.get("OVERLAP_SEC", "1.0"))

# Параметры аудио
AUDIO_SAMPLE_RATE = int(os.environ.get("AUDIO_SAMPLE_RATE", "16000"))
AUDIO_CHANNELS = 1  # Mono
AUDIO_CODEC = "pcm_s16le"  # PCM 16-bit

# ============================================================================
# Text Segmentation Parameters
# ============================================================================

# Параметры группировки текста в блоки
MIN_PAUSE_SEC = int(os.environ.get("MIN_PAUSE_SEC", "60"))
MAX_BLOCK_DURATION_SEC = int(os.environ.get("MAX_BLOCK_DURATION_SEC", "600"))
MIN_BLOCK_DURATION_SEC = 120  # для двухминутных блоков (сек)

# ============================================================================
# Speaker Diarization Parameters
# ============================================================================

MIN_SEGMENT_DURATION_SEC = float(os.environ.get("MIN_SEGMENT_DURATION_SEC", "0.8"))
DEFAULT_NUM_SPEAKERS = int(os.environ.get("DEFAULT_NUM_SPEAKERS", "2"))

# ============================================================================
# File Extensions
# ============================================================================

# Поддерживаемые аудио форматы
AUDIO_EXTENSIONS = {'.wav', '.mp3', '.m4a', '.ogg', '.flac', '.aac', '.wma', '.amr', '.opus'}

# Поддерживаемые видео форматы
VIDEO_EXTENSIONS = {
    '.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv',
    '.webm', '.m4v', '.mpg', '.mpeg', '.3gp', '.ts'
}

# Все поддерживаемые медиа форматы
MEDIA_EXTENSIONS = AUDIO_EXTENSIONS | VIDEO_EXTENSIONS

# Проблемные аудиоформаты, требующие конвертации для pyannote
# (вызывают ошибки с количеством сэмплов)
PROBLEMATIC_AUDIO_FORMATS = {'.amr', '.m4a', '.aac', '.3gp', '.opus', '.wma'}

# ============================================================================
# Funny Speaker Names
# ============================================================================

# Список прикольных бесполых имен для спикеров
FUNNY_SPEAKER_NAMES = [
    "Пикачу", "Бублик", "Котлета", "Зефирка", "Кактус",
    "Вафля", "Печенька", "Шарик", "Кнопка", "Носок",
    "Байт", "Пиксель", "Глюк", "Фикс", "Баг",
    "Сэндвич", "Маффин", "Тостер", "Пончик", "Круассан"
]

# ============================================================================
# Model Configuration
# ============================================================================

# GigaAM модель
GIGAAM_MODEL_NAME = "ai-sage/GigaAM-v3"
GIGAAM_DEFAULT_REVISION = "e2e_rnnt"
GIGAAM_AVAILABLE_REVISIONS = ["e2e_rnnt", "e2e_ctc", "rnnt", "ctc", "ssl"]

# Pyannote модель
PYANNOTE_MODEL_NAME = "pyannote/speaker-diarization-3.1"

# ============================================================================
# Logging Configuration
# ============================================================================

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
LOG_FORMAT = '%(asctime)s - %(levelname)s - %(message)s'
