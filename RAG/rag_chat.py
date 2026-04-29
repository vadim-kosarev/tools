"""
RAG-чат по документации СОИБ КЦОИ.

Возможности:
  - Семантический поиск по .md файлам через ClickHouse + Ollama (bge-m3)
  - Генерация ответов через LLM (qwen3:8b)
  - Regex-поиск по исходным файлам с контекстом вокруг совпадений

Использование:
    python rag_chat.py                          # интерактивный чат
    python rag_chat.py "что такое КЦОИ"         # одиночный вопрос
    python rag_chat.py --reindex                # принудительная переиндексация
    python rag_chat.py --regex "\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}"  # regex-поиск

Переменные окружения (.env):
    OLLAMA_BASE_URL        — адрес Ollama (по умолчанию http://localhost:11434)
    OLLAMA_MODEL           — LLM-модель (по умолчанию qwen3:8b)
    OLLAMA_FINAL_MODEL     — более мощная модель для финального ответа
                             (по умолчанию hf.co/hesamation/Qwen3.6-35B-A3B-Claude-4.6-Opus-Reasoning-Distilled-GGUF:Q4_K_M)
                             # Было: qwen2.5:14b
    OLLAMA_EMBED_MODEL     — модель эмбеддингов (по умолчанию bge-m3)
    KNOWLEDGE_DIR          — папка с .md файлами источников знаний
    PROMPTS_DIR            — папка с промптами относительно RAG/ (по умолчанию prompts, можно prompts_v2)
    CLICKHOUSE_HOST        — хост ClickHouse (по умолчанию localhost)
    CLICKHOUSE_PORT        — порт ClickHouse HTTP (по умолчанию 8123)
    CLICKHOUSE_USERNAME    — пользователь (по умолчанию clickhouse)
    CLICKHOUSE_PASSWORD    — пароль (по умолчанию clickhouse)
    CLICKHOUSE_DATABASE    — база данных (по умолчанию soib_kcoi_v2)
    CLICKHOUSE_TABLE       — таблица чанков (по умолчанию chunks)
"""

import re
import logging
import argparse
import sys
from pathlib import Path
from typing import Optional

# Принудительно переключаем stdout/stderr на UTF-8 — иначе кириллица
# в логах отображается иероглифами в PowerShell (cp866/cp1251 по умолчанию)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import chromadb  # noqa: F401 — kept for potential direct use by external scripts
from dotenv import load_dotenv
from pydantic import BaseModel
from pydantic_settings import BaseSettings

from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_ollama import OllamaEmbeddings, ChatOllama
from logging_config import setup_logging


load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
# Подавляем избыточные HTTP-логи от httpx/httpcore (используются внутри Ollama и ChromaDB клиентов)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Настройки
# ---------------------------------------------------------------------------

