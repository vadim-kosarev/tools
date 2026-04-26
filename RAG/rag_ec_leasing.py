"""
RAG-чат по коллекции ec-leasing в удалённом ChromaDB с hybrid retrieval.

Проблема чистого семантического поиска: технические идентификаторы
(RDEFINE, RITEM и т.п.) не имеют семантических соседей — embedding-модель
не находит их по similarity. Решение — hybrid retrieval:
  1. Semantic search   — топ-K чанков по cosine similarity (LangChain Chroma)
  2. Keyword search    — точное вхождение через Chroma where_document.$contains
  3. Union + dedup     — объединяем оба списка (keyword-результаты в приоритете)
  4. LLM               — отвечает на основе объединённого контекста

Использование:
    python rag_ec_leasing.py                     # интерактивный чат
    python rag_ec_leasing.py "что такое RDEFINE"  # одиночный вопрос

Переменные окружения (опционально, .env):
    OLLAMA_BASE_URL  — адрес ollama  (default: http://192.168.1.99:11434)
    OLLAMA_MODEL     — LLM модель    (default: qwen2.5:7b)
    CHROMA_HOST      — Chroma host   (default: http://192.168.1.99:3266)
    CHROMA_COLLECTION — коллекция    (default: ec-leasing)
    RETRIEVER_TOP_K  — топ-K semantic (default: 10)
    KEYWORD_TOP_K    — топ-K keyword  (default: 5)
"""

import sys
import logging
import json
import re
from pathlib import Path

import chromadb
from chromadb import HttpClient
from dotenv import load_dotenv
from pydantic import BaseModel
from pydantic_settings import BaseSettings

from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_chroma import Chroma
from langchain_ollama import OllamaEmbeddings, ChatOllama
from logging_config import setup_logging as _setup_logging

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Настройки
# ---------------------------------------------------------------------------

class Settings(BaseSettings):
    ollama_base_url: str = "http://192.168.1.99:11434"
    ollama_model: str = "qwen2.5:7b"
    ollama_embed_model: str = "nomic-embed-text:latest"
    chroma_host: str = "http://192.168.1.99:3266"
    chroma_collection: str = "ec-leasing"
    retriever_top_k: int = 10   # кол-во semantic результатов
    keyword_top_k: int = 5       # кол-во keyword результатов

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()


# ---------------------------------------------------------------------------
# DTO
# ---------------------------------------------------------------------------

class HybridResult(BaseModel):
    """Результат одного чанка из hybrid retrieval."""
    chunk_id: str
    content: str
    source: str
    retrieval_type: str  # "semantic" | "keyword" | "both"


class RagAnswer(BaseModel):
    question: str
    answer: str
    source_files: list[str]
    semantic_chunks: int
    keyword_chunks: int


# ---------------------------------------------------------------------------
# Chroma клиенты
# ---------------------------------------------------------------------------

def _parse_chroma_url(url: str) -> tuple[str, int]:
    """Разбирает URL вида http://host:port на (host, port)."""
    url = url.rstrip("/")
    m = re.match(r"https?://([^:]+):(\d+)", url)
    if not m:
        raise ValueError(f"Неверный формат CHROMA_HOST: {url}")
    return m.group(1), int(m.group(2))


def build_chroma_http_client() -> chromadb.HttpClient:
    """Создаёт сырой ChromaDB HttpClient для keyword поиска."""
    host, port = _parse_chroma_url(settings.chroma_host)
    logger.debug(f"Подключение к Chroma: {host}:{port}")
    return chromadb.HttpClient(host=host, port=port)


def build_langchain_vectorstore(chroma_client: chromadb.HttpClient) -> Chroma:
    """Оборачивает сырой клиент в LangChain Chroma для semantic search."""
    embeddings = OllamaEmbeddings(
        model=settings.ollama_embed_model,
        base_url=settings.ollama_base_url,
    )
    return Chroma(
        client=chroma_client,
        collection_name=settings.chroma_collection,
        embedding_function=embeddings,
    )


# ---------------------------------------------------------------------------
# Hybrid Retrieval
# ---------------------------------------------------------------------------

