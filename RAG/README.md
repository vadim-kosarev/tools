# RAG - агент по локальной документации

RAG-агент и инструменты работы с корпусом документов **.docx** (и `.md`).
Всё работает локально: LLM и эмбеддинги — через Ollama, хранилище — ClickHouse,
наружу не уходит ничего. Те же инструменты публикуются по протоколу **MCP**,
поэтому базой знаний может пользоваться и внешний AI-клиент.

## Стек технологий

- **Документы**: python-docx (.docx), markdown-it-py (.md)
- **Embeddings**: bge-m3 через Ollama (1024-мерные векторы)
- **Vector Store**: ClickHouse с cosineDistance
- **Агент**: tool-calling поверх LangChain-инструментов, локальная LLM в Ollama
- **MCP**: официальный MCP SDK, два транспорта (Streamable HTTP + stdio)

```
документы .docx/.md
      |  docx_splitter.py / md_splitter.py  (общий контракт чанка — chunking.py)
      v
  ClickHouse (chunks + chunks_sections)
      ^
      |  kb_tools.py — 13 инструментов
      |
  rag_agent.py (локальная LLM)     kb_tools_mcp_http.py / _stdio.py (внешние клиенты)
```

## Быстрый старт

```powershell
# Виртуальное окружение (venv в корне репозитория)
..\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 1. Проиндексировать корпус из KNOWLEDGE_DIR
python rag_chat.py --reindex

# 2. Спросить агента
python rag_agent.py ask "какие серверы входят в состав системы"
python rag_agent.py chat                    # интерактивный режим
python rag_agent.py tools                   # список инструментов

# Прямой вызов инструментов без LLM
python kb_tools.py list
python kb_tools.py run exact_search substring=КЦОИ limit=10
```

## Агент

`rag_agent.py` — tool-calling агент с ограниченным циклом: LLM сама выбирает
инструменты, выполняет их против ClickHouse и формирует ответ. После
`AGENT_MAX_ITERATIONS` раундов агент обязан ответить без вызова инструментов,
поэтому зациклиться он не может.

```powershell
python rag_agent.py ask "что такое КЦОИ" --show-tools   # с трассой вызовов
python rag_agent.py --debug ask "..."                   # DEBUG-логи
python rag_agent.py --env ..\other.env chat             # другой .env
```

В ответе агент печатает разделы-источники, попавшие в контекст, — по ним видно,
откуда взяты факты.

Агент подгоняет аргументы от модели под схему инструмента: снимает кавычки со
значений и исправляет придуманное имя параметра, когда оно однозначно (`term` →
`query`). Если вызов всё же не прошёл, модель получает список параметров
инструмента и повторяет попытку.

| Переменная | Назначение |
|-----------|-----------|
| `AGENT_MAX_ITERATIONS` | Максимум раундов вызова инструментов (6) |
| `AGENT_MAX_TOOL_CHARS` | Предел символов результата инструмента, отдаваемого LLM (12000) |
| `OLLAMA_NUM_PREDICT` | Максимум токенов в ответе (2048) |
| `OLLAMA_REASONING` | `true` — разрешить модели блок рассуждений (по умолчанию выключен) |
| `OLLAMA_TIMEOUT` | Таймаут запроса к Ollama в секундах (300) |

### Про рассуждения и скорость

`OLLAMA_REASONING` по умолчанию `false`, и это важно. Модели вроде `qwen3.5`
пишут блок рассуждений в отдельное поле ответа, расходуя на него бюджет
`num_predict`: замер на 9B при ~10 токенах/с показал, что 300 сгенерированных
токенов целиком ушли в рассуждения, а текст ответа так и не начался. Внешне это
выглядит как зависание. С выключенными рассуждениями ответ на тот же вопрос
приходит за десятки секунд.

`rag_chat.py` печатает ответ потоком, по мере генерации — на локальной модели это
единственный способ отличить работу от зависания.

## Индексация документов

```powershell
python rag_chat.py --reindex             # пересоздать таблицу и проиндексировать заново
python kb_tools.py build-section-index   # только индекс названий разделов
```

