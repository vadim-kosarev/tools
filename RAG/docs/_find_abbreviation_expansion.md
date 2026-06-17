# find_abbreviation_expansion

Инструмент для поиска расшифровки аббревиатур в базе знаний.

**Файл:** `RAG/kb_tools.py`

## Принцип работы

По аббревиатуре автоматически генерируется regex-паттерн: каждая буква → начало слова
с любыми последующими буквами; между словами допускаются пробелы и знаки препинания.
Поиск выполняется по исходным `.md`-файлам через `regex_search`.

| Аббревиатура | Паттерн | Результат |
|--------------|---------|-----------|
| `КЦОИ` | `К[а-яё]*\s+Ц[а-яё]*\s+О[а-яё]*\s+И[а-яё]*` | `"Корпоративный Центр Обработки Информации"` |
| `RAM` | `R[a-z]*\s+A[a-z]*\s+M[a-z]*` | `"Random Access Memory"` |
| `AK47` | `A[a-z]*\s+K[a-z]*\s+47` | цифры — точное совпадение |

Поддерживается кириллица, латиница и цифры. Язык каждой буквы определяется автоматически.

## Результат

```python
class AbbreviationExpansionResult(BaseModel):
    abbreviation: str                           # исходная аббревиатура
    expansions: list[AbbreviationExpansionItem] # расшифровки с чанками
    total_found: int                            # всего найдено
    pattern_used: str                           # использованный regex

class AbbreviationExpansionItem(BaseModel):
    expansion: str      # текст расшифровки
    chunk: ChunkResult  # чанк с метаданными (source, section, line_start, ...)
```

Дубликаты автоматически удаляются, результаты сортируются.

## Использование через CLI

```powershell
python kb_tools.py run find_abbreviation_expansion abbreviation=КЦОИ
python kb_tools.py run find_abbreviation_expansion abbreviation=RAM max_results=20
```

## Использование через Python

```python
from kb_tools import create_kb_tools

tools = create_kb_tools(vectorstore, knowledge_dir)
find_abbr = next(t for t in tools if t.name == "find_abbreviation_expansion")

result = find_abbr.invoke({"abbreviation": "КЦОИ"})
for item in result.expansions:
    print(item.expansion, "—", item.chunk.metadata.source)
```

## Параметры

| Параметр | Тип | По умолчанию | Описание |
|----------|-----|-------------|---------|
| `abbreviation` | str | — | Аббревиатура заглавными буквами |
| `max_results` | int | 50 | Максимум совпадений для regex-поиска |