def semantic_search(vectorstore: Chroma, query: str, k: int) -> list[Document]:
    """Семантический поиск через LangChain/Chroma similarity."""
    retriever = vectorstore.as_retriever(
        search_type="similarity",
        search_kwargs={"k": k},
    )
    results = retriever.invoke(query)
    logger.debug(f"Semantic search вернул {len(results)} чанков")
    return results


def extract_keywords(query: str) -> list[str]:
    """
    Извлекает потенциальные технические идентификаторы из запроса:
    - слова из 2+ символов, написанные ЗАГЛАВНЫМИ буквами (RDEFINE, RACF, ...)
    - слова с цифрами в контексте технических терминов
    Также добавляет весь запрос как fallback, если идентификаторов нет.
    """
    # Слова полностью в верхнем регистре (минимум 2 буквы)
    upper_words = re.findall(r'\b[A-Z][A-Z0-9_]{1,}\b', query)
    keywords = list(dict.fromkeys(upper_words))  # dedup, сохраняя порядок

    if not keywords:
        # Нет идентификаторов — используем весь запрос
        keywords = [query]

    logger.debug(f"Extracted keywords: {keywords}")
    return keywords


def keyword_search(chroma_client: chromadb.HttpClient, query: str, k: int) -> list[Document]:
    """
    Keyword поиск через Chroma where_document.$contains.
    Извлекает технические идентификаторы из запроса и ищет каждый отдельно.
    Объединяет результаты (dedup по content).
    """
    collection = chroma_client.get_collection(settings.chroma_collection)
    keywords = extract_keywords(query)

    seen: set[str] = set()
    all_docs: list[Document] = []

    for kw in keywords:
        try:
            results = collection.get(
                where_document={"$contains": kw},
                limit=k,
                include=["documents", "metadatas"],
            )
            for content, meta in zip(results["documents"], results["metadatas"]):
                key = content[:100]
                if key not in seen:
                    seen.add(key)
                    all_docs.append(Document(
                        page_content=content,
                        metadata=meta or {},
                    ))
        except Exception as e:
            logger.warning(f"Keyword search по '{kw}' ошибка: {e}")

    logger.debug(f"Keyword search по {keywords} вернул {len(all_docs)} чанков")
    return all_docs


def hybrid_retrieve(
    vectorstore: Chroma,
    chroma_client: chromadb.HttpClient,
    query: str,
) -> tuple[list[Document], dict]:
    """
    Hybrid retrieval: объединяет semantic + keyword результаты.
    Keyword-результаты идут первыми (они важнее при точном поиске).
    Возвращает (merged_docs, stats).
    """
    semantic_docs = semantic_search(vectorstore, query, settings.retriever_top_k)
    keyword_docs = keyword_search(chroma_client, query, settings.keyword_top_k)

    # Деduplication по content
    seen: set[str] = set()
    merged: list[Document] = []

    for doc in keyword_docs:
        key = doc.page_content[:100]
        if key not in seen:
            seen.add(key)
            merged.append(doc)

    for doc in semantic_docs:
        key = doc.page_content[:100]
        if key not in seen:
            seen.add(key)
            merged.append(doc)

    stats = {
        "semantic_total": len(semantic_docs),
        "keyword_total": len(keyword_docs),
        "merged_total": len(merged),
    }
    logger.info(
        f"Hybrid retrieval: semantic={stats['semantic_total']}, "
        f"keyword={stats['keyword_total']}, merged={stats['merged_total']}"
    )
    return merged, stats


# ---------------------------------------------------------------------------
# RAG-цепочка
# ---------------------------------------------------------------------------

PROMPT_TEMPLATE = """Ты — ИИ-ассистент, который отвечает строго на основе предоставленных документов.
Отвечай ТОЛЬКО на русском языке.

ВАЖНО: при поиске точных технических терминов, команд, идентификаторов (например RDEFINE, RITEM, RACF и т.п.)
— ищи их ДОСЛОВНО в контексте ниже и цитируй найденный фрагмент.

Используй приведённый контекст для ответа на вопрос пользователя.
Если в контексте нет релевантной информации — ответь: "Не нашёл ответа в документах."
Никогда не придумывай информацию, которой нет в контексте.

Контекст:
{context}

Вопрос: {question}

Ответ:"""