Обходятся все `**/*.docx` и `**/*.md` в `KNOWLEDGE_DIR`; временные файлы Word
(`~$имя.docx`) пропускаются. Ошибка одного документа не останавливает индексацию.

Что распознаётся в `.docx`:
- иерархия разделов по стилям заголовков, включая корпоративные
  (`Заголовок 2`, `Приложение: Заголовок 2`) и `w:outlineLvl` для нестандартных стилей;
- служебные заголовки (`Содержание`, `Оглавление`) не попадают в путь раздела;
- таблицы — построчно (`table_row`: значения в `content`, имена колонок в
  `table_headers` — они подмешиваются в эмбеддинг и в выдачу инструментов)
  плюс целиком (`table_full`); строка заголовков определяется по оформлению
  (флаг Word «повторять как заголовок», жирный, заливка), а не по позиции, поэтому
  в глоссариях без шапки первая запись не теряется; объединённые и пустые
  заголовки нормализуются, чтобы ни одна ячейка не пропала;
- списки — с сохранением маркеров;
- документы с битыми связями пакета (`Target="NULL"`) чинятся в памяти, исходник не меняется.

Для `.md`: ATX-заголовки, pipe-таблицы (GFM), grid-таблицы (RST), вложенные папки.

## Инструменты базы знаний (13)

### Поиск по содержимому
- `semantic_search` — поиск по эмбеддингам (концептуальные вопросы)
- `exact_search` — case-insensitive поиск по подстроке (UTF-8, включая кириллицу)
- `multi_term_exact_search` — поиск по списку терминов с ранжированием по покрытию
- `regex_search` — RE2-regex по содержимому чанков (IP, порты, коды)
- `search_abbreviation` — расшифровка аббревиатур (КЦОИ, RAM, API)

### Поиск по названиям разделов
- `search_section_by_name` — четыре сигнала: подстрока в названии, семантика,
  нечёткое совпадение (ngram), термины в содержимом

### Чтение контента
- `get_section_content` — полный текст раздела
- `read_table` — строки таблицы по названию раздела
- `get_neighbor_chunks` — соседние чанки вокруг найденного фрагмента
- `get_chunks_by_index` — чанки по индексам (source, section, chunk_indices)

### Навигация
- `list_sources` — список файлов с количеством чанков
- `list_sections` — дерево разделов базы знаний
- `list_all_sections` — уникальные пары (source, section)

## MCP Server

Те же 13 инструментов публикуются по MCP — для Continue.dev, Claude Code и любых
других MCP-клиентов:

```powershell
.\start_kb_tools_mcp_http.ps1                 # 0.0.0.0:8000, endpoint /mcp
Invoke-RestMethod http://localhost:8000/health
```

Подробности, конфиги клиентов и тесты — [_MCP_SERVER.md](_MCP_SERVER.md).

## Карты сетевых потоков

`tool_netflows.py` разворачивает разделы «Карта сетевых потоков ...» в плоскую
таблицу `netflows`: одна строка — один разрешённый поток
(адрес источника, адрес получателя, протокол, порт). В документах правило записано
списками внутри ячеек («источники | получатели | протокол/порт | описание»), искать
по ним нельзя; после разбора — обычные SQL-фильтры.

```powershell
python tool_netflows.py sections            # какие карты есть в базе знаний
python tool_netflows.py preview --limit 5   # как разбираются строки, без записи
python tool_netflows.py build --truncate    # разобрать и записать потоки
python tool_netflows.py stats               # сводка: карты, протоколы, топ портов
python tool_netflows.py issues              # строки, где не нашлось адресов или портов

# Поиск: адрес учитывается и по вхождению в подсеть
python tool_netflows.py query --port 445 --proto TCP
python tool_netflows.py query --src 10.6.113.200 --limit 20
python tool_netflows.py query --dst 10.29.130.15 --like домен
python tool_netflows.py export --map КЦОИ-МР --out flows.csv
```

