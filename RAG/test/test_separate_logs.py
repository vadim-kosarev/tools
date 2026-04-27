"""Тест системы логирования с отдельными файлами."""
from pathlib import Path
from llm_call_logger import LlmCallLogger

# Создаем временную директорию для теста
test_dir = Path(__file__).parent / "logs" / "test"
test_dir.mkdir(parents=True, exist_ok=True)

print("="*80)
print("ТЕСТ: Система логирования с отдельными файлами")
print("="*80)

# Очищаем тестовую директорию
for f in test_dir.glob("*.log"):
    f.unlink()

print(f"\nТестовая директория: {test_dir}")
print()

# Создаем logger с separate_files=True
logger = LlmCallLogger(
    enabled=True,
    log_dir=test_dir,
    separate_files=True
)

print("Создаем тестовые записи...")
print()

# Тест 1: LLM REQUEST
with logger.record("PLAN") as rec:
    rec.set_request("""[SYSTEM]
# Test System Prompt

[AVAILABLE_TOOLS]
[{"name":"semantic_search","description":"test"}]

[USER]
тестовый запрос""")

    rec.set_response("""{"status":"plan","step":1,"thought":"тестовый план","plan":["шаг 1","шаг 2"]}""")

print("✅ Записан: PLAN request/response")

# Тест 2: TOOL REQUEST/RESPONSE
with logger.record("TOOL:exact_search") as rec:
    rec.set_request("""{"substring": "test", "limit": 10}""")
    rec.set_response("""**SearchChunksResult**
- query: test
- chunks: (2 элементов)
  1. ChunkResult(source=test.md, section=Test, line=100, content='test content')
- total_found: 2""")

print("✅ Записан: TOOL:exact_search request/response")

# Тест 3: Еще один LLM запрос
with logger.record("OBSERVATION") as rec:
    rec.set_request("""[SYSTEM]
Analyze results

[MESSAGES]

[USER]
analyze this

[TOOL_RESULT: exact_search]
Found 2 results""")

    rec.set_response("""{"status":"observation","step":3,"observation":"found 2 results"}""")

print("✅ Записан: OBSERVATION request/response")

# Проверяем созданные файлы
print()
print("="*80)
print("РЕЗУЛЬТАТ:")
print("="*80)

log_files = sorted(test_dir.glob("[0-9][0-9][0-9]_*.log"))
print(f"\nСоздано файлов: {len(log_files)}")
print()

for f in log_files:
    size = f.stat().st_size
    print(f"  ✅ {f.name:40} {size:>6} bytes")

print()
print("="*80)
print("СОДЕРЖИМОЕ ПЕРВОГО ФАЙЛА (001):")
print("="*80)
print()

if log_files:
    with log_files[0].open("r", encoding="utf-8") as f:
        content = f.read()
        # Показываем первые 500 символов
        if len(content) > 500:
            print(content[:500])
            print(f"\n... (еще {len(content)-500} символов)")
        else:
            print(content)

print()
print(f"{'='*80}")
print(f"✅ ТЕСТ ПРОЙДЕН! Проверьте файлы в: {test_dir}")
print(f"{'='*80}")

