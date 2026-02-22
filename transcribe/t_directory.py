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
from enum import Enum



class TranscriptionStatus(Enum):
    """Статус транскрипции файла"""
    NOT_ATTEMPTED = "not_attempted"      # Не пытались обрабатывать
    FAILED = "failed"                     # Пытались, но неудачно (пустой файл)
    SUCCESS = "success"                   # Есть успешный результат

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


def get_transcription_status(file_path: Path, revision: str) -> TranscriptionStatus:
    """
    Проверяет статус транскрипции файла.

    Args:
        file_path: Путь к медиафайлу
        revision: Используемая ревизия модели

    Returns:
        TranscriptionStatus:
            - NOT_ATTEMPTED: Нет файла транскрипции (не пытались обрабатывать)
            - FAILED: Есть файл, но он пустой (0 байт) - ошибка обработки
            - SUCCESS: Есть файл с содержимым - успешная транскрипция
    """
    stem = file_path.stem
    parent = file_path.parent

    # Паттерн поиска: <имя>.gigaam-<revision>-*.txt
    pattern = f"{stem}.gigaam-{revision}-*.txt"
    matching_files = list(parent.glob(pattern))

    if not matching_files:
        # Нет файлов транскрипции
        return TranscriptionStatus.NOT_ATTEMPTED

    # Проверяем найденные файлы
    has_empty = False
    has_non_empty = False

    for transcription_path in matching_files:
        try:
            file_size = transcription_path.stat().st_size
            if file_size == 0:
                has_empty = True
            else:
                has_non_empty = True
        except OSError:
            continue

    # Приоритет: если есть хотя бы один непустой файл - SUCCESS
    if has_non_empty:
        return TranscriptionStatus.SUCCESS

    # Если есть только пустые файлы - FAILED
    if has_empty:
        return TranscriptionStatus.FAILED

    # Файлы есть, но недоступны (OSError) - считаем как NOT_ATTEMPTED
    return TranscriptionStatus.NOT_ATTEMPTED


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

        if skip_existing:
            status = get_transcription_status(file_path, revision)
            if status == TranscriptionStatus.SUCCESS:
                # Пропускаем только успешно обработанные файлы
                logger.debug(f"Пропущен (успешная транскрипция): {file_path.name}")
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
    Обрабатывает все медиафайлы в директории с динамическим пересканированием.

    После каждого обработанного файла пересканирует директорию,
    чтобы всегда обрабатывать самый свежий файл (актуально для CallRec и других систем записи).

    Запоминает файлы с ошибками, чтобы не зацикливаться на них.

    Args:
        directory: Директория для обработки
        revision: Ревизия модели
        device: Устройство
        skip_existing: Пропускать существующие транскрипции
        t_gigaam_script: Путь к скрипту t_gigaam.py

    Returns:
        Словарь со статистикой обработки
    """
    stats = {"total": 0, "success": 0, "failed": 0}

    # Множество путей к файлам, обработка которых завершилась с ошибкой
    # Используем множество для быстрой проверки принадлежности
    failed_files = set()

    # Инициализация: сканируем директорию для поиска файлов со статусом FAILED
    # (пустые транскрипции = ошибка обработки)
    logger.info("Проверка наличия файлов с ошибками обработки...")
    initial_scan = collect_media_files(directory, skip_existing=False, revision=revision)

    failed_count = 0
    for file_path in initial_scan:
        status = get_transcription_status(file_path, revision)
        if status == TranscriptionStatus.FAILED:
            failed_files.add(file_path)
            failed_count += 1
            logger.debug(f"Файл со статусом FAILED добавлен в failed_files: {file_path.name}")

    if failed_count > 0:
        logger.warning(
            f"Обнаружено файлов с ошибками обработки: {failed_count}\n"
            f"Эти файлы будут пропущены (имеют пустые транскрипции)"
        )

    # Цикл с динамическим пересканированием
    while True:
        # Пересканируем директорию для поиска необработанных файлов
        media_files = collect_media_files(directory, skip_existing, revision)

        # Фильтруем файлы с ошибками - не пытаемся обрабатывать их повторно
        media_files = [f for f in media_files if f not in failed_files]

        if not media_files:
            # Нет больше файлов для обработки
            if stats["total"] == 0:
                logger.info("Нет файлов для обработки")
            else:
                logger.info("Все файлы обработаны")
                if failed_files:
                    logger.warning(
                        f"Пропущено файлов с ошибками: {len(failed_files)}\n"
                        f"Эти файлы не были обработаны повторно, чтобы избежать зацикливания"
                    )
            break

        # Берём ПЕРВЫЙ файл из отсортированного списка (самый свежий)
        file_path = media_files[0]
        stats["total"] += 1

        logger.info(
            f"\n{'=' * 80}\n"
            f"[{stats['total']}] Обработка файла (осталось необработанных: {len(media_files)})...\n"
            f"{'=' * 80}"
        )

        success = process_single_file(file_path, revision, device, t_gigaam_script)

        if success:
            stats["success"] += 1
        else:
            stats["failed"] += 1
            # Запоминаем файл с ошибкой, чтобы не обрабатывать его повторно
            failed_files.add(file_path)
            logger.debug(f"Файл добавлен в список проваленных: {file_path.name}")

        # После обработки файла цикл повторится и пересканирует директорию
        # Это позволяет обрабатывать новые файлы, появившиеся во время работы

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

