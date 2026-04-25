# RAG — Retrieval-Augmented Generation по документации

Консольный чат по корпусу документов `.md` с тремя режимами работы: базовый чат, многошаговый пайплайн и свободный агент с инструментами.

Стек: **Ollama** (LLM + эмбеддинги) · **ClickHouse** (векторное хранилище) · **LangChain 1.x / LangGraph 1.x** (оркестрация).

---

## Общий дизайн

### Слои системы

```
┌─────────────────────────────────────────────────────────────────────────┐
│  СЛОЙ ПОЛЬЗОВАТЕЛЯ                                                      │
│                                                                         │
│   rag_chat.py          rag_agent.py          rag_lc_agent.py            │
│   Базовый чат          Пайплайн-агент        LangGraph-агент            │
│   (1 поиск → LLM)      (фиксированные шаги)  (свободный выбор tools)   │
└────────┬───────────────────────┬──────────────────────┬─────────────────┘
         │                       │                      │
         │                       │              ┌───────▼────────────────┐
         │                       │              │  kb_tools.py           │
         │                       │              │  8 LangChain Tools:    │
         │                       │              │  semantic_search       │
         │                       │              │  exact_search          │
         │                       │              │  regex_search          │
         │                       │              │  read_table            │
         │                       │              │  get_section_content   │
         │                       │              │  list_sections         │
         │                       │              │  get_neighbor_chunks   │
         │                       │              │  list_sources          │
         └─────────────┬─────────┘              └───────┬────────────────┘
                       │                                │
         ┌─────────────▼────────────────────────────────▼─────────────────┐
         │  clickhouse_store.py  — ClickHouseVectorStore                  │
         │                                                                 │
         │  similarity_search()    cosineDistance(embedding, query_vec)   │
         │  exact_search()         positionCaseInsensitive(content, sub)  │
         │  get_neighbor_chunks()  line_start < anchor ORDER BY DESC      │
         │  clone()                независимый HTTP-клиент на поток       │
         └─────────────────────────────┬───────────────────────────────────┘
                                       │
         ┌─────────────────────────────▼───────────────────────────────────┐
         │  ClickHouse  soib_kcoi_v2.chunks                                │
         │                                                                 │
         │  ENGINE = ReplacingMergeTree ORDER BY (source, section,        │
         │           chunk_type, cityHash64(content))                     │
         │                                                                 │
         │  id · source · section · chunk_type · table_headers            │
         │  content · embedding(Array(Float32)) · line_start · line_end   │
         └─────────────────────────────────────────────────────────────────┘

         ┌─────────────────────────────────────────────────────────────────┐
         │  md_splitter.py  — индексация .md → Documents                  │
         │                                                                 │
         │  ""             prose size-split фрагмент (для поиска)         │
         │  paragraph_full полный блок (контекст)                         │
         │  table_row      одна строка таблицы как JSON-массив            │
         │  table_full     вся таблица raw (контекст)                     │
         │  table_raw      непарсируемая taблица verbatim                 │
         └─────────────────────────────────────────────────────────────────┘

         ┌─────────────────────────────────────────────────────────────────┐
         │  Ollama                                                         │
         │  LLM:         qwen3:8b  (генерация + tool calling)             │
         │  Эмбеддинги:  bge-m3   (1024-мерные векторы)                  │
         └─────────────────────────────────────────────────────────────────┘
```

### Три режима работы

| Режим | Файл | Стратегия | Когда использовать |
|---|---|---|---|
| **Базовый чат** | `rag_chat.py` | 1 semantic search → LLM | Быстрые концептуальные вопросы |
| **Пайплайн-агент** | `rag_agent.py` | Фиксированные шаги: анализ → N поисков → rerank → обогащение → синтез | Сложные вопросы, таблицы, списки |
| **LangGraph-агент** | `rag_lc_agent.py` | LLM сам выбирает из 8 tools и итерирует | Исследовательские вопросы, навигация по KB |

### Типы чанков в ClickHouse

Каждый `.md` файл парсится через `md_splitter.py` и создаёт несколько **взаимодополняющих** представлений:

```
Markdown-файл
│
├─ Заголовок H1 > H2 > H3  →  metadata.section (breadcrumb)
│
├─ Таблица
│   ├─ chunk_type="table_full"   — вся таблица raw text (для чтения контекста)
│   └─ chunk_type="table_row"    — 1 строка = 1 Document, content=JSON["v1","v2"]
│                                  metadata.table_headers=JSON["h1","h2"]
│
└─ Прозаический блок
    ├─ chunk_type="paragraph_full"  — полный блок (для контекста)
    └─ chunk_type=""                — size-split фрагменты ≤1500 символов (для поиска)
```

