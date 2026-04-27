"""
Тест автоматической очистки логов при запуске агента.
"""
from pathlib import Path
import sys

# Добавляем путь к модулю
sys.path.insert(0, str(Path(__file__).parent))

from rag_lg_agent import clear_logs_directory

print("="*80)
print("ТЕСТ: Автоматическая очистка логов")
print("="*80)

logs_dir = Path(__file__).parent / "logs"
logs_dir.mkdir(parents=True, exist_ok=True)

# Создаем тестовые файлы
test_files = [
    "001_llm_plan_request.log",
    "002_llm_plan_response.log",
    "003_tool_exact_search_request.log",
    "004_tool_exact_search_response.log",
    "_rag_llm.log",
]

print("\n1. Создаем тестовые файлы логов:")
for filename in test_files:
    filepath = logs_dir / filename
    filepath.write_text(f"Test content for {filename}")
    print(f"   ✅ Создан: {filename}")

# Создаем файл, который НЕ должен удаляться
keep_file = logs_dir / "README.md"
keep_file.write_text("This file should NOT be deleted")
print(f"   ✅ Создан: README.md (не должен удалиться)")

print("\n2. Проверяем наличие файлов:")
log_files_before = list(logs_dir.glob("[0-9][0-9][0-9]_*.log"))
old_log_before = (logs_dir / "_rag_llm.log").exists()
readme_before = keep_file.exists()

print(f"   📊 Файлов с нумерацией: {len(log_files_before)}")
print(f"   📊 _rag_llm.log существует: {old_log_before}")
print(f"   📊 README.md существует: {readme_before}")

print("\n3. Вызываем clear_logs_directory():")
clear_logs_directory()

print("\n4. Проверяем результат:")
log_files_after = list(logs_dir.glob("[0-9][0-9][0-9]_*.log"))
old_log_after = (logs_dir / "_rag_llm.log").exists()
readme_after = keep_file.exists()

print(f"   📊 Файлов с нумерацией: {len(log_files_after)}")
print(f"   📊 _rag_llm.log существует: {old_log_after}")
print(f"   📊 README.md существует: {readme_after}")

print("\n" + "="*80)
print("РЕЗУЛЬТАТ:")
print("="*80)

success = True

if len(log_files_after) == 0:
    print("✅ Файлы с нумерацией успешно удалены")
else:
    print(f"❌ ОШИБКА: Остались файлы с нумерацией: {len(log_files_after)}")
    success = False

if not old_log_after:
    print("✅ _rag_llm.log успешно удален")
else:
    print("❌ ОШИБКА: _rag_llm.log не удален")
    success = False

if readme_after:
    print("✅ README.md сохранен (не удален)")
else:
    print("❌ ОШИБКА: README.md был удален!")
    success = False

# Очистка тестовых файлов
keep_file.unlink()

print("\n" + "="*80)
if success:
    print("🎉 ТЕСТ ПРОЙДЕН! Автоматическая очистка работает корректно.")
else:
    print("⚠️  ТЕСТ НЕ ПРОЙДЕН! Требуется проверка.")
print("="*80)