class Settings(BaseSettings):
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3:8b"
    # ollama_final_model: str = "qwen2.5:14b"  # Более мощная модель для финального ответа
    ollama_final_model: str = "hf.co/hesamation/Qwen3.6-35B-A3B-Claude-4.6-Opus-Reasoning-Distilled-GGUF:Q4_K_M"  # Claude-distilled модель для финального ответа
    ollama_embed_model: str = "bge-m3"
    knowledge_dir: str = r"Z:\ES-Leasing\СОИБ КЦОИ"
    prompts_dir: str = "prompts.bak"  # Папка с промптами относительно RAG/ (по умолчанию 'prompts', можно 'prompts_v2')
    # ClickHouse connection
    clickhouse_host:     str = "localhost"
    clickhouse_port:     int = 8123
    clickhouse_username: str = "clickhouse"
    clickhouse_password: str = "clickhouse"
    clickhouse_database: str = "soib_kcoi_v2"
    clickhouse_table:    str = "chunks"
    # Chunking
    chunk_size: int = 1500
    chunk_overlap: int = 300
    # Retrieval
    retriever_top_k: int = 10
    retriever_score_threshold: float = 0.0
    max_context_chars: int = 100_000
    regex_context_lines: int = 5
    memory_max_turns: int = 5
    reranker_top_n: int = 15          # top-N anchors kept after LLM reranking (step 2.5)
    hyde_enabled: bool = True
    llm_log_enabled: bool = True           # write LLM prompts/responses to logs/_rag_*.log
    log_level: str = "DEBUG"               # logging level: DEBUG, INFO, WARNING, ERROR, CRITICAL
    # Cross-session memory (ClickHouse table in the same database)
    agent_memory_table:          str   = "agent_memory"
    agent_memory_enabled:        bool  = True
    agent_memory_min_score:      int   = 4    # save only if completeness score >= this
    agent_memory_min_tool_calls: int   = 2    # save only if non-trivial (tool calls >= this)
    agent_memory_recall_sim:     float = 0.80 # min cosine similarity to inject hint
    agent_memory_dedup_sim:      float = 0.92 # skip saving if duplicate exists above this
    # Neighbor-chunk enrichment (step 3)
    # Preceding context is fetched up to enrich_before_chars (≈2× larger than after).
    # A large number of candidate chunks is fetched from ClickHouse; Python trims by chars.
    enrich_before_chars: int = 3000        # max chars of preceding context per anchor
    enrich_after_chars: int = 1500         # max chars of following context per anchor
    enrich_candidates: int = 30            # max ClickHouse rows to fetch per direction

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()


# ---------------------------------------------------------------------------
# DTO
# ---------------------------------------------------------------------------

class RegexMatch(BaseModel):
    file: str
    line_number: int
    match: str
    context: str


class RagAnswer(BaseModel):
    question: str
    answer: str
    source_files: list[str]


class RegexSearchResult(BaseModel):
    pattern: str
    total_matches: int
    matches: list[RegexMatch]


# ---------------------------------------------------------------------------
# Чанкинг документов — делегировано в md_splitter (markdown-it-py)
# ---------------------------------------------------------------------------

from md_splitter import split_md_file  # noqa: E402  (after sys.stdout patch)


def load_and_split_all(knowledge_dir: Path) -> list[Document]:
    """Загружает и разбивает все .md файлы из папки знаний."""
    md_files = sorted(knowledge_dir.glob("**/*.md"))
    logger.info(f"Найдено .md файлов: {len(md_files)}")

    all_chunks: list[Document] = []
    for md_file in md_files:
        try:
            chunks = split_md_file(
                md_file,
                chunk_size=settings.chunk_size,
                chunk_overlap=settings.chunk_overlap,
            )
            all_chunks.extend(chunks)
        except Exception as exc:
            logger.warning(f"Ошибка загрузки {md_file.name}: {exc}")

    logger.info(f"Итого чанков для индексации: {len(all_chunks)}")
    return all_chunks


# ---------------------------------------------------------------------------
# Векторное хранилище (ClickHouse)
# ---------------------------------------------------------------------------

from clickhouse_store import ClickHouseVectorStore, ClickHouseStoreSettings, build_store


def _make_embeddings() -> OllamaEmbeddings:
    return OllamaEmbeddings(
        model=settings.ollama_embed_model,
        base_url=settings.ollama_base_url,
    )


def _make_ch_settings() -> ClickHouseStoreSettings:
    return ClickHouseStoreSettings(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        username=settings.clickhouse_username,
        password=settings.clickhouse_password,
        database=settings.clickhouse_database,
        table=settings.clickhouse_table,
    )


_MIN_CHUNK_LEN = 20
_MIN_LETTER_RATIO = 0.15

_VALUABLE_PATTERNS = re.compile(
    r"""
    \b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}
    | \b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/\d{1,2}
    | (?:порт|port)\s*:?\s*\d{2,5}
    | (?:vlan|влан)\s*:?\s*\d+
    | \b0x[0-9A-Fa-f]{4,}\b
    | \b[А-ЯA-Z]{2,}-\d+(?:\.\d+)*\b
    """,
    re.VERBOSE | re.IGNORECASE,
)