Это позволяет:
- Искать точные значения в таблицах (`table_row` + `exact_search`)
- Читать полные таблицы целиком (`table_full` + `get_section_content`)
- Балансировать точность/полноту при semantic search (оба типа индексируются)

### Поток данных при запросе

```
Вопрос пользователя
      │
      ▼ (rag_lc_agent.py)
LangGraph ReAct loop:
      │
      ├─ LLM выбирает tool → вызов → результат → снова LLM → ...
      │
      │  exact_search("БДКО")
      │    └─ ClickHouse: positionCaseInsensitive(content,"БДКО") → 5 чанков
      │
      │  semantic_search("база данных карточек объектов")
      │    └─ bge-m3 embed → cosineDistance в ClickHouse → top-10
      │
      │  get_section_content("file.md", "Раздел > Подраздел")
      │    └─ прямое чтение .md файла → полный текст секции
      │
      └─ финальный AIMessage с ответом
```

---

## Стек

| Компонент | Технология |
|---|---|
| LLM | `qwen3:8b` через Ollama |
| Эмбеддинги | `bge-m3` через Ollama |
| Векторное хранилище | ClickHouse (`ReplacingMergeTree`, cosineDistance) |
| Оркестрация пайплайна | LangChain 1.x (`langchain-ollama`) |
| Агент с инструментами | LangGraph 1.x (`create_react_agent`) |
| Конфигурация | `pydantic-settings` + `.env` |

---

## Файлы

| Файл | Назначение |
|---|---|
| `rag_chat.py` | Базовый RAG-чат: индексация, семантический поиск, regex-поиск; `Settings` (все env переменные) |
| `rag_agent.py` | Агентный RAG: многошаговый пайплайн поверх `rag_chat.py` |
| `kb_tools.py` | 8 LangChain Tools для доступа к KB (ClickHouse + .md файлы) |
| `rag_lc_agent.py` | LangGraph ReAct-агент: итеративный поиск, оценка полноты, cross-session память |
| `session_memory.py` | Cross-session память: ClickHouse `agent_memory`, recall/save/dedup |
| `llm_call_logger.py` | Файловый логгер LLM/tool/DB вызовов (`logs/_rag_llm.log`) + `LangChainFileLogger` |
| `clickhouse_store.py` | `ClickHouseVectorStore`: insert, similarity_search, exact_search, clone() |
| `md_splitter.py` | Парсер `.md` → LangChain Documents (prose + table_row + table_full) |
| `text_utils.py` | Нормализация текста перед эмбеддингом |
| `requirements.txt` | Зависимости Python |
| `.env` | Переменные окружения (не в репозитории) |

---

## Переменные окружения (`.env`)

```env
# LLM + эмбеддинги
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen3:8b
OLLAMA_EMBED_MODEL=bge-m3

# Источники знаний
KNOWLEDGE_DIR=Z:\ES-Leasing\СОИБ КЦОИ

# ClickHouse
CLICKHOUSE_HOST=localhost
CLICKHOUSE_PORT=8123
CLICKHOUSE_USERNAME=clickhouse
CLICKHOUSE_PASSWORD=clickhouse
CLICKHOUSE_DATABASE=soib_kcoi_v2
CLICKHOUSE_TABLE=chunks

# Поиск
RETRIEVER_TOP_K=10
RETRIEVER_SCORE_THRESHOLD=0.0
MAX_CONTEXT_CHARS=100000
RERANKER_TOP_N=15
MEMORY_MAX_TURNS=5

# Логирование LLM/tool вызовов в файл
LLM_LOG_ENABLED=true

# Cross-session память (rag_lc_agent.py)
AGENT_MEMORY_ENABLED=true
AGENT_MEMORY_TABLE=agent_memory
AGENT_MEMORY_MIN_SCORE=4
AGENT_MEMORY_MIN_TOOL_CALLS=2
AGENT_MEMORY_RECALL_SIM=0.80
AGENT_MEMORY_DEDUP_SIM=0.92
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

# Агентный чат (пайплайн, рекомендуется для сложных вопросов)
python rag_agent.py                         # интерактивный режим
python rag_agent.py "что такое КЦОИ"        # одиночный вопрос
python rag_agent.py --ips                   # все IP из документов в консоль
python rag_agent.py --ips ips_all.txt       # сохранить список IP в файл

# LangChain Tools агент (свободный агент с инструментами)
python rag_lc_agent.py                      # интерактивный режим
python rag_lc_agent.py "что такое КЦОИ"    # одиночный вопрос
python rag_lc_agent.py "все IP сети" --verbose  # показывать вызовы инструментов
```

