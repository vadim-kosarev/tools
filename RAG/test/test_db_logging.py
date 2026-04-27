"""
Тест для проверки исправления имен файлов логов.

Проверяет, что файлы с префиксом DB: создаются правильно
и не создаются пустые файлы xxx_llm_db.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from llm_call_logger import LlmCallLogger


def test_db_logging():
    """Тест логирования DB запросов."""
    print("=" * 80)
    print("ТЕСТ: Логирование DB запросов (исправление имен файлов)")
    print("=" * 80)

    # Создаем временную папку для тестов
    test_log_dir = Path(__file__).parent / "logs" / "_test"
    test_log_dir.mkdir(parents=True, exist_ok=True)

    # Очищаем test папку
    for file in test_log_dir.glob("*"):
        file.unlink()

    # Создаем logger
    logger = LlmCallLogger(enabled=True, log_dir=test_log_dir, separate_files=True)

    print("\n1. Тест DB:semantic_search")
    rec1 = logger.start_record("DB:semantic_search")
    rec1.set_request("query='test'\ntop_k=10")
    rec1.set_response("Найдено 5 чанков")
    print("   ✅ Записан REQUEST и RESPONSE")

    print("\n2. Тест DB:exact_search")
    rec2 = logger.start_record("DB:exact_search")
    rec2.set_request("substring='СУБД'\nlimit=30")
    rec2.set_response("Найдено 12 чанков")
    print("   ✅ Записан REQUEST и RESPONSE")

    print("\n3. Тест TOOL:exact_search")
    rec3 = logger.start_record("TOOL:exact_search")
    rec3.set_request("{'substring': 'СУБД'}")
    rec3.set_response("Результат поиска")
    print("   ✅ Записан REQUEST и RESPONSE")

    print("\n4. Проверка созданных файлов:")
    files = sorted(test_log_dir.glob("*"))

    has_empty = False
    has_colon = False

    for file in files:
        size = file.stat().st_size
        status = "✅" if size > 0 else "❌"
        print(f"   {status} {file.name} ({size} байт)")

        if size == 0:
            has_empty = True
        if ":" in file.name:
            has_colon = True
            print(f"      ⚠️  ОШИБКА: двоеточие в имени файла!")

    print("\n" + "=" * 80)
    print("РЕЗУЛЬТАТ")
    print("=" * 80)

    if has_empty:
        print("❌ FAIL: Есть пустые файлы!")
        return False
    elif has_colon:
        print("❌ FAIL: Есть двоеточия в именах файлов!")
        return False
    else:
        print("✅ PASS: Все файлы созданы правильно!")
        print(f"✅ Создано файлов: {len(files)}")
        print("✅ Нет пустых файлов")
        print("✅ Нет недопустимых символов в именах")
        return True


if __name__ == "__main__":
    success = test_db_logging()
    sys.exit(0 if success else 1)