def _is_valid_chunk(doc: Document) -> bool:
    """Проверяет, что чанк пригоден для создания эмбеддинга."""
    text = doc.page_content.strip()
    if len(text) < _MIN_CHUNK_LEN:
        return False
    alnum_count = sum(1 for c in text if c.isalnum())
    if alnum_count < 3:
        return False
    if _VALUABLE_PATTERNS.search(text):
        return True
    letter_count = sum(1 for c in text if c.isalpha())
    if letter_count / max(len(text), 1) < _MIN_LETTER_RATIO:
        return False
    return True


def build_vectorstore(force_reindex: bool = False) -> ClickHouseVectorStore:
    """Возвращает ClickHouseVectorStore.

    При force_reindex=True удаляет таблицу и индексирует заново.
    При наличии данных и force_reindex=False — просто подключается.
    """
    embeddings = _make_embeddings()
    ch_cfg = _make_ch_settings()

    store = ClickHouseVectorStore(
        client=__import__("clickhouse_connect").get_client(
            host=ch_cfg.host, port=ch_cfg.port,
            username=ch_cfg.username, password=ch_cfg.password,
            pool_mgr=__import__("urllib3").PoolManager(maxsize=ch_cfg.pool_maxsize),
        ),
        embedding=embeddings,
        cfg=ch_cfg,
    )
    store.create_table()

    if not force_reindex:
        count = store.count()
        logger.info(
            f"Подключаемся к ClickHouse '{ch_cfg.database}.{ch_cfg.table}'\n"
            f"  Host: {ch_cfg.host}:{ch_cfg.port}\n"
            f"  Чанков в таблице: {count}"
        )
        return store

    # Полная переиндексация
    store.drop_table()
    store.create_table()
    logger.info(f"Таблица {ch_cfg.database}.{ch_cfg.table} пересоздана")

    knowledge_dir = Path(settings.knowledge_dir)
    chunks = load_and_split_all(knowledge_dir)
    valid_chunks = [c for c in chunks if _is_valid_chunk(c)]
    skipped = len(chunks) - len(valid_chunks)

    logger.info(
        f"Индексация в ClickHouse...\n"
        f"  Эмбеддинги: {settings.ollama_embed_model}\n"
        f"  База:       {ch_cfg.database}.{ch_cfg.table}\n"
        f"  Чанков:     {len(valid_chunks)} (отфильтровано: {skipped})"
    )

    batch_size = 100
    indexed = 0
    for i in range(0, len(valid_chunks), batch_size):
        batch = valid_chunks[i: i + batch_size]
        try:
            store.add_documents(batch)
            indexed += len(batch)
        except Exception as exc:
            logger.warning(f"Ошибка батча {i}..{i+len(batch)}: {exc} — пробуем по одному")
            for doc in batch:
                try:
                    store.add_documents([doc])
                    indexed += 1
                except Exception as doc_exc:
                    logger.warning(f"  Пропуск [{doc.metadata.get('source')}]: {str(doc_exc)[:100]}")
        logger.info(f"  Проиндексировано {indexed}/{len(valid_chunks)} чанков")

    logger.info(f"Индексация завершена: {indexed} чанков добавлено")
    return store


# ---------------------------------------------------------------------------
# Regex-поиск
# ---------------------------------------------------------------------------

