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
import sys
from pathlib import Path
from typing import Optional

# Принудительно переключаем stdout/stderr на UTF-8 — иначе кириллица
# в логах отображается иероглифами в PowerShell (cp866/cp1251 по умолчанию)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

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
    ollama_embed_model: str = "bge-m3"
    knowledge_dir: str = r"Z:\ES-Leasing\СОИБ КЦОИ"
    chroma_host: str = "localhost"
    chroma_port: int = 3266
    chroma_collection: str = "soib_kcoi_v2"
    chunk_size: int = 1500
    chunk_overlap: int = 300
    retriever_top_k: int = 10
    retriever_score_threshold: float = 0.0   # 0.0 = отключено; рекомендуемое значение ~0.3
    max_context_chars: int = 60_000          # максимальный размер контекста для LLM (~40K токенов)
    regex_context_lines: int = 5             # строк контекста вокруг regex-совпадения
    memory_max_turns: int = 5               # сколько последних обменов хранить в памяти диалога
    reranker_top_n: int = 5                 # топ-N чанков после reranking (0 = reranking отключён)
    hyde_enabled: bool = True               # включить HyDE (генерация гипотетического документа)

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


def _is_table_line(line: str) -> bool:
    """Возвращает True если строка является частью таблицы (Markdown или grid/RST)."""
    stripped = line.lstrip()
    return stripped.startswith("|") or (stripped.startswith("+") and "-" in stripped)


def _parse_md_table(lines: list[str]) -> tuple[list[str], list[list[str]]]:
    """
    Разбирает Markdown-таблицу (|col|col|) на заголовки и строки данных.
    Возвращает (headers, data_rows), где каждый элемент — список значений ячеек.
    Строки-разделители (| --- |) пропускаются.
    """
    headers: list[str] = []
    data_rows: list[list[str]] = []

    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        # Убираем ведущий/завершающий `|`, разбиваем по `|`
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        # Строка-разделитель: все ячейки из тире/двоеточий/пробелов
        if all(re.match(r"^[-:]+$", c) for c in cells if c):
            continue
        if not headers:
            headers = cells
        else:
            data_rows.append(cells)

    return headers, data_rows


def _parse_grid_table(lines: list[str]) -> tuple[list[str], list[list[str]]]:
    """
    Разбирает grid/RST-таблицу (разделители +----+) на заголовки и строки данных.

    Поддерживает многострочные ячейки: текст из нескольких строк одной ячейки
    конкатенируется через пробел. Вертикальные объединения (rowspan) не поддерживаются —
    каждый блок между разделителями считается отдельной строкой.
    Возвращает (headers, data_rows).
    """
    # Строки-разделители: начинаются с '+' и содержат '-'
    sep_indices = [
        i for i, line in enumerate(lines)
        if line.strip().startswith("+") and "-" in line
    ]

    if len(sep_indices) < 2:
        return [], []

    # Определяем границы столбцов по первой строке-разделителю
    first_sep = lines[sep_indices[0]].rstrip()
    col_starts = [i for i, c in enumerate(first_sep) if c == "+"]

    if len(col_starts) < 2:
        return [], []

    col_ranges: list[tuple[int, int]] = [
        (col_starts[j] + 1, col_starts[j + 1])
        for j in range(len(col_starts) - 1)
    ]

    def _extract_cells(row_lines: list[str]) -> list[str]:
        """Извлекает текст ячеек из блока строк между разделителями."""
        cells = [""] * len(col_ranges)
        for rline in row_lines:
            if not rline.lstrip().startswith("|"):
                continue
            padded = rline.rstrip()
            for ci, (start, end) in enumerate(col_ranges):
                if start < len(padded):
                    part = padded[start:end].strip()
                    if part:
                        cells[ci] = (cells[ci] + " " + part).strip()
        return cells

    headers: list[str] = []
    data_rows: list[list[str]] = []

    for block_idx in range(len(sep_indices) - 1):
        block_start = sep_indices[block_idx] + 1
        block_end = sep_indices[block_idx + 1]
        row_lines = lines[block_start:block_end]
        cells = _extract_cells(row_lines)

        if not any(cells):
            continue

        if not headers:
            headers = cells
        else:
            # Пропускаем строки-дублики заголовка (иногда повторяется после первого +---+)
            if cells != headers:
                data_rows.append(cells)

    return headers, data_rows


