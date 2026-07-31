"""
Базовый RAG-чат и индексация корпуса документации.

Возможности:
  - Индексация локального корпуса (.docx и .md) в ClickHouse с эмбеддингами bge-m3
  - Семантический поиск + генерация ответа через локальную LLM (Ollama)

Для агента с инструментами используйте rag_agent.py, для regex-поиска —
`python kb_tools.py run regex_search pattern=...` (выполняется в ClickHouse).

Использование:
    python rag_chat.py                          # интерактивный чат
    python rag_chat.py "что такое КЦОИ"         # одиночный вопрос
    python rag_chat.py --reindex                # принудительная переиндексация

Переменные окружения (.env):
    OLLAMA_BASE_URL        — адрес Ollama (по умолчанию http://localhost:11434)
    OLLAMA_MODEL           — LLM-модель (по умолчанию qwen3.5:9b)
    OLLAMA_EMBED_MODEL     — модель эмбеддингов (по умолчанию bge-m3)
    KNOWLEDGE_DIR          — папка с исходными документами (.docx, .md)
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
    ollama_model: str = "qwen3.5:9b"
    ollama_embed_model: str = "bge-m3"
    knowledge_dir: str = r"Z:\ES-Leasing\СОИБ КЦОИ"
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
    log_level: str = "DEBUG"               # logging level: DEBUG, INFO, WARNING, ERROR, CRITICAL
    # Agent (rag_agent.py)
    agent_max_iterations: int = 6          # max tool-calling rounds before forcing an answer
    agent_max_tool_chars: int = 12_000     # per-tool-result cap fed back to the LLM
    ollama_timeout: float = 300.0          # seconds, per LLM request

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()


# ---------------------------------------------------------------------------
# DTO
# ---------------------------------------------------------------------------

class RagAnswer(BaseModel):
    question: str
    answer: str
    source_files: list[str]


# ---------------------------------------------------------------------------
# Чанкинг документов — делегировано в сплиттеры по формату
# ---------------------------------------------------------------------------

from md_splitter import split_md_file      # noqa: E402  (after sys.stdout patch)
from docx_splitter import split_docx_file  # noqa: E402

# Расширение файла -> функция разбиения на чанки
_SPLITTERS = {
    ".docx": split_docx_file,
    ".md":   split_md_file,
}


def find_source_files(knowledge_dir: Path) -> list[Path]:
    """Возвращает поддерживаемые документы из папки знаний (рекурсивно).

    Временные файлы Word (`~$имя.docx`) и скрытые файлы пропускаются.
    """
    files = [
        path for path in sorted(knowledge_dir.glob("**/*"))
        if path.is_file()
        and path.suffix.lower() in _SPLITTERS
        and not path.name.startswith(("~$", "."))
    ]
    return files


def load_and_split_all(knowledge_dir: Path) -> list[Document]:
    """Загружает и разбивает на чанки все поддерживаемые документы папки знаний."""
    source_files = find_source_files(knowledge_dir)
    by_type: dict[str, int] = {}
    for path in source_files:
        by_type[path.suffix.lower()] = by_type.get(path.suffix.lower(), 0) + 1
    logger.info(
        f"Найдено документов: {len(source_files)} "
        f"({', '.join(f'{ext}: {cnt}' for ext, cnt in sorted(by_type.items())) or 'нет'})"
    )

    all_chunks: list[Document] = []
    for path in source_files:
        split_file = _SPLITTERS[path.suffix.lower()]
        try:
            all_chunks.extend(split_file(
                path,
                chunk_size=settings.chunk_size,
                chunk_overlap=settings.chunk_overlap,
            ))
        except Exception as exc:
            logger.warning(f"Ошибка загрузки {path.name}: {exc}")

    logger.info(f"Итого чанков для индексации: {len(all_chunks)}")
    return all_chunks


# ---------------------------------------------------------------------------
# Векторное хранилище (ClickHouse)
# ---------------------------------------------------------------------------

from clickhouse_store import ClickHouseVectorStore, ClickHouseStoreSettings


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

    # Семантический индекс по НАЗВАНИЯМ секций (для find_relevant_sections STAGE 2)
    logger.info("Построение индекса названий секций...")
    sections_indexed = store.build_section_index()
    logger.info(f"Индекс названий секций: {sections_indexed} секций")

    return store


# ---------------------------------------------------------------------------
# RAG-цепочка
# ---------------------------------------------------------------------------

PROMPT_TEMPLATE = """\
Ты — эксперт-аналитик по локальной документации.

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


# ---------------------------------------------------------------------------
# Интерактивный чат
# ---------------------------------------------------------------------------

def run_interactive_chat(vectorstore: ClickHouseVectorStore, llm: ChatOllama) -> None:
    """Интерактивный чат в консоли."""
    print(f"\n{SEP}")
    print("RAG-чат по локальной документации")
    print("  Обычный вопрос      → семантический поиск + ответ LLM")
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

        print_rag_answer(ask_question(vectorstore, llm, question))


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RAG-чат по локальной документации (.docx, .md)")
    parser.add_argument("question", nargs="*", help="Вопрос (если не указан — интерактивный режим)")
    parser.add_argument("--reindex", action="store_true", help="Принудительно переиндексировать документы")
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

    if args.reindex:
        confirm = input(
            f"\nВНИМАНИЕ: переиндексация удалит таблицу '{settings.clickhouse_database}.{settings.clickhouse_table}'!\n"
            f"   Введите 'reindex' для подтверждения: "
        ).strip()
        if confirm != "reindex":
            print("Отменено.")
            return

    vectorstore = build_vectorstore(force_reindex=args.reindex)
    llm = build_llm()

    if args.question:
        print_rag_answer(ask_question(vectorstore, llm, " ".join(args.question)))
    else:
        run_interactive_chat(vectorstore, llm)


if __name__ == "__main__":
    main()