---

## Архитектура `rag_chat.py` (базовый слой)

### Индексация документов

1. Сканирует `KNOWLEDGE_DIR/**/*.md` рекурсивно
2. Каждый файл парсится через **markdown-it-py** — структурный разбор AST:
   - Накапливает breadcrumb-путь (`H1 > H2 > H3`) как metadata `section`
   - Прозаические блоки → `paragraph_full` (полный блок) + `""` (size-split фрагменты)
   - GFM pipe-таблицы → `table_full` (полная таблица) + `table_row` (одна строка на Document)
   - Grid/RST таблицы → те же типы через fallback-парсер
3. Фильтрует пустые/бессмысленные чанки (< 20 символов или < 3 алфавитно-цифровых)
4. Загружает батчами по 100 в ClickHouse (`ReplacingMergeTree`)
   - `id` = детерминированный `uuid5(source, section, chunk_type, content)` → автоматическая дедупликация

### Поиск

- **Семантический**: ClickHouse cosineDistance по эмбеддингу запроса (`bge-m3`)
- **Exact**: ClickHouse `positionCaseInsensitive` — точное вхождение подстроки в content
- **Regex**: прямой grep по исходным `.md` файлам с N строками контекста вокруг совпадения
- Все SELECT используют `FINAL` — немедленная дедупликация до мёрджа

### Настройки

| Параметр | Дефолт | Описание |
|---|---|---|
| `chunk_size` | 1500 | Максимальный размер prose-чанка (символы) |
| `chunk_overlap` | 300 | Перекрытие между size-split фрагментами |
| `retriever_top_k` | 10 | Топ-K чанков при семантическом поиске |
| `retriever_score_threshold` | 0.0 | Порог косинусного расстояния (0 = отключён) |
| `max_context_chars` | 60 000 | Лимит контекста для LLM |
| `regex_context_lines` | 5 | Строк контекста вокруг regex-совпадения |

---

## Как `rag_agent.py` обрабатывает запрос пользователя

Вместо одного прямого поиска — многошаговый агентный пайплайн. Каждый шаг строится на результатах предыдущего.

```
┌─────────────────────────────────────────────────────────────────────┐
│                      Вопрос пользователя                            │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  ШАГ 0 · Pre-analysis  (детерминированный, без LLM)          ✅ вкл │
│  ШАГ 1 · analyze_query  (LLM → JSON-план)                    ✅ вкл │
│  ШАГ 2 · multi_retrieve  (семантика + exact + regex)          ✅ вкл │
│  ШАГ 2.5 · llm_rerank_docs  (Listwise LLM reranking)        ✅ вкл │
│  ШАГ 3 · enrich_with_neighbor_chunks  (merged_group)         ✅ вкл │
└──────────────────────────────┬──────────────────────────────────────┘
                               │  all_docs (итерация 1)
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  ШАГ 1.5 · evaluate_search_results  (LLM-оценка)             ✅ вкл │
│                                                                     │
│  LLM видит краткую сводку найденных чанков (source + section +      │
│  первые 300 символов) и отвечает на вопрос:                         │
│    "Достаточно ли данных для полного ответа?"                       │
│                                                                     │
│  Возвращает SearchEvaluation:                                       │
│    needs_more        true | false                                   │
│    reasoning         объяснение решения                             │
│    new_search_queries альтернативные формулировки (если needs_more) │
│    new_exact_terms   новые точные термины (если needs_more)         │
│    new_query_type    тип запроса для итерации 2                     │
└────────────────┬────────────────────────────┬───────────────────────┘
                 │ needs_more=false            │ needs_more=true
                 │                             ▼
                 │          ┌──────────────────────────────────────────┐
                 │          │  ИТЕРАЦИЯ 2                              │
                 │          │    ШАГ 2 · multi_retrieve (новые запросы)│
                 │          │    ШАГ 3 · enrich_with_neighbor_chunks   │
                 │          │    дедупликация: добавляем только новые  │
                 │          │    чанки (по content[:200])              │
                 │          └──────────────────┬───────────────────────┘
                 │                             │ new_docs
                 │                             ▼
                 └──────────────┬──────────────┘
                                │ all_docs = iter1 + iter2 (unique)
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│  ШАГ 4 · synthesize_answer  (LLM → финальный ответ)          ✅ вкл │
│                                                                     │
│  Синтез по всем чанкам из всех итераций                             │
│  Контекст обрезается до max_context_chars символов                  │
│  <think>...</think> блоки вырезаются                                │
│  История диалога обновляется                                        │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  AgentAnswer                                                        │
│    question          исходный вопрос                                │
│    analysis          план поиска итерации 1 (QueryAnalysis)         │
│    retrieved_chunks  итого чанков во всех итерациях                 │
│    iterations        1 или 2                                        │
│    answer            текст ответа                                   │
│    source_files      список источников                              │
│    found_sections    все (файл, раздел) для прозрачности            │
└─────────────────────────────────────────────────────────────────────┘
```

