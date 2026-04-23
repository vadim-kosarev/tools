"""
RAG-чат по документации СОИБ КЦОИ.

Возможности:
  - Семантический поиск по .md файлам через ChromaDB HTTP + Ollama (bge-m3)
  - Генерация ответов через LLM (qwen3:8b)
  - Regex-поиск по исходным файлам с контекстом вокруг совпадений
  - Гибридный режим: семантика + regex для специальных запросов

Использование:
    python rag_chat.py                          # интерактивный чат
    python rag_chat.py "что такое КЦОИ"         # одиночный вопрос
    python rag_chat.py --reindex                # принудительная переиндексация
    python rag_chat.py --regex "\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}"  # regex-поиск

Переменные окружения (.env):
    OLLAMA_BASE_URL     — адрес Ollama (по умолчанию http://localhost:11434)
    OLLAMA_MODEL        — LLM-модель (по умолчанию qwen3:8b)
    OLLAMA_EMBED_MODEL  — модель эмбеддингов (по умолчанию bge-m3)
    KNOWLEDGE_DIR       — папка с .md файлами источников знаний
    CHROMA_HOST         — хост ChromaDB HTTP-сервера (по умолчанию localhost)
    CHROMA_PORT         — порт ChromaDB HTTP-сервера (по умолчанию 3266)
"""

import re
import logging
import argparse
from pathlib import Path
from typing import Optional

import chromadb
from dotenv import load_dotenv
from pydantic import BaseModel
from pydantic_settings import BaseSettings

from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_chroma import Chroma
from langchain_ollama import OllamaEmbeddings, ChatOllama


load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Настройки
# ---------------------------------------------------------------------------

class Settings(BaseSettings):
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3:8b"
    ollama_embed_model: str = "bge-m3"
    knowledge_dir: str = r"Z:\ES-Leasing\СОИБ КЦОИ"
    chroma_host: str = "localhost"
    chroma_port: int = 3266
    chroma_collection: str = "soib_kcoi_v2"
    chunk_size: int = 1500
    chunk_overlap: int = 300
    retriever_top_k: int = 10
    regex_context_lines: int = 5  # строк контекста вокруг regex-совпадения

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
# Чанкинг документов (собственная реализация без sentence_transformers)
# ---------------------------------------------------------------------------

_HEADER_RE = re.compile(r"^(#{1,4})\s+(.+)$")


