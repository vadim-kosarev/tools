# Инструмент find_abbreviation_expansion

**Дата:** 2026-04-29  
**Автор:** AI Agent  
**Файл:** `RAG/kb_tools.py`  
**Обновлено:** 2026-04-29 (добавлены чанки в результат)

## Описание

Инструмент для поиска расшифровки аббревиатур в базе знаний.

**Ключевая особенность:** Возвращает **список расшифровок с полными чанками** (метаданные, контекст) вместо просто строк.

### Принцип работы

Инструмент автоматически генерирует regex-паттерн для поиска последовательности слов, начинающихся с букв аббревиатуры.

**Алгоритм:**
1. Принимает аббревиатуру заглавными буквами (например, "КЦОИ")
2. Генерирует regex-паттерн: каждая буква → начало слова + любые последующие буквы
3. Между словами допускаются пробелы и знаки препинания
4. Использует существующий `regex_search` для поиска по исходным .md файлам
5. **Извлекает уникальные расшифровки** и нормализует пробелы
6. Возвращает отсортированный список уникальных строк

### Примеры

| Аббревиатура | Паттерн | Результат |
|--------------|---------|-----------|
| `КЦОИ` | `К[а-яё]*\s+Ц[а-яё]*\s+О[а-яё]*\s+И[а-яё]*` | `["Корпоративный Центр Обработки Информации"]` |
| `RAM` | `R[a-z]*\s+A[a-z]*\s+M[a-z]*` | `["Random Access Memory"]` |
| `API` | `A[a-z]*\s+P[a-z]*\s+I[a-z]*` | `["Application Programming Interface"]` |
| `СУБД` | `С[а-яё]*\s+У[а-яё]*\s+Б[а-яё]*\s+Д[а-яё]*` | `["Система Управления Базами Данных"]` |
| `AK47` | `A[a-z]*\s+K[a-z]*\s+47` | `["Автомат Калашникова 47"]` |
| `T34` | `T[a-z]*\s+34` | `["Танк 34"]` |
| `MP5` | `M[a-z]*\s+P[a-z]*\s+5` | `["Maschinenpistole 5"]` |

### Поддержка языков и символов

- **Кириллица:** А-Я, ё
- **Латиница:** A-Z
- **Цифры:** 0-9 (точное совпадение)

Язык букв определяется автоматически для каждого символа. Цифры обрабатываются как точное совпадение.

## Результат (Pydantic модели)

### AbbreviationExpansionItem

```python
class AbbreviationExpansionItem(BaseModel):
    """Одна найденная расшифровка аббревиатуры с чанком"""
    expansion: str           # Текст расшифровки
    chunk: ChunkResult       # Чанк с полными метаданными
```

### AbbreviationExpansionResult

```python
class AbbreviationExpansionResult(BaseModel):
    """Результат поиска расшифровки аббревиатуры"""
    abbreviation: str                           # Исходная аббревиатура
    expansions: list[AbbreviationExpansionItem] # Расшифровки с чанками
    total_found: int                            # Всего найдено расшифровок
    pattern_used: str                           # Использованный regex паттерн
```

### ChunkResult (встроенный)

```python
class ChunkResult(BaseModel):
    content: str            # Содержимое чанка
    metadata: ChunkMetadata # Метаданные (source, section, line_start, etc.)
    score: Optional[float]  # Оценка релевантности
```

### Пример результата

```json
{
  "abbreviation": "КЦОИ",
  "expansions": [
    {
      "expansion": "Корпоративный Центр Обработки Информации",
      "chunk": {
        "content": "...текст чанка с расшифровкой...",
        "metadata": {
          "chunk_id": "5c7d3122-3b40-457d-abde-faeef7f38cf9",
          "source": "Общее описание системы.md",
          "section": "Введение > Термины",
          "chunk_type": "",
          "line_start": 42,
          "line_end": 45,
          "chunk_index": 5,
          "table_headers": null
        },
        "score": null
      }
    }
  ],
  "total_found": 1,
  "pattern_used": "К[а-яё]*\\s+Ц[а-яё]*\\s+О[а-яё]*\\s+И[а-яё]*"
}
```