### Данные и хранилища

| Источник данных | Что запрашивается | На каком шаге | Статус |
|---|---|---|---|
| **Ollama LLM** (`qwen3:8b`) | JSON-план поиска | Шаг 1 | ✅ вкл |
| **ClickHouse** (cosineDistance) | top-K чанков по эмбеддингу | Шаг 2a | ✅ вкл |
| **ClickHouse** (positionCaseInsensitive) | чанки по точному вхождению | Шаг 2b | ✅ вкл |
| **Файловая система** (`.md` файлы) | regex-совпадения с контекстом | Шаг 2c/2d | ✅ вкл |
| **Flashrank** (cross-encoder, локально) | переранжирование пар вопрос↔чанк | Шаг 2.5 | ⏸ откл (заменён LLM) |
| **Ollama LLM** (`qwen3:8b`) | listwise ранжирование якорных чанков | Шаг 2.5 | ✅ вкл |
| **ClickHouse** (line_start соседи) | ±N чанков вокруг каждого найденного | Шаг 3 | ✅ вкл |
| **Файловая система** (`.md` файлы) | полный текст раздела | Шаг 3.5 | ⏸ откл |
| **Ollama LLM** (`qwen3:8b`) | оценка полноты результатов итерации 1 | Шаг 1.5 | ✅ вкл |
| **ClickHouse** (итерация 2) | повторный поиск по новым запросам | Шаги 2–3 iter2 | ✅ вкл |
| **Ollama LLM** (`qwen3:8b`) | финальный ответ по всем итерациям | Шаг 4 | ✅ вкл |

### Параметры

| Параметр | Дефолт | Где влияет |
|---|---|---|
| `retriever_top_k` | 10 | Шаг 2a: чанков на один semantic-запрос (×3 при list) |
| `reranker_top_n` | 15 | Шаг 2.5: якорных чанков после LLM reranking (идут в enrichment) |
| `enrich_before_chars` | 3000 | Шаг 3: символьный бюджет предшествующего контекста на якорь (не пересекает границу раздела) |
| `enrich_after_chars` | 1500 | Шаг 3: символьный бюджет последующего контекста на якорь (вдвое меньше) |
| `enrich_candidates` | 30 | Шаг 3: макс. кандидатов, запрашиваемых из ClickHouse в каждую сторону |
| `max_context_chars` | 100 000 | Шаг 4: лимит контекста LLM (символов) |
| `memory_max_turns` | 5 | Шаги 1, 4: глубина истории диалога |
| `chunk_size` | 1 500 | Индексация: макс. символов на чанк |
| `chunk_overlap` | 300 | Индексация: перекрытие между чанками |

### Команды в интерактивном режиме

| Ввод | Действие |
|---|---|
| Любой текст | Полный агентный пайплайн (шаги 0–4) |
| `ips` | Regex-скан всех .md файлов, список уникальных IP/подсетей |
| `ips path/to/file.txt` | То же, сохранить список в файл |
| `/reset` | Очистить историю диалога (ConversationBuffer) |
| `exit` / `quit` / `выход` | Выйти |

### Детали шага 3 — обогащение соседними чанками

Для каждого якорного чанка (якорь = результат семантического поиска после reranking):

**Preceding context (до якоря)**:
1. Из ClickHouse загружается до `enrich_candidates` (30) предыдущих чанков по `line_start`.
2. Идём от ближайшего к якорю назад — проверяем **два условия остановки**:
   - `chunk.section != anchor.section` — **граница подраздела**: дальше не идём,  
     даже если бюджет символов не исчерпан. Окно всегда ограничено текущим подразделом.
   - `budget (before_chars) ≤ 0` — лимит символов исчерпан.
3. Если раздел короче `before_chars` — берётся весь раздел целиком (естественная остановка).
4. Если раздел длиннее — обрезаются самые дальние от якоря чанки.