def regex_search(pattern: str, knowledge_dir: Path) -> RegexSearchResult:
    """Ищет совпадения с regex-паттерном в исходных .md файлах."""
    ctx = settings.regex_context_lines
    try:
        compiled = re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        logger.error(f"Некорректный regex-паттерн '{pattern}': {exc}")
        return RegexSearchResult(pattern=pattern, total_matches=0, matches=[])

    matches: list[RegexMatch] = []
    for md_file in sorted(knowledge_dir.glob("**/*.md")):
        try:
            lines = md_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception as exc:
            logger.warning(f"Ошибка чтения {md_file.name}: {exc}")
            continue
        for i, line in enumerate(lines):
            for m in compiled.finditer(line):
                ctx_start = max(0, i - ctx)
                ctx_end = min(len(lines), i + ctx + 1)
                context_block = "\n".join(
                    f"{'>>>' if j == i else '   '} {lines[j]}"
                    for j in range(ctx_start, ctx_end)
                )
                matches.append(RegexMatch(
                    file=md_file.name,
                    line_number=i + 1,
                    match=m.group(0),
                    context=context_block,
                ))

    logger.info(f"Regex '{pattern}': найдено {len(matches)} совпадений")
    return RegexSearchResult(pattern=pattern, total_matches=len(matches), matches=matches)


# ---------------------------------------------------------------------------
# RAG-цепочка
# ---------------------------------------------------------------------------

PROMPT_TEMPLATE = """\
Ты — эксперт-аналитик по документации системы СОИБ КЦОИ Банка России.

Правила:
1. Используй ТОЛЬКО информацию из предоставленного контекста.
2. Аббревиатуры раскрываются в скобках рядом с полным названием: «коллективный центр обработки информации (КЦОИ)».
3. Приводи точные цитаты и ссылки на источник (название файла).
4. Если информации недостаточно — явно скажи об этом.
5. Отвечай на русском языке, структурированно.
6. Каждый фрагмент контекста снабжён заголовком вида [файл] — раздел.
   Если ключевые термины вопроса встречаются в заголовке раздела — считай этот фрагмент приоритетным.
7. Для таблиц: каждая строка представлена в виде «Заголовок столбца: значение».
   Используй эти пары для точного ответа на вопросы о конкретных значениях (IP, названия, коды).

Контекст:
{context}

Вопрос: {question}

Ответ:"""


def build_llm(model: Optional[str] = None) -> ChatOllama:
    return ChatOllama(
        model=model or settings.ollama_model,
        base_url=settings.ollama_base_url,
        temperature=0.1,
        num_predict=4096,
        streaming=True,
    )


def ask_question(vectorstore: ClickHouseVectorStore, llm: ChatOllama, question: str) -> RagAnswer:
    """Выполняет RAG-запрос: поиск → контекст → генерация ответа."""
    source_docs = vectorstore.similarity_search(question, k=settings.retriever_top_k)
    sources = list({doc.metadata.get("source", "?") for doc in source_docs})

    context_parts = []
    for doc in source_docs:
        src = doc.metadata.get("source", "?")
        section = doc.metadata.get("section", "")
        header = f"[{src}]" + (f" — {section}" if section else "")
        context_parts.append(f"{header}\n{doc.page_content}")
    context = "\n\n---\n\n".join(context_parts)

    logger.debug(
        f"Найдено {len(source_docs)} релевантных чанков:\n" +
        "\n".join(
            f"  [{d.metadata.get('source')}] {d.page_content[:120].replace(chr(10), ' ')}"
            for d in source_docs
        )
    )

    prompt = ChatPromptTemplate.from_template(PROMPT_TEMPLATE)
    chain = prompt | llm | StrOutputParser()
    answer = chain.invoke({"context": context, "question": question})

    logger.info(
        f"Ответ сформирован\n"
        f"  Источников: {len(sources)}\n"
        f"  Файлы: {', '.join(sources)}"
    )
    return RagAnswer(question=question, answer=answer, source_files=sources)


# ---------------------------------------------------------------------------
# Вывод результатов
# ---------------------------------------------------------------------------

SEP = "=" * 70


def print_rag_answer(rag: RagAnswer) -> None:
    print(f"\n{SEP}")
    print(f"Вопрос: {rag.question}")
    print(SEP)
    print(rag.answer)
    print(f"\nИсточники: {', '.join(rag.source_files)}")
    print(SEP)