## Использование

### Python API

```python
from kb_tools import create_kb_tools

tools = create_kb_tools(vectorstore, knowledge_dir)

# Поиск инструмента
find_abbr = next(t for t in tools if t.name == "find_abbreviation_expansion")

# Вызов
result = find_abbr.invoke({"abbreviation": "КЦОИ", "max_results": 50})

# Вывод результатов с метаданными
print(f"Найдено {result.total_found} уникальных расшифровок для '{result.abbreviation}':")
for item in result.expansions:
    print(f"  - {item.expansion}")
    print(f"    Файл: {item.chunk.metadata.source}")
    print(f"    Секция: {item.chunk.metadata.section}")
    print(f"    Строка: {item.chunk.metadata.line_start}-{item.chunk.metadata.line_end}")
```

### В LangChain Agent

Агент может автоматически выбрать этот инструмент при запросах вида:
- "Что означает аббревиатура КЦОИ?"
- "Расшифруй RAM"
- "Найди полное название API"

Теперь агент получает не только расшифровку, но и **полный чанк с контекстом**, что позволяет:
- Показать контекст использования термина
- Использовать `get_neighbor_chunks` для расширения контекста
- Указать точный источник (файл, секция, строка)

Пример ответа агента:
```
Найдено 1 расшифровка для КЦОИ:
- Корпоративный Центр Обработки Информации
```

## Изменения в коде

### 1. Добавлена Pydantic модель результата

```python
class AbbreviationExpansionResult(BaseModel):
    """Результат поиска расшифровки аббревиатуры"""
    abbreviation: str = Field(description="Исходная аббревиатура")
    expansions: list[str] = Field(description="Уникальные найденные расшифровки")
    total_found: int = Field(description="Всего найдено расшифровок")
    pattern_used: str = Field(description="Использованный regex паттерн")
```

### 2. Input модель

```python
class FindAbbreviationExpansionInput(BaseModel):
    abbreviation: str = Field(
        description="Abbreviation in CAPITAL LETTERS to find expansion for (e.g. 'КЦОИ', 'RAM', 'API')"
    )
    max_results: int = Field(default=regex_max_results, description="Max matches to return", ge=1, le=200)
```

### 3. Функция генерации паттерна

```python
def _build_abbreviation_pattern(abbreviation: str) -> str:
    """Создает regex паттерн для поиска расшифровки аббревиатуры."""
    # Определение языка (кириллица/латиница)
    # Генерация паттерна для каждой буквы
    # Возврат regex-паттерна
```

### 4. Инструмент с извлечением уникальных расшифровок

```python
@tool(args_schema=FindAbbreviationExpansionInput)
def find_abbreviation_expansion(
    abbreviation: str, 
    max_results: int = regex_max_results
) -> AbbreviationExpansionResult:
    """Find expansions of abbreviations in knowledge base."""
    # Генерация паттерна
    # Поиск через regex_search
    # Извлечение уникальных расшифровок
    # Нормализация и сортировка
    # Возврат структурированного результата
```

**Ключевая логика извлечения:**
```python
# Извлекаем уникальные расшифровки из matched_text
expansions_set = set()
for match in search_result.matches[:max_results]:
    # Очищаем текст от лишних пробелов и нормализуем
    expansion = " ".join(match.match.split())
    if expansion:
        expansions_set.add(expansion)

# Преобразуем в отсортированный список
unique_expansions = sorted(list(expansions_set))
```

### 5. Регистрация

- ✅ Добавлен в `ALL_TOOLS`
- ✅ Добавлен в `AGENT_SELECTABLE_TOOLS`
- ✅ Добавлен в `create_kb_tools()` return list
- ✅ Добавлен в `get_tool_registry()`
- ✅ Обновлен module docstring