def _table_row_to_docs(
    headers: list[str],
    header_line: str,
    separator_line: str,
    data_rows: list[list[str]],
    source_name: str,
    breadcrumb: str,
) -> list[Document]:
    """
    Создаёт один Document на каждую строку данных таблицы.

    Формат page_content каждого чанка:
        <оригинальная строка таблицы с заголовком>
        <key-value строка: Столбец: значение | ...)

    Такое представление позволяет:
    - Семантически искать по значениям (IP, имена, коды)
    - LLM видит и столбец и значение без дополнительного контекста
    """
    docs: list[Document] = []
    for row_cells in data_rows:
        # Нормализуем длину строки до числа заголовков
        padded = row_cells + [""] * max(0, len(headers) - len(row_cells))
        padded = padded[:len(headers)] if headers else row_cells

        # Markdown-строка (с заголовком таблицы для контекста)
        md_row = f"| {' | '.join(padded)} |"
        table_context = f"{header_line}\n{separator_line}\n{md_row}"

        # Key-value представление строки
        kv_parts = [
            f"{h}: {v}"
            for h, v in zip(headers, padded)
            if h and v  # пропускаем пустые ячейки
        ]
        kv_line = " | ".join(kv_parts)

        page_content = f"{table_context}\n\n{kv_line}" if kv_line else table_context

        docs.append(Document(
            page_content=page_content,
            metadata={
                "source": source_name,
                "section": breadcrumb,
                "chunk_type": "table_row",
                "table_headers": ", ".join(headers),
            },
        ))
    return docs


def _split_section_preserving_tables(
    text: str,
    chunk_size: int,
    chunk_overlap: int,
    source_name: str,
    breadcrumb: str,
) -> list[Document]:
    """
    Разбивает текст секции на чанки:
    - Markdown-таблицы: каждая строка → отдельный Document (row-level chunking)
      с key-value представлением и заголовком таблицы в контексте.
    - Нетабличный текст: разбивается по размеру стандартным способом.
    """
    lines = text.splitlines(keepends=True)
    blocks: list[tuple[bool, str]] = []
    i = 0
    while i < len(lines):
        if _is_table_line(lines[i]):
            table_lines: list[str] = []
            while i < len(lines) and _is_table_line(lines[i]):
                table_lines.append(lines[i])
                i += 1
            blocks.append((True, "".join(table_lines)))
        else:
            prose_lines: list[str] = []
            while i < len(lines) and not _is_table_line(lines[i]):
                prose_lines.append(lines[i])
                i += 1
            blocks.append((False, "".join(prose_lines)))

    docs: list[Document] = []
    for is_table, block in blocks:
        if not block.strip():
            continue

        if is_table:
            # Парсим таблицу: заголовки + строки данных
            block_lines = block.splitlines()

            # Определяем тип таблицы: grid (+----+) или Markdown (| col |)
            is_grid = any(
                line.strip().startswith("+") and "-" in line
                for line in block_lines
            )
            if is_grid:
                headers, data_rows = _parse_grid_table(block_lines)
            else:
                headers, data_rows = _parse_md_table(block_lines)

            if not headers or not data_rows:
                # Если таблица не распозналась — кладём целиком как обычный чанк
                docs.append(Document(
                    page_content=block,
                    metadata={"source": source_name, "section": breadcrumb, "chunk_type": "table_raw"},
                ))
                continue

            # Находим строку-заголовок и строку-разделитель для контекста в каждом чанке
            header_line = ""
            separator_line = ""
            if is_grid:
                # Для grid-таблиц заголовок — первая строка `|`, разделитель — первая `+---+`
                for line in block_lines:
                    stripped = line.strip()
                    if not separator_line and stripped.startswith("+") and "-" in stripped:
                        separator_line = stripped
                    elif separator_line and not header_line and stripped.startswith("|"):
                        header_line = stripped
                    elif header_line and separator_line and stripped.startswith("+") and "-" in stripped:
                        break  # нашли оба
            else:
                for line in block_lines:
                    stripped = line.strip()
                    if not header_line and stripped.startswith("|") and not re.match(r"^\|[\s|:-]+\|$", stripped):
                        header_line = stripped
                    elif header_line and not separator_line and re.match(r"^\|[\s|:-]+\|$", stripped):
                        separator_line = stripped

            row_docs = _table_row_to_docs(
                headers=headers,
                header_line=header_line,
                separator_line=separator_line,
                data_rows=data_rows,
                source_name=source_name,
                breadcrumb=breadcrumb,
            )
            docs.extend(row_docs)
            logger.debug(
                f"  Таблица [{source_name}] '{breadcrumb[:60]}': "
                f"{len(data_rows)} строк → {len(row_docs)} чанков"
            )
        else:
            # Нетабличный текст
            if len(block) <= chunk_size:
                docs.append(Document(
                    page_content=block,
                    metadata={"source": source_name, "section": breadcrumb},
                ))
            else:
                for sub in _split_text_by_size(block, chunk_size, chunk_overlap):
                    docs.append(Document(
                        page_content=sub,
                        metadata={"source": source_name, "section": breadcrumb},
                    ))

    return docs


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
            result.extend(
                _split_section_preserving_tables(
                    section_text,
                    settings.chunk_size,
                    settings.chunk_overlap,
                    source_name=source_name,
                    breadcrumb=breadcrumb,
                )
            )

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