**Following context (после якоря)**:  
Берётся до `after_chars` символов вперёд без ограничения по разделу.

**Склейка**:  
Якорь + отобранные соседи сортируются по `line_start` и склеиваются в один `Document`  
с `chunk_type="merged_group"`. Смежные группы разных якорей из одного файла  
объединяются в единый блок. Non-positional чанки (regex, `line_start==0`) — без изменений.


## LangChain Tools агент (`rag_lc_agent.py`)

Вместо жёсткого пайплайна (`rag_agent.py`) — **LangGraph ReAct-агент** с итеративным уточнением:
LLM сам выбирает из 8 инструментов, а внешняя Python-оболочка оценивает полноту ответа
и запускает следующий раунд, если данных недостаточно.

### Схема обработки одного запроса

```
Вопрос пользователя
        │
        ▼
┌──────────────────────────────────────────────────────────────────────┐
│  СТАДИЯ 0 · Cross-session memory recall                              │
│                                                                       │
│  SessionMemory.recall(question, top_k=3)                             │
│    cosineDistance(question_embedding, stored) в ClickHouse           │
│    порог сходства: recall_sim=0.80                                   │
│                                                                       │
│  Если найдено похожее → формируется ПОДСКАЗКА:                       │
│    💡 [файл] — [раздел] (сработало в прошлый раз)                    │
│    Запросы которые сработали: semantic_search("..."), ...            │
│    ↓ подсказка вставляется перед вопросом в раунде 1                 │
└──────────────────────────────┬───────────────────────────────────────┘
                               │
        ┌──────────────────────▼──────────────────────┐
        │         ИТЕРАТИВНЫЙ ЦИКЛ  (≤ max_rounds=4)  │
        └──────────────────────┬──────────────────────┘
                               │
        ┌──────────────────────▼──────────────────────────────────────┐
        │  СТАДИЯ 1 · LangGraph ReAct round  (один раунд)             │
        │                                                              │
        │  agent.invoke(messages=[chat_history + HumanMessage])        │
        │                                                              │
        │  LLM (qwen3:8b) видит:                                       │
        │    • system_prompt  — правила + список файлов KB             │
        │    • chat_history   — до N последних пар H/AI                │
        │    • HumanMessage   — вопрос (раунд 1) или                   │
        │                       обогащённый контекст (раунды 2+)       │
        │                                                              │
        │  LLM выбирает tools → вызов → результат → снова LLM → ...   │
        │   semantic_search("...") → bge-m3 + cosineDistance в CH     │
        │   exact_search("...") → positionCaseInsensitive в CH        │
        │   get_section_content("file.md", "Раздел") → чтение .md    │
        │   read_table("Раздел") → table_row через CH                 │
        │   list_sections("file.md") → DISTINCT section из CH         │
        │   regex_search(r"...") → grep по .md файлам                 │
        │   get_neighbor_chunks(source, line_start) → ±N чанков       │
        │   list_sources() → GROUP BY source в CH                     │
        │                                                              │
        │  → финальный AIMessage с текстом ответа                      │
        └──────────────────────┬──────────────────────────────────────┘
                               │
        ┌──────────────────────▼──────────────────────────────────────┐
        │  СТАДИЯ 2 · Оценка полноты (_evaluate_round)                 │
        │                                                              │
        │  Отдельный LLM-вызов (qwen3:8b, /no_think):                 │
        │    • исходный вопрос                                         │
        │    • краткая сводка всех tool-выводов раунда                 │
        │  → RoundEvaluation { is_complete, score 1-5,                │
        │                      missing, refined_query }               │
        │                                                              │
        │  if score ≥ 4 or is_complete → СТОП, перейти к сохранению   │
        │  else    → СТАДИЯ 3 (формируем следующий раунд)             │
        └──────────────────────┬──────────────────────────────────────┘
                               │  score < 4
        ┌──────────────────────▼──────────────────────────────────────┐
        │  СТАДИЯ 3 · Формирование запроса следующего раунда           │
        │            (_build_next_round_query)                         │
        │                                                              │
        │  Структура запроса:                                          │
        │  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━    │
        │  ИСХОДНЫЙ ВОПРОС ПОЛЬЗОВАТЕЛЯ (не меняется):                 │
        │  <оригинальный вопрос>                                       │
        │  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━    │
        │  УЖЕ ВЫПОЛНЕНО (не повторять):                               │
        │    Раунд 1: semantic_search({...}); exact_search({...})      │
        │    Раунд 2: list_sections({...}); get_section_content({...}) │
        │  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━    │
        │  ЧТО БЫЛО НАЙДЕНО ПО ШАГАМ:                                  │
        │    --- Раунд 1 --- [tool outputs] [частичный ответ]          │
        │    --- Раунд 2 --- [tool outputs] [частичный ответ]          │
        │  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━    │
        │  ЧТО НЕ ХВАТАЕТ / ЗАДАЧА: <refined_query от оценщика>        │
        │                                                              │
        │  → возврат в СТАДИЯ 1 (следующий раунд)                      │
        └──────────────────────┬──────────────────────────────────────┘
                               │ (после последнего раунда)
        ┌──────────────────────▼──────────────────────────────────────┐
        │  СТАДИЯ 4 · Сохранение в cross-session память                │
        │                                                              │
        │  Критерии сохранения (все три должны быть выполнены):        │
        │    1. score ≥ min_score (default 4) — ответ полный          │
        │    2. tool_calls ≥ min_tool_calls (default 2) — нетривиально│
        │    3. нет дубликата в памяти (cosine < dedup_sim = 0.92)    │
        │                                                              │
        │  Что сохраняется:                                            │
        │    question + question_embedding (для recall)                │
        │    answer  — лучший финальный ответ                          │
        │    key_sections — [(source, section), ...] полезные разделы  │
        │    effective_tools — [{name, args}, ...] что сработало       │
        │    sources, rounds, score, tool_calls_count                  │
        └──────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
                        Ответ пользователю
```

