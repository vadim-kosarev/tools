#!/usr/bin/env python3
"""
Logging Configuration
Настройка логирования для приложения с выводом в консоль и файл с ротацией
Включает отображение прошедшего времени (elapsed) с начала работы приложения
"""

import logging
import sys
import time
from pathlib import Path
from logging.handlers import RotatingFileHandler


# Глобальное время начала работы приложения
_APPLICATION_START_TIME = time.time()


class ElapsedTimeFormatter(logging.Formatter):
    """
    Кастомный formatter который добавляет время прошедшее с начала работы приложения
    Формат: YYYY-MM-DD HH:MM:SS [+HH:mm:ss.ms] - name - level - message
    """

    def format(self, record):
        """
        Форматирует логирование с добавлением elapsed time
        """
        # Получаем стандартное форматирование
        log_message = super().format(record)

        # Вычисляем прошедшее время с начала работы
        elapsed_seconds = time.time() - _APPLICATION_START_TIME

        # Конвертируем в HH:mm:ss.ms
        hours = int(elapsed_seconds // 3600)
        minutes = int((elapsed_seconds % 3600) // 60)
        seconds = int(elapsed_seconds % 60)
        milliseconds = int((elapsed_seconds % 1) * 1000)

        elapsed_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"

        # Вставляем elapsed время после таймстампа
        # Ищем первый " - " (разделитель между таймстампом и именем логгера)
        parts = log_message.split(" - ", 1)
        if len(parts) == 2:
            timestamp = parts[0]
            rest = parts[1]
            return f"{timestamp} [+{elapsed_str}] - {rest}"
        else:
            return log_message


def setup_logging(
    log_level: int = logging.INFO,
    log_file: str = "./logs/application.log",
    max_bytes: int = 20 * 1024 * 1024,  # 20 MB
    backup_count: int = 5,
    log_format: str = '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    date_format: str = '%Y-%m-%d %H:%M:%S'
) -> logging.Logger:
    """
    Настраивает логирование для приложения

    Args:
        log_level: Уровень логирования (default: INFO)
        log_file: Путь к файлу логов (default: ./logs/application.log)
        max_bytes: Максимальный размер файла лога в байтах (default: 20MB)
        backup_count: Количество файлов для ротации (default: 5)
        log_format: Формат логирования
        date_format: Формат даты и времени

    Returns:
        Настроенный root logger
    """
    # Создаем директорию для логов если не существует
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Создаем кастомный formatter с поддержкой elapsed time
    formatter = ElapsedTimeFormatter(log_format, datefmt=date_format)

    # Настраиваем root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Очищаем существующие handlers если есть
    root_logger.handlers.clear()

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # File handler with rotation
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding='utf-8'
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # Логируем информацию о настройке
    root_logger.info(f"Logging configured: level={logging.getLevelName(log_level)}, file={log_file}, max_size={max_bytes/(1024*1024):.0f}MB")

    return root_logger


def get_logger(name: str) -> logging.Logger:
    """
    Получает logger с указанным именем

    Args:
        name: Имя logger (обычно __name__)

    Returns:
        Logger instance
    """
    return logging.getLogger(name)


# Инициализация логирования при импорте модуля
setup_logging()