_MIN_CHUNK_LEN = 20        # минимальная длина текста чанка для индексации
_MIN_LETTER_RATIO = 0.15   # минимальная доля букв — применяется только если нет "ценных" паттернов

# Паттерны, присутствие которых делает чанк ценным даже при малом числе букв
_VALUABLE_PATTERNS = re.compile(
    r"""
    \b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}   # IPv4
    | \b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/\d{1,2}  # CIDR
    | (?:порт|port)\s*:?\s*\d{2,5}           # порт
    | (?:vlan|влан)\s*:?\s*\d+               # VLAN
    | \b0x[0-9A-Fa-f]{4,}\b                  # hex
    | \b[А-ЯA-Z]{2,}-\d+(?:\.\d+)*\b        # номер документа
    """,
    re.VERBOSE | re.IGNORECASE,
)


def _is_valid_chunk(doc: Document) -> bool:
    """
    Проверяет, что чанк пригоден для создания эмбеддинга.

    Логика:
    1. Минимальная длина — отсекает пустые чанки.
    2. Минимальное число alnum-символов — отсекает чанки из одних спецсимволов (---|---).
    3. Если чанк содержит «ценные» паттерны (IPv4, порт, VLAN, hex, номер документа) —
       пропускаем в индекс даже при малом числе букв. IP-адреса важны для поиска!
    4. Если «ценных» паттернов нет — проверяем долю букв (_MIN_LETTER_RATIO).
       Чанки с чисто числовыми строками без смыслового текста вызывают NaN в bge-m3.
    """
    text = doc.page_content.strip()
    if len(text) < _MIN_CHUNK_LEN:
        return False

    alnum_count = sum(1 for c in text if c.isalnum())
    if alnum_count < 3:
        return False

    # Чанк с IP/портами/VLAN/hex — ценен, разрешаем даже без букв
    if _VALUABLE_PATTERNS.search(text):
        return True

    # Проверяем долю букв: чанки без ценных паттернов и почти без букв → NaN
    letter_count = sum(1 for c in text if c.isalpha())
    if letter_count / max(len(text), 1) < _MIN_LETTER_RATIO:
        return False

    return True


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
6. Каждый фрагмент контекста снабжён заголовком вида [файл] — раздел.
   Если ключевые термины вопроса встречаются в заголовке раздела — считай этот фрагмент приоритетным
   и опирайся на него в первую очередь при формировании ответа.
7. Для таблиц: каждая строка представлена в виде «Заголовок столбца: значение».
   Используй эти пары для точного ответа на вопросы о конкретных значениях (IP, названия, коды).

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