### Что логируется в `logs/_rag_llm.log`

Каждый LLM-вызов и каждый вызов инструмента пишется в файл через `LangChainFileLogger`:

```
=== #001 2026-04-25 17:51:25 [LLM_CALL] REQUEST ===
Model: ChatOllama
[SYSTEM] <system_prompt>
[HUMAN] нужен перечень программного обеспечения
--- end #001 REQUEST ---

=== #001 2026-04-25 17:51:38 [LLM_CALL] RESPONSE ===
[tool_calls]
[{"name": "semantic_search", "args": {"query": "состав ПО"}}]
--- end #001 RESPONSE ---

=== #002 2026-04-25 17:51:38 [TOOL:semantic_search] REQUEST ===
{"query": "состав ПО", "top_k": 10}
--- end #002 RESPONSE ---

=== #003 2026-04-25 17:51:41 [DB:semantic_search] REQUEST ===
query='состав программного обеспечения'   ← параметры запроса к ClickHouse
--- end #003 REQUEST ---

=== #003 2026-04-25 17:51:41 [DB:semantic_search] RESPONSE ===
Найдено 10 чанков                          ← количество + превью результатов
--- end #003 RESPONSE ---
```

Каждый блок `[LLM_CALL]`, `[TOOL:*]`, `[DB:*]` имеет уникальный номер `#NNN` — REQUEST и RESPONSE одного вызова можно найти по одному номеру.

### Cross-session память (`session_memory.py`)

Хранится в ClickHouse таблице `soib_kcoi_v2.agent_memory`:

```sql
-- Просмотр всех записей
SELECT id, created_at, question, score, rounds, tool_calls_count
FROM soib_kcoi_v2.agent_memory ORDER BY created_at DESC LIMIT 20;

-- Поиск по теме
SELECT question, answer
FROM soib_kcoi_v2.agent_memory
WHERE positionCaseInsensitive(question, 'ИП-адрес') > 0;

-- Удалить запись
ALTER TABLE soib_kcoi_v2.agent_memory DELETE WHERE id = '<uuid>';
```

### Инструменты (`kb_tools.py`)

| Инструмент | Метод / источник | Когда агент использует |
|---|---|---|
| `semantic_search` | `cosineDistance` (bge-m3 эмбеддинги) | Концептуальные вопросы, "что такое X", общий поиск по смыслу |
| `exact_search` | `positionCaseInsensitive` в ClickHouse | Конкретные термины, аббревиатуры, названия систем |
| `regex_search` | grep по `.md` файлам (RE2) | IP-адреса, порты, ВЛАНы, коды документов |
| `read_table` | `chunk_type IN (table_row, table_full)` + section filter | Чтение данных из таблиц по разделу |
| `get_section_content` | Прямое чтение `.md` файла по заголовку | Полный текст раздела (таблицы + списки целиком) |
| `list_sections` | `SELECT DISTINCT source, section` из ClickHouse | Навигация по структуре KB, поиск точного названия раздела |
| `get_neighbor_chunks` | `line_start < anchor ORDER BY line_start` | Расширение контекста вокруг найденного чанка |
| `list_sources` | `SELECT source, count() GROUP BY source` | Обзор доступных файлов в KB |

