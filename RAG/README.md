# RAG — Retrieval-Augmented Generation по документации

Консольный чат-агент для работы с корпусом документов в формате `.md`.  
Архитектура: **Ollama** (LLM + эмбеддинги) + **ChromaDB** (векторное хранилище) + **LangChain** (оркестрация).

---

## Стек

| Компонент | Технология |
|---|---|
| LLM | `qwen3:8b` через Ollama |
| Эмбеддинги | `bge-m3` через Ollama |
| Векторное хранилище | ChromaDB (HTTP-сервер, Docker) |
| Оркестрация | LangChain (`langchain-ollama`, `langchain-chroma`) |
| Конфигурация | `pydantic-settings` + `.env` |

---

## Файлы

| Файл | Назначение |
|---|---|
| `rag_chat.py` | Базовый RAG-чат: индексация, семантический поиск, regex-поиск |
| `rag_agent.py` | Агентный RAG: многошаговый пайплайн поверх `rag_chat.py` |
| `requirements.txt` | Зависимости Python |
| `.env` | Переменные окружения (не в репозитории) |

---

## Переменные окружения (`.env`)

```env
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen3:8b
OLLAMA_EMBED_MODEL=bge-m3
KNOWLEDGE_DIR=Z:\ES-Leasing\СОИБ КЦОИ
CHROMA_HOST=localhost
CHROMA_PORT=3266
CHROMA_COLLECTION=soib_kcoi_v2
RETRIEVER_TOP_K=10
RETRIEVER_SCORE_THRESHOLD=0.0
MAX_CONTEXT_CHARS=60000
```

---

## Запуск

```powershell
# Активировать venv
.\.venv\Scripts\Activate.ps1

# Базовый чат
python rag_chat.py                          # интерактивный режим
python rag_chat.py "что такое КЦОИ"         # одиночный вопрос
python rag_chat.py --reindex                # переиндексировать документы
python rag_chat.py --regex "\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}"  # regex-поиск

# Агентный чат (рекомендуется)
python rag_agent.py                         # интерактивный режим
python rag_agent.py "что такое КЦОИ"        # одиночный вопрос
python rag_agent.py --ips                   # все IP из документов в консоль
python rag_agent.py --ips ips_all.txt       # сохранить список IP в файл
```

---

## Архитектура `rag_chat.py` (базовый слой)

### Индексация документов

1. Сканирует `KNOWLEDGE_DIR/**/*.md` рекурсивно
2. Разбивает каждый файл на чанки с учётом заголовков `#`/`##`/`###`/`####`:
   - Накапливает "хлебные крошки" (`H1 > H2 > H3`) как metadata `section`
   - Секции длиннее `chunk_size` (1500 символов) дополнительно дробятся с `overlap` (300 символов)
3. Фильтрует пустые/бессмысленные чанки (< 20 символов или < 3 алфавитно-цифровых)
4. Загружает батчами по 50 в ChromaDB через HTTP-клиент
5. При ошибке батча — пробует по одному документу

### Поиск

- **Семантический**: через ChromaDB `similarity` или `similarity_score_threshold` (если `RETRIEVER_SCORE_THRESHOLD > 0`)
- **Regex**: прямой поиск по исходным `.md` файлам с N строками контекста вокруг совпадения

### Настройки

| Параметр | Дефолт | Описание |
|---|---|---|
| `chunk_size` | 1500 | Максимальный размер чанка (символы) |
| `chunk_overlap` | 300 | Перекрытие между чанками |
| `retriever_top_k` | 10 | Топ-K чанков при семантическом поиске |
| `retriever_score_threshold` | 0.0 | Порог релевантности (0 = отключён) |
| `max_context_chars` | 60 000 | Лимит контекста для LLM (~40K токенов) |
| `regex_context_lines` | 5 | Строк контекста вокруг regex-совпадения |

---

## Архитектура `rag_agent.py` (агентный пайплайн)

Вместо одного прямого поиска — четырёхшаговый агентный цикл:

```
Вопрос пользователя
        │
        ▼
[Шаг 0] Pre-analysis (детерминированный, без LLM)
        Regex-детектирование точных значений в тексте вопроса:
        IPv4, FQDN, номера документов, hex-коды, порты, VLAN
        │
        ▼
[Шаг 1] analyze_query — LLM строит план поиска
        → query_type: factual | list | comparison | pattern_search
        → key_terms: ключевые термины с раскрытыми аббревиатурами
        → search_queries: 2–4 перефразировки для семантического поиска
        → regex_patterns: дополнительные паттерны (при pattern_search)
        │
        ▼
[Шаг 2] multi_retrieve — параллельный поиск (ThreadPoolExecutor)
        ├─ N семантических поисков (по каждой перефразировке, параллельно)
        ├─ Forced regex (из pre-analysis — всегда, независимо от LLM)
        └─ LLM regex (из плана шага 1 — только при pattern_search)
        + Дедупликация по первым 200 символам page_content
        + Обрезка до MAX_CONTEXT_CHARS символов
        │
        ▼
[Шаг 3] synthesize_answer — LLM формирует финальный ответ
        Контекст = чанки с заголовками [файл] — раздел
        Ответ очищается от <think>...</think> блоков (qwen3/deepseek-r1)
        │
        ▼
AgentAnswer { question, analysis, retrieved_chunks, answer, source_files }
```

### Особенности реализации

**Стриппинг `<think>` блоков**  
`qwen3` и `deepseek-r1` генерируют `<think>...</think>` блоки в ответе.  
Утилита `_strip_think_tags()` удаляет их на двух этапах: при парсинге JSON плана поиска и в финальном ответе.

**Параллельный семантический поиск**  
`ThreadPoolExecutor` с `max_workers = len(search_queries)` — все перефразировки ищутся одновременно. Ошибки отдельных futures не роняют весь поиск.

**Ограничение контекста**  
`synthesize_answer` добавляет чанки в контекст пока не достигнут `max_context_chars`. При обрезке логируется `WARNING` с количеством использованных чанков.

**Score threshold**  
Если `RETRIEVER_SCORE_THRESHOLD > 0` — `multi_retrieve` переключает ChromaDB retriever в режим `similarity_score_threshold`, отфильтровывая нерелевантные чанки.

**Pre-analysis паттерны**

| Тип | Паттерн |
|---|---|
| IPv4 | `\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?:/\d{1,2})?\b` |
| FQDN/host | `\b(?:[a-zA-Z0-9-]+\.){2,}[a-zA-Z]{2,}\b` |
| Номер документа | `\b[А-ЯA-Z]{2,}-\d+(?:\.\d+)*\b` |
| Hex-код | `\b0x[0-9A-Fa-f]{4,}\b` |
| Порт | `(?:порт\|port)\s*:?\s*(\d{2,5})\b` |
| VLAN | `(?:vlan\|влан)\s*:?\s*(\d+)\b` |

### Команды в интерактивном режиме

| Ввод | Действие |
|---|---|
| Любой текст | Агентный цикл: анализ → поиск → синтез |
| `ips` | Вывести все IP/подсети из документов |
| `ips path/to/file.txt` | Сохранить список IP в файл |
| `exit` / `quit` / `выход` | Выйти |

---

## Источники знаний

- **Папка**: `Z:\ES-Leasing\СОИБ КЦОИ`
- **Формат**: `.md` (сконвертировано из `.docx`)
- **Поддерживаются** вложенные папки (`**/*.md`)