Разбор учитывает реальные особенности документов: лишнюю колонку от объединённой
шапки и хвостовые пустые ячейки (иначе разбор съезжает на колонку), шесть нотаций
портов (`TCP/445`, `443/TCP`, `SSH – 22/tcp`, `TCP (HTTPS)/443`, `TCP/49152–65535`,
`ip-proto-105`, `IP/*`), опечатки в протоколах (`UPD`, кириллическое `ТСР`).
Правила без распознанных адресов или портов не теряются: они пишутся с пустым
значением и полным исходным текстом ячейки, а `issues` показывает их списком.
Стенд функционального тестирования выделяется в отдельную карту (`КЦОИ-1-стенд`),
чтобы тестовые правила не смешивались с продуктивными.

Ключевые колонки `netflows`: `map_name`, `src_addr` / `dst_addr` (+ признаки
`src_is_cidr` / `dst_is_cidr`), `protocol`, `dst_port_min` / `dst_port_max`,
`service`, `rule_desc`, а также исходные `src_text` / `dst_text` / `ports_text` и
координаты в базе знаний (`source`, `section`, `chunk_index`, `row_no`). Повторный
`build` идемпотентен: `flow_id` — устойчивый UUID5 от состава потока.

Порт всегда относится к **получателю**: колонка в документах называется «Входящие
соединения, протокол/порт», то есть это порт, на котором слушает принимающая
сторона. Порт источника эфемерный — назначается стеком при открытии сокета, в
картах не указан и не хранится. `dst_port_min` / `dst_port_max` — границы
диапазона: `TCP/49152–65535` даёт 49152 и 65535, одиночный порт — одинаковые
значения. Поэтому `query --port 60000` находит и правила, заданные диапазоном.

## Основные модули

| Модуль | Назначение |
|--------|------------|
| `rag_agent.py` | RAG-агент: локальная LLM + инструменты, CLI (`ask` / `chat` / `tools`) |
| `tool_netflows.py` | Карты сетевых потоков -> плоская таблица `netflows` + поиск по ней |
| `kb_tools.py` | 13 LangChain Tools + CLI |
| `clickhouse_store.py` | ClickHouseVectorStore (векторное хранилище, все виды поиска) |
| `chunking.py` | Общий контракт чанка: метаданные, разбиение по размеру, таблицы |
| `docx_splitter.py` | Разбор `.docx` (python-docx): заголовки, таблицы, списки |
| `md_splitter.py` | Разбор `.md` (markdown-it-py) |
| `rag_chat.py` | Индексация корпуса, настройки (`Settings`), простой RAG-чат |
| `kb_tools_mcp_http.py` | MCP-сервер (Streamable HTTP) |
| `kb_tools_mcp_stdio.py` | MCP-сервер (stdio) |
| `llm_call_logger.py` | Логирование LLM-вызовов и запросов к БД |
| `logging_config.py` | Настройка логирования |
| `text_utils.py` | Нормализация текста для эмбеддингов |

## Настройка

Создайте `.env` (шаблон — `.env.example`):

```env
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen3.5:9b
OLLAMA_EMBED_MODEL=bge-m3

KNOWLEDGE_DIR=path/to/documents

CLICKHOUSE_HOST=localhost
CLICKHOUSE_PORT=8123
CLICKHOUSE_USERNAME=clickhouse
CLICKHOUSE_PASSWORD=clickhouse
CLICKHOUSE_DATABASE=kcoi
CLICKHOUSE_TABLE=chunks

RETRIEVER_TOP_K=10
AGENT_MAX_ITERATIONS=6
LOG_LEVEL=INFO
```

Модели должны быть загружены в Ollama:

```powershell
ollama pull bge-m3
ollama pull qwen3.5:9b
```

## Документация

- [_MCP_SERVER.md](_MCP_SERVER.md) — MCP-сервер: транспорты, эндпоинты, клиенты
- [docs/README.md](docs/README.md) — архитектура, схема ClickHouse, справочник по поиску
- `.ai/` — отчёты об изменениях по датам

## Требования

```powershell
pip install -r requirements.txt
```
