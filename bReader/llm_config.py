#!/usr/bin/env python3
"""
LLM Configuration: LangChain, LangSmith, DeepSeek settings
"""

import os
import logging
from pathlib import Path
from logging_config import get_logger

logger = logging.getLogger(__name__)

# Load environment variables from .env file
try:
    from dotenv import load_dotenv

    # Определяем путь к .env файлу (в той же директории что и этот скрипт)
    env_path = Path(__file__).parent / '.env'

    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
        logger.info(f"Loaded environment variables from: {env_path}")
    else:
        logger.warning(f".env file not found at: {env_path}")
except ImportError:
    logger.warning("python-dotenv not installed. Install with: pip install python-dotenv")
except Exception as e:
    logger.error(f"Error loading .env file: {e}")

logger = logging.getLogger(__name__)


# ============================================================================
# DEEPSEEK CONFIGURATION
# ============================================================================

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_TEMPERATURE = float(os.getenv("DEEPSEEK_TEMPERATURE", "0.2"))

# Проверка наличия обязательных параметров
if not DEEPSEEK_API_KEY:
    logger.warning("DEEPSEEK_API_KEY not set in environment variables or .env file")


# ============================================================================
# LANGSMITH TRACING CONFIGURATION
# ============================================================================

LANGSMITH_API_KEY = os.getenv("LANGSMITH_API_KEY")
LANGSMITH_PROJECT = os.getenv("LANGSMITH_PROJECT", "proj1")
LANGSMITH_ENDPOINT = os.getenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com")


# ============================================================================
# LANGCHAIN CONFIGURATION
# ============================================================================

# Включаем трейсинг если установлен API ключ
def configure_langsmith_tracing():
    """Настраивает LangSmith трейсинг если доступен API ключ"""
    if LANGSMITH_API_KEY:
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGCHAIN_API_KEY"] = LANGSMITH_API_KEY
        os.environ["LANGCHAIN_PROJECT"] = LANGSMITH_PROJECT
        os.environ["LANGCHAIN_ENDPOINT"] = LANGSMITH_ENDPOINT
        logger.info(f"LangSmith tracing enabled for project: {LANGSMITH_PROJECT}")
        return True
    else:
        logger.warning("LangSmith tracing disabled - LANGSMITH_API_KEY not set")
        return False


# ============================================================================
# LANGSMITH DECORATOR
# ============================================================================

# Try to import langsmith for tracing
try:
    from langsmith import traceable
    LANGSMITH_AVAILABLE = True
except ImportError:
    LANGSMITH_AVAILABLE = False
    # Create a no-op decorator if langsmith is not available
    def traceable(*args, **kwargs):
        def decorator(func):
            return func
        return decorator if not args else decorator(args[0])


# Автоматически настраиваем трейсинг при импорте модуля
configure_langsmith_tracing()

