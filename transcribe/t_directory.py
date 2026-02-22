#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
Скрипт для рекурсивной обработки медиафайлов в директории.

Обходит все папки и файлы в указанной директории,
для каждого медиафайла (аудио/видео) вызывает скрипт транскрибации
для получения транскрипции.

Usage:
    python t_directory.py <directory> [--script t_gigaam_simple.py] [--revision e2e_rnnt] [--device auto]
    python t_directory.py H:\videos --device cuda
    python t_directory.py C:\media --script t_gigaam.py --device cuda
    python t_directory.py C:\media --script t_gigaam_blocks.py --revision e2e_ctc --device cpu
"""

import argparse
import logging
import sys
from pathlib import Path
from typing import List
import subprocess

# Импорт функций для работы с транскрипциями
from transcribe_utils import find_existing_transcription

# ============================================================================
# Настройка логирования
# ============================================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================================
# Конфигурация
# ============================================================================

# Поддерживаемые аудио форматы
AUDIO_EXTENSIONS = {'.wav', '.mp3', '.m4a', '.ogg', '.flac', '.aac', '.wma', '.amr', '.opus', '.3gp'}

# Поддерживаемые видео форматы
VIDEO_EXTENSIONS = {
    '.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv',
    '.webm', '.m4v', '.mpg', '.mpeg', '.3gp', '.ts'
}

# Все поддерживаемые медиа форматы
MEDIA_EXTENSIONS = AUDIO_EXTENSIONS | VIDEO_EXTENSIONS


def is_media_file(file_path: Path) -> bool:
    """Проверяет, является ли файл медиафайлом"""
    return file_path.suffix.lower() in MEDIA_EXTENSIONS


def has_transcription(file_path: Path, revision: str) -> bool:
    """
    Проверяет, существует ли уже транскрипция для файла с любым суффиксом.

    Args:
        file_path: Путь к медиафайлу
        revision: Используемая ревизия модели

    Returns:
        True если найдена транскрипция с любым суффиксом (speakers, blocks, simple)
        и она имеет ненулевой размер
    """
    # Используем общую функцию поиска транскрипции
    transcription_path = find_existing_transcription(file_path, revision)
    return transcription_path is not None


def collect_media_files(
        directory: Path,
        skip_existing: bool = True,
        revision: str = "e2e_rnnt"
) -> List[Path]:
    """
    Собирает все медиафайлы в директории рекурсивно.

    Args:
        directory: Корневая директория для поиска
        skip_existing: Пропускать файлы с существующей транскрипцией
        revision: Ревизия модели для проверки существующих файлов

    Returns:
        Список путей к медиафайлам для обработки
    """
    logger.info(f"Сканирование директории: {directory}")

    media_files = []
    skipped_count = 0

    for file_path in directory.rglob('*'):
        if not file_path.is_file():
            continue

        if not is_media_file(file_path):
            continue

        if skip_existing and has_transcription(file_path, revision):
            logger.debug(f"Пропущен (транскрипция существует): {file_path.name}")
            skipped_count += 1
            continue

        media_files.append(file_path)

    logger.info(
        f"Найдено медиафайлов:\n"
        f"  - Для обработки: {len(media_files)}\n"
        f"  - Пропущено (уже обработано): {skipped_count}\n"
        f"  - Всего: {len(media_files) + skipped_count}\n"
        f"  - Порядок обработки: от новых к старым (по времени изменения)"
    )

    # Сортируем по времени модификации (от новых к старым)
    sorted_files = sorted(media_files, key=lambda p: p.stat().st_mtime, reverse=True)

    # Логируем первые 5 файлов для проверки
    if sorted_files:
        logger.info("Первые файлы для обработки:")
        for i, file_path in enumerate(sorted_files[:5], 1):
            mtime = file_path.stat().st_mtime
            from datetime import datetime
            mtime_str = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')
            logger.info(f"  {i}. {file_path.name} (изменён: {mtime_str})")
        if len(sorted_files) > 5:
            logger.info(f"  ... и ещё {len(sorted_files) - 5} файлов")

    return sorted_files


def process_single_file(
        file_path: Path,
        revision: str,
        device: str,
        t_gigaam_script: Path
) -> bool:
    """
    Обрабатывает один медиафайл через t_gigaam.py.

    Args:
        file_path: Путь к медиафайлу
        revision: Ревизия модели GigaAM
        device: Устройство (cuda/cpu/auto)
        t_gigaam_script: Путь к скрипту t_gigaam.py

    Returns:
        True если обработка успешна, False иначе
    """
    logger.info(f"\n{'=' * 80}\nОбработка: {file_path.name}\n{'=' * 80}")

    cmd = [
        sys.executable,
        str(t_gigaam_script),
        str(file_path),
        "--revision", revision,
        "--device", device
    ]

    logger.info(f"{' '.join(cmd)}")

    try:
        # Вывод идет напрямую в консоль в реальном времени
        result = subprocess.run(cmd, check=False)

        if result.returncode == 0:
            logger.info(f"✓ Успешно обработан: {file_path.name}")
            return True
        else:
            logger.error(f"✗ Ошибка обработки: {file_path.name} (код: {result.returncode})")
            return False

    except Exception as e:
        logger.error(f"✗ Исключение при обработке {file_path.name}: {e}")
        return False


def process_directory(
        directory: Path,
        revision: str,
        device: str,
        skip_existing: bool,
        t_gigaam_script: Path
) -> dict:
    """
    Обрабатывает все медиафайлы в директории.

    Args:
        directory: Директория для обработки
        revision: Ревизия модели
        device: Устройство
        skip_existing: Пропускать существующие транскрипции
        t_gigaam_script: Путь к скрипту t_gigaam.py

    Returns:
        Словарь со статистикой обработки
    """
    media_files = collect_media_files(directory, skip_existing, revision)

    if not media_files:
        logger.info("Нет файлов для обработки")
        return {"total": 0, "success": 0, "failed": 0}

    stats = {"total": len(media_files), "success": 0, "failed": 0}

    for idx, file_path in enumerate(media_files, 1):
        logger.info(f"\n[{idx}/{stats['total']}] Обработка файла...")

        success = process_single_file(file_path, revision, device, t_gigaam_script)

        if success:
            stats["success"] += 1
        else:
            stats["failed"] += 1

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Рекурсивная обработка медиафайлов через GigaAM-v3 + pyannote"
    )
    parser.add_argument(
        "directory",
        type=str,
        help="Директория для обработки"
    )
    parser.add_argument(
        "--revision",
        default="e2e_rnnt",
        choices=["e2e_rnnt", "e2e_ctc", "rnnt", "ctc"],
        help="Ревизия модели GigaAM-v3"
    )
    parser.add_argument(
        "--device",
        default="auto",
        choices=["auto", "cpu", "cuda"],
        help="Устройство для обработки"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Обрабатывать все файлы, даже если транскрипция уже существует"
    )
    parser.add_argument(
        "--script",
        default="t_gigaam_simple.py",
        help="Имя скрипта для транскрибации (по умолчанию: t_gigaam_simple.py)"
    )

    args = parser.parse_args()

    # Проверка директории
    directory = Path(args.directory).expanduser().resolve()
    if not directory.exists():
        logger.error(f"Директория не найдена: {directory}")
        sys.exit(1)

    if not directory.is_dir():
        logger.error(f"Путь не является директорией: {directory}")
        sys.exit(1)

    # Проверка наличия скрипта транскрибации
    script_dir = Path(__file__).parent
    t_gigaam_script = script_dir / args.script

    if not t_gigaam_script.exists():
        logger.error(f"Не найден скрипт транскрибации: {t_gigaam_script}")
        sys.exit(1)

    # Запуск обработки
    logger.info(
        f"{'=' * 80}\n"
        f"Параметры обработки:\n"
        f"  Директория: {directory}\n"
        f"  Скрипт транскрибации: {args.script}\n"
        f"  Ревизия: {args.revision}\n"
        f"  Устройство: {args.device}\n"
        f"  Пропускать существующие: {not args.force}\n"
        f"{'=' * 80}"
    )

    try:
        stats = process_directory(
            directory,
            args.revision,
            args.device,
            skip_existing=not args.force,
            t_gigaam_script=t_gigaam_script
        )

        # Итоговая статистика
        if stats['total'] > 0:
            success_percent = stats['success'] / stats['total'] * 100
            logger.info(
                f"\n{'=' * 80}\n"
                f"ИТОГОВАЯ СТАТИСТИКА:\n"
                f"  Всего файлов: {stats['total']}\n"
                f"  Успешно: {stats['success']}\n"
                f"  Ошибок: {stats['failed']}\n"
                f"  Процент успеха: {success_percent:.1f}%\n"
                f"{'=' * 80}"
            )
        else:
            logger.info(
                f"\n{'=' * 80}\n"
                f"ИТОГОВАЯ СТАТИСТИКА:\n"
                f"  Всего файлов: 0\n"
                f"  Файлы не были обработаны\n"
                f"{'=' * 80}"
            )

        sys.exit(0 if stats['failed'] == 0 else 1)

    except KeyboardInterrupt:
        logger.warning("\n\nОбработка прервана пользователем (Ctrl+C)")
        sys.exit(130)
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

