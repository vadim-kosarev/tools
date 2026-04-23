"""
Agentic RAG-чат по документации СОИБ КЦОИ.

Отличие от rag_chat.py: вместо одного прямого поиска используется
многошаговый агентный пайплайн (как у Claude Sonnet):

  1. АНАЛИЗ ЗАПРОСА — LLM разбирает что спрашивается, определяет
     тип вопроса, выделяет ключевые термины и аббревиатуры.
  2. ПЛАН ПОИСКА — LLM строит N перефразировок запроса + опциональные
     regex-паттерны для поиска точных данных (IP, номера, коды).
  3. ПАРАЛЛЕЛЬНЫЙ ПОИСК — N семантических поисков + regex (если нужен),
     результаты дедуплицируются по содержимому.
  4. СИНТЕЗ — LLM формирует структурированный ответ с цитатами и
     ссылками на источники.

Использование:
    python rag_agent.py                      # интерактивный чат
    python rag_agent.py "что такое КЦОИ"     # одиночный вопрос

Переменные окружения — те же что у rag_chat.py (читает тот же .env).
"""

import re
import json
import logging
from pathlib import Path
from typing import Optional

from pydantic import BaseModel
from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_ollama import ChatOllama

import rag_chat
from rag_chat import (
    settings,
    build_vectorstore,
    regex_search,
    SEP,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DTO для агентного пайплайна
# ---------------------------------------------------------------------------

class QueryAnalysis(BaseModel):
    """Результат анализа запроса пользователя."""
    query_type: str           # "factual" | "list" | "comparison" | "pattern_search"
    key_terms: list[str]      # ключевые термины и аббревиатуры
    search_queries: list[str] # 2-4 перефразировки для semantic search
    regex_patterns: list[str] # regex-паттерны (пусто если не нужны)
    reasoning: str            # краткое объяснение плана


class AgentAnswer(BaseModel):
    """Итоговый ответ агента."""
    question: str
    analysis: QueryAnalysis
    retrieved_chunks: int
    answer: str
    source_files: list[str]


# ---------------------------------------------------------------------------
# Промпты
# ---------------------------------------------------------------------------

_ANALYSIS_PROMPT = """\
Ты — аналитик запросов к документации СОИБ КЦОИ Банка России.

Проанализируй запрос пользователя и составь план поиска информации.

Запрос: {question}

Верни ТОЛЬКО валидный JSON без комментариев и markdown-блоков:
{{
  "query_type": "<factual|list|comparison|pattern_search>",
  "key_terms": ["термин1", "термин2"],
  "search_queries": [
    "оригинальный запрос",
    "перефразировка 1 с раскрытыми аббревиатурами",
    "перефразировка 2 другими словами",
    "перефразировка 3 по смыслу"
  ],
  "regex_patterns": [],
  "reasoning": "кратко: что ищем и почему такой план"
}}

Правила:
- query_type = "pattern_search" если ищут IP, номера, коды, шаблоны
- regex_patterns заполнять только при pattern_search
- search_queries: 2-4 штуки, разными словами
- key_terms: аббревиатуры раскрывать (КЦОИ → "коллективный центр обработки информации")
"""

_SYNTHESIS_PROMPT = """\
Ты — эксперт-аналитик по документации системы СОИБ КЦОИ Банка России.

Правила:
1. Используй ТОЛЬКО информацию из предоставленного контекста.
2. Приводи точные цитаты с указанием источника [имя_файла].
3. Если информации недостаточно — явно скажи об этом.
4. Отвечай на русском языке, структурированно.
5. Если вопрос о списке (IP, серверов, систем) — дай полный список.

Контекст:
{context}

Вопрос: {question}

Ответ:"""


# ---------------------------------------------------------------------------
# Шаги агентного пайплайна
# ---------------------------------------------------------------------------

def analyze_query(llm: ChatOllama, question: str) -> QueryAnalysis:
    """
    Шаг 1: LLM анализирует запрос и строит план поиска.
    Возвращает QueryAnalysis с перефразировками и опциональными regex-паттернами.
    """
    prompt = ChatPromptTemplate.from_template(_ANALYSIS_PROMPT)
    chain = prompt | llm | StrOutputParser()

    logger.debug(f"Анализируем запрос: {question}")
    raw = chain.invoke({"question": question})

    # Извлекаем JSON из ответа (LLM иногда добавляет лишнее)
    json_match = re.search(r'\{.*\}', raw, re.DOTALL)
    if not json_match:
        logger.warning(f"LLM вернул не-JSON при анализе запроса, используем fallback\nRaw: {raw[:300]}")
        return QueryAnalysis(
            query_type="factual",
            key_terms=[question],
            search_queries=[question],
            regex_patterns=[],
            reasoning="fallback: не удалось распарсить план",
        )

    try:
        data = json.loads(json_match.group(0))
        analysis = QueryAnalysis(**data)
    except Exception as exc:
        logger.warning(f"Ошибка парсинга QueryAnalysis: {exc}\nRaw: {raw[:300]}")
        analysis = QueryAnalysis(
            query_type="factual",
            key_terms=[question],
            search_queries=[question],
            regex_patterns=[],
            reasoning=f"fallback: {exc}",
        )

    logger.info(
        f"План поиска построен\n"
        f"  Тип запроса:   {analysis.query_type}\n"
        f"  Ключевые слова: {json.dumps(analysis.key_terms, ensure_ascii=False)}\n"
        f"  Поисковых запросов: {len(analysis.search_queries)}\n"
        f"  Regex-паттернов: {len(analysis.regex_patterns)}\n"
        f"  Обоснование: {analysis.reasoning}"
    )
    return analysis


def multi_retrieve(
    vectorstore,
    analysis: QueryAnalysis,
    knowledge_dir: Path,
) -> list[Document]:
    """
    Шаг 2: Выполняет N семантических поисков по всем перефразировкам
    + regex-поиск если нужен. Дедуплицирует результаты по содержимому.
    """
    retriever = vectorstore.as_retriever(
        search_type="similarity",
        search_kwargs={"k": settings.retriever_top_k},
    )

    seen: set[str] = set()
    all_docs: list[Document] = []

    # Семантические поиски по каждой перефразировке
    for query in analysis.search_queries:
        logger.debug(f"Semantic search: {query}")
        docs = retriever.invoke(query)
        for doc in docs:
            key = doc.page_content[:200]
            if key not in seen:
                seen.add(key)
                all_docs.append(doc)

    # Regex-поиск если запрошен
    if analysis.regex_patterns:
        for pattern in analysis.regex_patterns:
            result = regex_search(pattern, knowledge_dir)
            # Конвертируем совпадения в Document для единого контекста
            for match in result.matches[:30]:  # ограничиваем топ-30
                content = f"[regex match: {match.match}]\n{match.context}"
                key = content[:200]
                if key not in seen:
                    seen.add(key)
                    all_docs.append(Document(
                        page_content=content,
                        metadata={"source": match.file, "section": "regex-match"},
                    ))

    logger.info(
        f"Поиск завершён\n"
        f"  Уникальных чанков: {len(all_docs)}\n"
        f"  Из {len(analysis.search_queries)} семантических запросов"
        + (f" + {len(analysis.regex_patterns)} regex" if analysis.regex_patterns else "")
    )
    return all_docs


def synthesize_answer(
    llm: ChatOllama,
    question: str,
    docs: list[Document],
) -> tuple[str, list[str]]:
    """
    Шаг 3: LLM синтезирует финальный ответ из найденных чанков.
    Возвращает (answer_text, source_files).
    """
    context_parts = []
    for doc in docs:
        src = doc.metadata.get("source", "?")
        section = doc.metadata.get("section", "")
        header = f"[{src}]" + (f" — {section}" if section else "")
        context_parts.append(f"{header}\n{doc.page_content}")
    context = "\n\n---\n\n".join(context_parts)

    sources = list({doc.metadata.get("source", "?") for doc in docs})

    prompt = ChatPromptTemplate.from_template(_SYNTHESIS_PROMPT)
    chain = prompt | llm | StrOutputParser()
    answer = chain.invoke({"context": context, "question": question})

    return answer, sources


# ---------------------------------------------------------------------------
# Главный метод агента
# ---------------------------------------------------------------------------

def agent_ask(
    vectorstore,
    llm: ChatOllama,
    question: str,
    knowledge_dir: Path,
) -> AgentAnswer:
    """
    Полный агентный цикл:
    1. analyze_query  — LLM строит план поиска
    2. multi_retrieve — N семантических + regex поисков с дедупликацией
    3. synthesize_answer — LLM формирует финальный ответ
    """
    # Шаг 1: анализ
    analysis = analyze_query(llm, question)

    # Шаг 2: поиск
    docs = multi_retrieve(vectorstore, analysis, knowledge_dir)

    # Шаг 3: синтез
    answer, sources = synthesize_answer(llm, question, docs)

    logger.info(
        f"Агентный ответ готов\n"
        f"  Источников: {len(sources)}\n"
        f"  Файлы: {', '.join(sources)}"
    )
    return AgentAnswer(
        question=question,
        analysis=analysis,
        retrieved_chunks=len(docs),
        answer=answer,
        source_files=sources,
    )


# ---------------------------------------------------------------------------
# Вывод
# ---------------------------------------------------------------------------

def print_agent_answer(result: AgentAnswer) -> None:
    """Выводит агентный ответ с планом поиска."""
    print(f"\n{SEP}")
    print(f"Вопрос: {result.question}")
    print(f"{'-' * 70}")
    print(f"[Анализ] тип={result.analysis.query_type} | чанков={result.retrieved_chunks}")
    print(f"[Запросы] {' / '.join(result.analysis.search_queries[:2])}...")
    if result.analysis.reasoning:
        print(f"[План] {result.analysis.reasoning}")
    print(SEP)
    print(result.answer)
    print(f"\nИсточники: {', '.join(result.source_files)}")
    print(SEP)


# ---------------------------------------------------------------------------
# Интерактивный чат
# ---------------------------------------------------------------------------

def run_interactive_chat(vectorstore, llm: ChatOllama, knowledge_dir: Path) -> None:
    """Интерактивный агентный чат с отображением плана поиска."""
    print(f"\n{SEP}")
    print("Agentic RAG-чат по документации СОИБ КЦОИ")
    print("  Каждый запрос: анализ → план → N поисков → синтез")
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

        result = agent_ask(vectorstore, llm, question, knowledge_dir)
        print_agent_answer(result)


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

def main() -> None:
    import sys
    import argparse

    parser = argparse.ArgumentParser(description="Agentic RAG-чат по документации СОИБ КЦОИ")
    parser.add_argument("question", nargs="*", help="Вопрос (если не указан — интерактивный режим)")
    args = parser.parse_args()

    logger.info(
        f"Запуск Agentic RAG\n"
        f"  LLM:         {settings.ollama_model}\n"
        f"  Эмбеддинги:  {settings.ollama_embed_model}\n"
        f"  Источники:   {settings.knowledge_dir}\n"
        f"  Chroma HTTP: http://{settings.chroma_host}:{settings.chroma_port}"
    )

    knowledge_dir = Path(settings.knowledge_dir)
    vectorstore = build_vectorstore(force_reindex=False)
    llm = rag_chat.build_llm()

    if args.question:
        question = " ".join(args.question)
        result = agent_ask(vectorstore, llm, question, knowledge_dir)
        print_agent_answer(result)
    else:
        run_interactive_chat(vectorstore, llm, knowledge_dir)


if __name__ == "__main__":
    main()

