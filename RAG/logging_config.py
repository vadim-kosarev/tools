"""Централизованная конфигурация логирования для всех агентов.

Настраивает логирование с записью в файл и вывод в консоль одновременно:
- Логи пишутся в logs/{agent_name}.log с автоматической ротацией
- Также выводятся в stderr для отладки в реальном времени
- Используется RotatingFileHandler (макс 10MB на файл, 5 backup-файлов)
- Формат с таймстампом, уровнем и сообщением

Usage:
    from logging_config import setup_logging
    
    logger = setup_logging("rag_single_pass_agent")
    logger.info("Agent started")
"""
import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler


def setup_logging(
    agent_name: str,
    level: int = logging.INFO,
    log_dir: Path | None = None,
    max_bytes: int = 10 * 1024 * 1024,  # 10 MB
    backup_count: int = 5,
    console_output: bool = True,
) -> logging.Logger:
    """Настраивает логирование для агента с записью в файл и консоль.
    
    Args:
        agent_name: Имя агента (используется для имени лог-файла)
        level: Уровень логирования (по умолчанию INFO)
        log_dir: Директория для лог-файлов (по умолчанию logs/ в текущей папке)
        max_bytes: Максимальный размер одного лог-файла (по умолчанию 10MB)
        backup_count: Количество backup-файлов при ротации (по умолчанию 5)
        console_output: Выводить ли логи в stderr (по умолчанию True)
    
    Returns:
        Настроенный logger для агента
    """
    # Определяем директорию для логов
    if log_dir is None:
        log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    
    # Имя лог-файла
    log_file = log_dir / f"{agent_name}.log"
    
    # Формат сообщений
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    
    # Получаем root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    
    # Очищаем существующие handlers (если уже настроены)
    root_logger.handlers.clear()
    
    # 1. FileHandler с ротацией
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)
    
    # 2. StreamHandler для консоли (stderr)
    if console_output:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)
    
    # Подавляем излишне болтливые библиотеки
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    
    # Получаем logger для конкретного агента
    agent_logger = logging.getLogger(agent_name)
    agent_logger.info(f"Logging configured: file={log_file}, level={logging.getLevelName(level)}")
    
    return agent_logger


def get_logger(name: str) -> logging.Logger:
    """Получить logger с именем (для использования в модулях)."""
    return logging.getLogger(name)