Все инструменты используют `vectorstore.clone()` — независимое HTTP-соединение к ClickHouse на каждый вызов, что обеспечивает безопасное параллельное выполнение (LangGraph запускает tool calls параллельно через `concurrent.futures`).

### Настройки агента

| Параметр | Дефолт | Описание |
|---|---|---|
| `retriever_top_k` | 10 | Топ-K чанков в `semantic_search` |
| `memory_max_turns` | 5 | Глубина скользящего буфера `chat_history` |
| `llm_log_enabled` | `true` | Писать LLM/tool вызовы в `logs/_rag_llm.log` |
| `agent_memory_enabled` | `true` | Включить cross-session память |
| `agent_memory_table` | `agent_memory` | Имя таблицы памяти (в той же БД) |
| `agent_memory_min_score` | `4` | Минимальная оценка полноты для сохранения |
| `agent_memory_min_tool_calls` | `2` | Минимум tool calls (нетривиальный вопрос) |
| `agent_memory_recall_sim` | `0.80` | Порог сходства для recall (0..1) |
| `agent_memory_dedup_sim` | `0.92` | Порог дедупликации (выше = не сохранять) |

### Команды интерактивного режима

| Ввод | Действие |
|---|---|
| Любой текст | Полный итеративный цикл (до 4 раундов) + сохранение в память |
| `/reset` | Очистить `chat_history` текущей сессии |
| `/verbose` | Показывать/скрывать детали tool calls в консоли |
| `/memory` | Показать количество записей в памяти + SQL для просмотра |
| `exit` / `quit` / `выход` | Выйти |

### Отличие от `rag_agent.py`

| Параметр | `rag_agent.py` (пайплайн) | `rag_lc_agent.py` (LangGraph + память) |
|---|---|---|
| Стратегия поиска | Фиксированный пайплайн шагов 0–4 | LLM сам решает какие tools вызвать |
| Количество LLM-вызовов | Фиксировано (analyze + [rerank] + evaluate + synthesize) | Переменное: N tool selections + 1 eval + возможен rerun |
| Итерации | До 2 итераций поиска (автоматически) | До 4 раундов с полным контекстом и памятью предыдущих |
| Долгосрочная память | Нет | ClickHouse `agent_memory` — cross-session hints |
| Логирование | `logger.info/debug` | Файл `logs/_rag_llm.log` (каждый LLM + tool + DB запрос) |
| Параллельность tools | Нет (sequential pipeline) | Да (LangGraph `concurrent.futures`) |
| Прозрачность | Детальные логи каждого шага | `--verbose` + `_rag_llm.log` |



---

## Roadmap улучшений

### ✅ Реализовано

| # | Улучшение | Файл |
|---|---|---|
| 1 | **Стриппинг `<think>` блоков** | `rag_agent.py`, `rag_lc_agent.py` |
| 2 | **Параллельный семантический поиск** | `rag_agent.py` (`ThreadPoolExecutor`) |
| 3 | **Ограничение контекста** | `rag_chat.py` (`max_context_chars`) |
| 4 | **Score threshold** | `rag_chat.py` (`retriever_score_threshold`) |
| 5 | **LLM Reranking (listwise)** | `rag_agent.py` (шаг 2.5) |
| 6 | **Conversation Memory** | `rag_agent.py`, `rag_lc_agent.py` (скользящий буфер) |
| 7 | **Exact-term search** | `clickhouse_store.py` (`positionCaseInsensitive`) |
| 8 | **LangGraph ReAct-агент с 8 tools** | `rag_lc_agent.py` + `kb_tools.py` |
| 9 | **Итеративный поиск (до 4 раундов)** | `rag_lc_agent.py` (`run_with_iterations`) |
| 10 | **Оценка полноты между раундами** | `rag_lc_agent.py` (`_evaluate_round`) |
| 11 | **Накопление контекста между раундами** | `rag_lc_agent.py` (`_build_next_round_query`) |
| 12 | **Cross-session память в ClickHouse** | `session_memory.py` |
| 13 | **Файловое логирование LLM/tool/DB** | `llm_call_logger.py` (`LangChainFileLogger`) |
| 14 | **Thread-safe ClickHouse (clone per call)** | `clickhouse_store.py`, `kb_tools.py` |

---

### 🔲 В планах

**Стриминг ответа в консоль**
Синтез занимает 10–30 сек тишины. `ChatOllama` поддерживает `streaming=True`.