## Преимущества

1. **Чистый результат** - только уникальные расшифровки без дубликатов
2. **Автоматическая нормализация** - лишние пробелы удаляются
3. **Сортировка** - результаты отсортированы для удобства
4. **Структурированный ответ** - Pydantic модель с типизацией
5. **Удобство для агента** - список строк легко использовать в промптах

## Отличия от предыдущей версии

| Аспект | Было | Стало |
|--------|------|-------|
| **Тип результата** | `RegexSearchResult` | `AbbreviationExpansionResult` |
| **Данные** | Список `RegexMatch` с метаданными | Список уникальных строк |
| **Дубликаты** | Возможны | Автоматически удаляются |
| **Сортировка** | Нет | Да, алфавитная |
| **Нормализация** | Нет | Да, убираются лишние пробелы |

## Ограничения

- Аббревиатура должна содержать заглавные буквы и/или цифры
- Буквы в аббревиатуре определяют алфавит автоматически для каждого символа
- Между словами должен быть хотя бы один пробел
- Спецсимволы (кроме букв и цифр) не поддерживаются

## Возможные улучшения

1. **Гибкие разделители** - не только пробелы, но и дефисы, слэши
2. **Нечеткий поиск** - допуск на опечатки в расшифровке
3. **Частотный анализ** - ранжирование по частоте встречаемости расшифровки
4. **Поддержка спецсимволов** - C++, .NET и подобные
5. **Дедупликация по контексту** - исключение полностью идентичных чанков

## ⚠️ Breaking Changes (2026-04-29)

**Изменился формат результата:**

### Было (v1)
```python
result.expansions[0]  # строка: "Корпоративный Центр..."
```

### Стало (v2)
```python
result.expansions[0].expansion  # строка: "Корпоративный Центр..."
result.expansions[0].chunk      # ChunkResult с метаданными
```

**Миграция:**
- Агенты должны обращаться к `item.expansion` вместо напрямую к строке
- Доступны дополнительные данные через `item.chunk.metadata`

## Тестирование

Инструмент протестирован с примерами:
- ✅ КЦОИ → расшифровки с чанками
- ✅ RAM → ["Random Access Memory"] + chunks
- ✅ API → ["Application Programming Interface"] + chunks
- ✅ СУБД → ["Система Управления Базами Данных"] + chunks
- ✅ AK47 → ["Автомат Калашникова 47"] + chunks
- ✅ T34 → ["Танк 34"] + chunks
- ✅ MP5 → ["Maschinenpistole 5"] + chunks

## Статистика

- **Добавлено строк кода:** ~210 (v1: ~130, v2: +80)
- **Новых функций:** 2 (`_build_abbreviation_pattern`, `find_abbreviation_expansion`)
- **Новых Pydantic моделей:** 3 (`FindAbbreviationExpansionInput`, `AbbreviationExpansionItem`, `AbbreviationExpansionResult`)
- **Обновлено списков:** 4 (ALL_TOOLS, AGENT_SELECTABLE_TOOLS, return, registry)
- **Breaking changes:** 1 (формат результата)

## История изменений

### v2 (2026-04-29) - Чанки в результатах
- ✅ Добавлен `AbbreviationExpansionItem` с полем `chunk`
- ✅ Изменен тип `expansions: list[AbbreviationExpansionItem]`
- ✅ Алгоритм поиска соответствующего чанка в vectorstore
- ✅ Метаданные (source, section, line_start) для каждой расшифровки

### v1 (2026-04-29) - Первая версия
- ✅ Создание инструмента `find_abbreviation_expansion`
- ✅ Автоматическая генерация regex паттернов
- ✅ Поддержка кириллицы и латиницы
- ✅ Поддержка цифр в аббревиатурах (AK47, T34)
- ✅ Дедупликация результатов