def _split_text_by_size(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    """Разбивает текст на части заданного размера с перекрытием по абзацам/строкам."""
    separators = ["\n\n", "\n", " ", ""]
    chunks: list[str] = []

    def _split(t: str, seps: list[str]) -> None:
        if len(t) <= chunk_size:
            if t.strip():
                chunks.append(t)
            return
        sep = seps[0] if seps else ""
        parts = t.split(sep) if sep else list(t)
        current = ""
        for part in parts:
            candidate = current + (sep if current else "") + part
            if len(candidate) <= chunk_size:
                current = candidate
            else:
                if current.strip():
                    chunks.append(current)
                # перекрытие: берём последние chunk_overlap символов
                overlap_start = max(0, len(current) - chunk_overlap)
                current = current[overlap_start:] + (sep if current else "") + part
                if len(current) > chunk_size and len(seps) > 1:
                    _split(current, seps[1:])
                    current = ""
        if current.strip():
            chunks.append(current)

    _split(text, separators)
    return chunks


def split_md_file(md_file: Path) -> list[Document]:
    """
    Разбивает .md файл на чанки с учётом структуры заголовков.
    Сначала делит по заголовкам #/##/###/####,
    затем слишком длинные секции дополнительно дробит по размеру.
    """
    text = md_file.read_text(encoding="utf-8", errors="replace")
    source_name = md_file.name

    # Разбиваем по заголовкам: собираем секции [(breadcrumb, content)]
    sections: list[tuple[str, str]] = []
    current_headers: dict[int, str] = {}
    current_lines: list[str] = []

    for line in text.splitlines(keepends=True):
        m = _HEADER_RE.match(line.rstrip())
        if m:
            # Сохраняем накопленный текст
            if current_lines:
                breadcrumb = " > ".join(current_headers[lvl] for lvl in sorted(current_headers))
                sections.append((breadcrumb, "".join(current_lines)))
                current_lines = []
            # Обновляем хлебные крошки
            level = len(m.group(1))
            current_headers[level] = m.group(2).strip()
            # Убираем более глубокие уровни
            for lvl in list(current_headers.keys()):
                if lvl > level:
                    del current_headers[lvl]
            current_lines.append(line)
        else:
            current_lines.append(line)

    if current_lines:
        breadcrumb = " > ".join(current_headers[lvl] for lvl in sorted(current_headers))
        sections.append((breadcrumb, "".join(current_lines)))

    # Формируем Document-чанки
    result: list[Document] = []
    for breadcrumb, section_text in sections:
        if not section_text.strip():
            continue
        if len(section_text) <= settings.chunk_size:
            result.append(Document(
                page_content=section_text,
                metadata={"source": source_name, "section": breadcrumb},
            ))
        else:
            for sub in _split_text_by_size(section_text, settings.chunk_size, settings.chunk_overlap):
                result.append(Document(
                    page_content=sub,
                    metadata={"source": source_name, "section": breadcrumb},
                ))

    logger.debug(f"{source_name}: {len(result)} чанков")
    return result


def load_and_split_all(knowledge_dir: Path) -> list[Document]:
    """Загружает и разбивает все .md файлы из папки знаний."""
    md_files = sorted(knowledge_dir.glob("**/*.md"))
    logger.info(f"Найдено .md файлов: {len(md_files)}")

    all_chunks: list[Document] = []
    for md_file in md_files:
        try:
            chunks = split_md_file(md_file)
            all_chunks.extend(chunks)
        except Exception as exc:
            logger.warning(f"Ошибка загрузки {md_file.name}: {exc}")

    logger.info(f"Итого чанков для индексации: {len(all_chunks)}")
    return all_chunks


# ---------------------------------------------------------------------------
# Векторное хранилище
# ---------------------------------------------------------------------------

def _make_embeddings() -> OllamaEmbeddings:
    return OllamaEmbeddings(
        model=settings.ollama_embed_model,
        base_url=settings.ollama_base_url,
    )


def _make_http_client() -> chromadb.HttpClient:
    """Создаёт HTTP-клиент для подключения к Chroma-серверу."""
    return chromadb.HttpClient(
        host=settings.chroma_host,
        port=settings.chroma_port,
    )


_MIN_CHUNK_LEN = 20  # минимальная длина текста чанка для индексации


def _is_valid_chunk(doc: Document) -> bool:
    """Проверяет, что чанк пригоден для создания эмбеддинга."""
    text = doc.page_content.strip()
    if len(text) < _MIN_CHUNK_LEN:
        return False
    # Отфильтровываем чанки, состоящие только из спецсимволов/разделителей
    printable_ratio = sum(1 for c in text if c.isalnum()) / max(len(text), 1)
    return printable_ratio >= 0.1


def _add_batch_safe(store: Chroma, batch: list[Document]) -> int:
    """
    Добавляет батч в Chroma. При ошибке пробует по одному документу.
    Возвращает количество успешно добавленных документов.
    """
    try:
        store.add_documents(batch)
        return len(batch)
    except Exception as batch_err:
        logger.warning(f"Ошибка добавления батча ({len(batch)} doc): {batch_err} — пробуем по одному...")
        success = 0
        for doc in batch:
            try:
                store.add_documents([doc])
                success += 1
            except Exception as doc_err:
                logger.warning(
                    f"Пропускаем чанк [{doc.metadata.get('source')}]: {str(doc_err)[:120]}"
                )
        return success


def build_vectorstore(force_reindex: bool = False) -> Chroma:
    """
    Возвращает ChromaDB векторное хранилище через HTTP-клиент.
    При force_reindex=True удаляет существующую коллекцию и строит заново.
    """
    embeddings = _make_embeddings()
    http_client = _make_http_client()

    existing = [col.name for col in http_client.list_collections()]
    collection_exists = settings.chroma_collection in existing

    if collection_exists and not force_reindex:
        logger.info(
            f"Подключаемся к существующей коллекции '{settings.chroma_collection}'\n"
            f"  Chroma: http://{settings.chroma_host}:{settings.chroma_port}"
        )
        store = Chroma(
            collection_name=settings.chroma_collection,
            embedding_function=embeddings,
            client=http_client,
        )
        count = store._collection.count()
        logger.info(f"Коллекция содержит {count} векторов")
        return store

    if force_reindex and collection_exists:
        http_client.delete_collection(settings.chroma_collection)
        logger.info(f"Коллекция '{settings.chroma_collection}' удалена, строим заново...")

    knowledge_dir = Path(settings.knowledge_dir)
    chunks = load_and_split_all(knowledge_dir)

    # Фильтруем проблемные чанки до отправки в Ollama
    valid_chunks = [c for c in chunks if _is_valid_chunk(c)]
    skipped = len(chunks) - len(valid_chunks)

    logger.info(
        f"Создаём эмбеддинги и загружаем в Chroma HTTP...\n"
        f"  Модель эмбеддингов: {settings.ollama_embed_model}\n"
        f"  Chroma: http://{settings.chroma_host}:{settings.chroma_port}\n"
        f"  Коллекция: {settings.chroma_collection}\n"
        f"  Чанков: {len(valid_chunks)} (отфильтровано: {skipped})"
    )

    # Индексируем батчами по 50 — меньше давления на Ollama
    batch_size = 50
    store: Optional[Chroma] = None
    indexed = 0
    for i in range(0, len(valid_chunks), batch_size):
        batch = valid_chunks[i : i + batch_size]
        if store is None:
            try:
                store = Chroma.from_documents(
                    documents=batch,
                    embedding=embeddings,
                    collection_name=settings.chroma_collection,
                    client=http_client,
                )
                indexed += len(batch)
            except Exception as e:
                logger.warning(f"Ошибка первого батча: {e} — пробуем по одному...")
                # Создаём коллекцию с первым валидным документом
                for doc in batch:
                    try:
                        store = Chroma.from_documents(
                            documents=[doc],
                            embedding=embeddings,
                            collection_name=settings.chroma_collection,
                            client=http_client,
                        )
                        indexed += 1
                        break
                    except Exception:
                        continue
                if store:
                    rest = [d for d in batch if d is not batch[0]]
                    indexed += _add_batch_safe(store, rest)
        else:
            indexed += _add_batch_safe(store, batch)
        logger.info(f"  Проиндексировано {indexed}/{len(valid_chunks)} чанков")

    logger.info(f"Индексация завершена: {indexed} чанков добавлено")
    return store


# ---------------------------------------------------------------------------
# Regex-поиск
# ---------------------------------------------------------------------------

def regex_search(pattern: str, knowledge_dir: Path) -> RegexSearchResult:
    """
    Ищет совпадения с regex-паттерном в исходных .md файлах.
    Возвращает список совпадений с контекстными строками вокруг.
    """
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

Контекст:
{context}

Вопрос: {question}

Ответ:"""


def build_llm() -> ChatOllama:
    """Создаёт экземпляр LLM."""
    return ChatOllama(
        model=settings.ollama_model,
        base_url=settings.ollama_base_url,
        temperature=0.1,
        num_predict=4096,
    )


def ask_question(vectorstore: Chroma, llm: ChatOllama, question: str) -> RagAnswer:
    """
    Выполняет RAG-запрос:
    1. Семантический поиск релевантных чанков
    2. Формирование контекста с указанием источника и раздела
    3. Генерация ответа через LLM
    """
    retriever = vectorstore.as_retriever(
        search_type="similarity",
        search_kwargs={"k": settings.retriever_top_k},
    )

    source_docs: list[Document] = retriever.invoke(question)
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


def print_regex_result(result: RegexSearchResult, max_show: int = 50) -> None:
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


def run_interactive_chat(vectorstore: Chroma, llm: ChatOllama, knowledge_dir: Path) -> None:
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
    args = parse_args()

    logger.info(
        f"Запуск RAG-чата\n"
        f"  LLM:         {settings.ollama_model}\n"
        f"  Эмбеддинги:  {settings.ollama_embed_model}\n"
        f"  Источники:   {settings.knowledge_dir}\n"
        f"  Chroma HTTP: http://{settings.chroma_host}:{settings.chroma_port}"
    )

    knowledge_dir = Path(settings.knowledge_dir)

    # Режим regex-поиска не требует LLM/vectorstore
    if args.regex:
        result = regex_search(args.regex, knowledge_dir)
        print_regex_result(result)
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