def print_regex_result(result: RegexSearchResult, max_show: int = 100) -> None:
    print(f"\n{SEP}")
    print(f"Regex: {result.pattern}")
    print(f"Всего совпадений: {result.total_matches}")
    print(SEP)
    for m in result.matches[:max_show]:
        print(f"\n[{m.file}] строка {m.line_number}: {m.match}")
        print(m.context)
        print("-" * 40)
    if result.total_matches > max_show:
        print(f"\n... и ещё {result.total_matches - max_show} совпадений.")
    print(SEP)


# ---------------------------------------------------------------------------
# Интерактивный чат
# ---------------------------------------------------------------------------

def _parse_regex_query(text: str) -> Optional[str]:
    """
    Если пользователь ввёл /pattern/ или 'regex: pattern' — возвращает паттерн.
    Иначе None.
    """
    m = re.match(r"^\s*/(.+)/\s*$", text)
    if m:
        return m.group(1)
    m = re.match(r"^\s*regex?:\s*(.+)$", text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return None


def run_interactive_chat(vectorstore: ClickHouseVectorStore, llm: ChatOllama, knowledge_dir: Path) -> None:
    """Интерактивный чат в консоли с поддержкой regex-запросов."""
    print(f"\n{SEP}")
    print("RAG-чат по документации СОИБ КЦОИ")
    print("  Обычный вопрос     → семантический поиск + ответ LLM")
    print("  /паттерн/          → regex-поиск по файлам")
    print("  regex: паттерн     → regex-поиск по файлам")
    print("  exit / quit / выход → выйти")
    print(f"{SEP}\n")

    while True:
        try:
            question = input("Вопрос: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nВыход.")
            break

        if not question:
            continue
        if question.lower() in ("exit", "quit", "выход"):
            print("До свидания!")
            break

        regex_pattern = _parse_regex_query(question)
        if regex_pattern:
            result = regex_search(regex_pattern, knowledge_dir)
            print_regex_result(result)
        else:
            rag = ask_question(vectorstore, llm, question)
            print_rag_answer(rag)


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RAG-чат по документации СОИБ КЦОИ")
    parser.add_argument("question", nargs="*", help="Вопрос (если не указан — интерактивный режим)")
    parser.add_argument("--reindex", action="store_true", help="Принудительно переиндексировать документы")
    parser.add_argument("--regex", metavar="PATTERN", help="Regex-поиск по исходным файлам (без LLM)")
    return parser.parse_args()


def main() -> None:
    # Настраиваем логирование в файл + консоль
    setup_logging("rag_chat")

    args = parse_args()

    logger.info(
        f"Запуск RAG-чата\n"
        f"  LLM:         {settings.ollama_model}\n"
        f"  Эмбеддинги:  {settings.ollama_embed_model}\n"
        f"  Источники:   {settings.knowledge_dir}\n"
        f"  ClickHouse:  {settings.clickhouse_host}:{settings.clickhouse_port} "
        f"→ {settings.clickhouse_database}.{settings.clickhouse_table}"
    )

    knowledge_dir = Path(settings.knowledge_dir)

    # Режим regex-поиска не требует LLM/vectorstore
    if args.regex:
        result = regex_search(args.regex, knowledge_dir)
        print_regex_result(result)
        return

    if args.reindex:
        confirm = input(
            f"\n⚠️  Переиндексация удалит таблицу '{settings.clickhouse_database}.{settings.clickhouse_table}'!\n"
            f"   Введите 'reindex' для подтверждения: "
        ).strip()
        if confirm != "reindex":
            print("Отменено.")
            return

    vectorstore = build_vectorstore(force_reindex=args.reindex)
    llm = build_llm()

    if args.question:
        question = " ".join(args.question)
        regex_pattern = _parse_regex_query(question)
        if regex_pattern:
            result = regex_search(regex_pattern, knowledge_dir)
            print_regex_result(result)
        else:
            rag = ask_question(vectorstore, llm, question)
            print_rag_answer(rag)
    else:
        run_interactive_chat(vectorstore, llm, knowledge_dir)


if __name__ == "__main__":
    main()