**Семантический кеш запросов**
Если похожий вопрос уже задавался (similarity > 0.95) — вернуть ответ из памяти без LLM.

**Валидация ответа (Self-RAG)**
После синтеза — дополнительный LLM-вызов: "Подтверждён ли каждый факт ответа найденными чанками?"

---

## Источники знаний

- **Папка**: `Z:\ES-Leasing\СОИБ КЦОИ`
- **Формат**: `.md` (сконвертировано из `.docx`)
- **Поддерживаются** вложенные папки (`**/*.md`)

---

## Требования к формату исходных Markdown-файлов

Файлы конвертируются из `.docx` через **Pandoc**. Для максимальной совместимости с парсером используйте следующие правила и флаги.

### Пример конвертации одного файла

```powershell
pandoc "Общее описание системы-(ИИ-26).docx" `
  -o "Общее описание системы-(ИИ-26).md" `
  --to=markdown+grid_tables `
  --wrap=none `
  --markdown-headings=atx `
  --extract-media=./media
```

### Пакетная конвертация всех .docx в папке (PowerShell)

```powershell
$srcDir = "C:\docs\source"
$outDir = "C:\docs\md"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

Get-ChildItem -Path $srcDir -Filter "*.docx" | ForEach-Object {
    $out = Join-Path $outDir ($_.BaseName + ".md")
    pandoc $_.FullName -o $out `
        --to=markdown+grid_tables `
        --wrap=none `
        --markdown-headings=atx `
        --extract-media="$outDir/media"
    Write-Host "Конвертирован: $($_.Name) → $out"
}
```

### Рекомендуемые флаги Pandoc

```powershell
pandoc input.docx -o output.md `
  --to=markdown+grid_tables `
  --wrap=none `
  --markdown-headings=atx `
  --extract-media=./media
```

Флаг `--extract-media` сохраняет изображения из `.docx` в указанную папку (не обязателен если изображения не нужны).

Альтернатива с pipe-таблицами (без поддержки многострочных ячеек):
```powershell
pandoc input.docx -o output.md `
  --to=markdown `
  --wrap=none `
  --markdown-headings=atx `
  --table-style=pipe
```

### Заголовки (обязательно ATX-стиль)

✅ **Поддерживается:**
```markdown
# Заголовок первого уровня
## Заголовок второго уровня
### Заголовок третьего уровня
#### Заголовок четвёртого уровня
```

❌ **НЕ поддерживается** (Setext-style — подчёркивание `===` / `---`):
```markdown
Заголовок
=========
```

Флаг Pandoc: `--markdown-headings=atx`

### Таблицы

Поддерживаются **два формата**:

#### 1. Pipe-таблицы (Markdown)

```markdown
| Столбец 1 | Столбец 2 | Столбец 3 |
|-----------|-----------|-----------|
| значение  | значение  | значение  |
```

Флаг Pandoc: `--table-style=pipe`  
Подходит для простых таблиц без объединения ячеек.

#### 2. Grid-таблицы (RST/Pandoc)

```
+------------+----------+------------------+
| Столбец 1  | Столбец 2| Столбец 3        |
+============+==========+==================+
| значение   | значение | значение         |
+------------+----------+------------------+
```

Флаг Pandoc: расширение `+grid_tables` или `--to=markdown+grid_tables`  
Поддерживает **многострочные ячейки** и вертикальные объединения (текст конкатенируется).

#### Важно для таблиц

- Каждая строка таблицы индексируется как **отдельный чанк** с key-value представлением вида `Столбец: значение`
- Это позволяет семантически искать по конкретным значениям (IP-адреса, имена БД, коды и т.д.)
- **Не рекомендуется** использовать `--table-style=multiline` (Pandoc multiline tables) — не поддерживается парсером

### Перенос строк

Используйте `--wrap=none` чтобы отключить автоматический перенос длинных строк внутри абзацев и ячеек таблиц. Без этого флага Pandoc вставляет жёсткие переносы, которые разрывают ячейки таблиц при парсинге.

### Кодировка

Все файлы должны быть в **UTF-8**. Pandoc использует UTF-8 по умолчанию.

### Структура заголовков и поиск

- Заголовки образуют «хлебные крошки» (`H1 > H2 > H3`) — это **metadata.section** каждого чанка
- LLM получает подсказку: если ключевые термины вопроса совпадают с заголовком раздела — тот чанк считается приоритетным
- При list-запросах агент дополнительно вычитывает **все чанки найденных секций** напрямую из файлов — глубина вложенности заголовков не ограничена