def format_docs(docs: list[Document]) -> str:
    """Форматирует список документов в строку контекста."""
    parts = []
    for i, doc in enumerate(docs, 1):
        source = doc.metadata.get("source", "unknown")
        parts.append(f"[Источник {i}: {source}]\n{doc.page_content}")
    return "\n\n---\n\n".join(parts)


def build_llm_chain() -> object:
    """Создаёт LLM chain (prompt → LLM → parser)."""
    llm = ChatOllama(
        model=settings.ollama_model,
        base_url=settings.ollama_base_url,
        temperature=0.1,
    )
    prompt = ChatPromptTemplate.from_template(PROMPT_TEMPLATE)
    return prompt | llm | StrOutputParser()


def ask_question(
    llm_chain,
    vectorstore: Chroma,
    chroma_client: chromadb.HttpClient,
    question: str,
) -> RagAnswer:
    """
    Выполняет RAG-запрос с hybrid retrieval:
    1. Hybrid search (semantic + keyword)
    2. Формирует контекст
    3. Отправляет в LLM
    """
    logger.debug(f"Вопрос: {question}")

    merged_docs, stats = hybrid_retrieve(vectorstore, chroma_client, question)
    sources = list({doc.metadata.get("source", "unknown") for doc in merged_docs})
    context = format_docs(merged_docs)

    if logger.isEnabledFor(logging.DEBUG):
        chunks_preview = "\n---\n".join(
            f"[{doc.metadata.get('source')}] {doc.page_content[:150]}"
            for doc in merged_docs
        )
        logger.debug(f"Чанки для LLM:\n{chunks_preview}")

    answer = llm_chain.invoke({"context": context, "question": question})

    logger.info(
        f"Ответ получен\n"
        f"  Источников: {len(sources)}\n"
        f"  Файлы: {', '.join(sources)}"
    )
    return RagAnswer(
        question=question,
        answer=answer,
        source_files=sources,
        semantic_chunks=stats["semantic_total"],
        keyword_chunks=stats["keyword_total"],
    )


# ---------------------------------------------------------------------------
# Режимы запуска
# ---------------------------------------------------------------------------

def run_single_question(llm_chain, vectorstore, chroma_client, question: str) -> None:
    """Режим одиночного вопроса."""
    result = ask_question(llm_chain, vectorstore, chroma_client, question)
    print(f"\n{'=' * 70}")
    print(f"Вопрос: {result.question}")
    print(f"{'=' * 70}")
    print(result.answer)
    print(f"\nИсточники: {', '.join(result.source_files)}")
    print(f"[semantic: {result.semantic_chunks}, keyword: {result.keyword_chunks}]")
    print('=' * 70)


def run_interactive_chat(llm_chain, vectorstore, chroma_client) -> None:
    """Интерактивный режим чата в консоли."""
    print("\n" + "=" * 70)
    print(f"Hybrid RAG-чат | коллекция: {settings.chroma_collection}")
    print("Введите вопрос или 'exit' для выхода")
    print("=" * 70 + "\n")

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

        result = ask_question(llm_chain, vectorstore, chroma_client, question)
        print(f"\n{result.answer}")
        print(f"\nИсточники: {', '.join(result.source_files)}")
        print(f"[semantic: {result.semantic_chunks}, keyword: {result.keyword_chunks}]\n")


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

def main() -> None:
    # Настраиваем логирование в файл + консоль
    _setup_logging("rag_ec_leasing")

    logger.info(
        f"Запуск Hybrid RAG-чата\n"
        f"  LLM модель:   {settings.ollama_model}\n"
        f"  Эмбеддинги:   {settings.ollama_embed_model}\n"
        f"  Chroma:       {settings.chroma_host} / {settings.chroma_collection}\n"
        f"  Semantic K:   {settings.retriever_top_k}\n"
        f"  Keyword K:    {settings.keyword_top_k}"
    )

    chroma_client = build_chroma_http_client()
    vectorstore = build_langchain_vectorstore(chroma_client)
    llm_chain = build_llm_chain()

    if len(sys.argv) > 1:
        question = " ".join(sys.argv[1:])
        run_single_question(llm_chain, vectorstore, chroma_client, question)
    else:
        run_interactive_chat(llm_chain, vectorstore, chroma_client)


if __name__ == "__main__":
    main()

