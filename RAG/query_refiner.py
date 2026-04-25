"""
Query expansion and answer evaluation for the LangGraph RAG agent.

Design principle — single source of truth for LLM output schema:
  Field descriptions live ONLY in Pydantic Field(description=...).
  System prompts are generated from model_json_schema() by _build_*_system()
  so there is no duplication between Python types and LLM instructions.

Query expansion (expand_query):
  1. expand_query(llm, question)
       → rephrased_queries: 5 reformulations (synonym, general, detailed, role, acronym)
       → key_terms: exact-search terms (abbreviations, codes, proper names)
       → synonyms: wider/narrower concepts for semantic search broadening
  2. build_expanded_query() wraps the question with numbered tool-call obligations.

Answer evaluation (evaluate_answer):
  3. evaluate_answer(llm, question, answer)
       → relevance_score  1-10  (how well the answer addresses the question)
       → completeness_score 1-10 (how fully the topic is covered)
       → missing_aspects: aspects not covered

Log file format (when LlmCallLogger is provided):
  ### QUERY EXPANSION START / COMPLETE  — expansion LLM call + result summary
  === EXPAND_QUERY REQUEST/RESPONSE     — full prompt/response captured by callback
  ### EVALUATION N START / COMPLETE     — evaluation LLM call + score summary
  === EVALUATE_PASS_N REQUEST/RESPONSE  — full evaluation prompt/response
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from langchain_core.messages import BaseMessage
from langchain_ollama import ChatOllama
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic models for structured LLM outputs
# — descriptions in Field() are the single source of truth for LLM prompts
# ---------------------------------------------------------------------------

class QueryExpansion(BaseModel):
    """Расширение вопроса пользователя для более полного покрытия базы знаний."""

    rephrased_queries: list[str] = Field(
        description=(
            "От 1 до 5 перефразировок вопроса (в зависимости от сложности оригинального запроса):\n"
            "  1) синонимическая — ключевые слова заменены синонимами;\n"
            "  2) более общая / абстрактная формулировка;\n"
            "  3) более конкретная / детальная формулировка;\n"
            "  4) с другой точки зрения (администратор, IT-специалист);\n"
            "  5) только аббревиатуры и технические коды без пояснений."
        ),
    )
    key_terms: list[str] = Field(
        description=(
            "ВСЕ аббревиатуры, ключевые словосочетания (в именительном падеже), "
            " коды, собственные имена и точные технические термины для exact_search:\n"
            "  — из вопроса напрямую;\n"
            "  — связанные термины из вероятного контекста;\n"
            "  — расшифровки аббревиатур (БДКО → «база данных карточек объекта»);\n"
            "  — синонимы технических понятий (ПО → «программное обеспечение», «программные средства»).\n"
            "Примеры: 'КЦОИ', 'Active Directory', 'svc/ldap-bdko', 'программное обеспечение'."
        ),
    )
    synonyms: list[str] = Field(
        description=(
            "Синонимы, обобщения и смежные технические понятия для semantic_search.\n"
            "Включай как более узкие, так и более широкие понятия.\n"
            "Пример: для «ПО» → 'программные средства', 'состав ПО', "
            "'технологический стек', 'прикладное программное обеспечение'."
        ),
    )
    regex_patterns: list[str] = Field(
        default_factory=list,
        description=(
            "Regex-паттерны для поиска структурированных данных через regex_search.\n"
            "Заполняй ТОЛЬКО если вопрос касается: IP-адресов, портов, VLAN-ов, "
            "подсетей, документных кодов, серийных номеров, URL-путей.\n"
            "Оставляй пустым для обычных текстовых вопросов.\n"
            r"Примеры: r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}' (IP),"
            r" r'порт\s*:?\s*\d+' (порт), r'vlan\s*\d+' (VLAN),"
            r" r'svc/[a-z\-]+' (сервисный аккаунт)."
        ),
    )


class AnswerEvaluation(BaseModel):
    """Оценка качества ответа агента на вопрос пользователя по двум критериям."""

    relevance_score: int = Field(
        ge=1,
        le=10,
        description=(
            "Насколько точно ответ адресует поставленный вопрос.\n"
            "  1–4 — ответ нерелевантен или полностью не по теме;\n"
            "  5–7 — частично отвечает, но явно чего-то не хватает;\n"
            "  8–9 — хороший ответ с незначительными пробелами;\n"
            "  10  — идеально точно адресует именно этот вопрос."
        ),
    )
    completeness_score: int = Field(
        ge=1,
        le=10,
        description=(
            "Насколько полно и исчерпывающе раскрыта тема.\n"
            "  1–4 — очень неполный, важные аспекты отсутствуют;\n"
            "  5–7 — удовлетворительно, но есть заметные пробелы;\n"
            "  8–9 — хорошо, незначительные пробелы;\n"
            "  10  — охватывает все важные аспекты темы исчерпывающе.\n"
            "Ставь 10 ТОЛЬКО если ответ одновременно точен И исчерпывает тему."
        ),
    )
    missing_aspects: list[str] = Field(
        description=(
            "Важные подвопросы, темы или объекты из исходного вопроса, "
            "которые не раскрыты или едва упомянуты в ответе."
        ),
    )
    reasoning: str = Field(
        description="Обоснование в 1–2 предложениях, объясняющее выставленные баллы.",
    )


class IterationResult(BaseModel):
    """One agent pass: what was asked, what was answered, how it scored."""

    pass_number: int
    expanded_query: str
    answer: str
    tool_calls_count: int = 0
    evaluation: AnswerEvaluation


class RefinementResult(BaseModel):
    """Complete two-pass result with self-evaluation and quality comparison."""

    original_question: str
    iterations: list[IterationResult]
    best_pass: int                   # 1 or 2
    quality_improved: bool
    quality_delta: str               # "улучшился" | "ухудшился" | "остался прежним"
    final_answer: str
    final_evaluation: AnswerEvaluation


# ---------------------------------------------------------------------------
# System prompts — generated from Pydantic schemas (single source of truth)
# ---------------------------------------------------------------------------

def _fields_block(model_cls: type[BaseModel]) -> str:
    """Render numbered field list from a Pydantic model's JSON schema.

    Used to inject field descriptions into system prompts without duplication:
    the Field(description=...) in each model is the one and only place where
    field semantics are defined.

    Args:
        model_cls: A Pydantic BaseModel subclass.

    Returns:
        Multi-line string, one numbered entry per field: "N. name — description".
    """
    schema = model_cls.model_json_schema()
    props = schema.get("properties", {})
    lines: list[str] = []
    for i, (name, info) in enumerate(props.items(), 1):
        desc = info.get("description", "—").replace("\n", " ").strip()
        lines.append(f"  {i}. {name} — {desc}")
    return "\n".join(lines)


def _build_expansion_system() -> str:
    """Build the expansion system prompt from QueryExpansion field descriptions."""
    model_doc = QueryExpansion.__doc__ or ""
    fields = _fields_block(QueryExpansion)
    return (
        "Ты — эксперт по семантическому поиску в специализированных базах знаний.\n"
        f"Проанализируй вопрос пользователя и верни объект QueryExpansion — {model_doc.strip()}\n\n"
        "Поля объекта QueryExpansion:\n"
        f"{fields}\n\n"
        "Отвечай ТОЛЬКО структурированным объектом. Никаких пояснений вне структуры."
    )


def _build_evaluation_system() -> str:
    """Build the evaluation system prompt from AnswerEvaluation field descriptions."""
    model_doc = AnswerEvaluation.__doc__ or ""
    fields = _fields_block(AnswerEvaluation)
    return (
        "Ты — строгий рецензент качества ответов на вопросы по технической документации.\n"
        f"Верни объект AnswerEvaluation — {model_doc.strip()}\n\n"
        "Поля объекта AnswerEvaluation:\n"
        f"{fields}\n\n"
        "Отвечай ТОЛЬКО структурированным объектом. Никаких пояснений вне структуры."
    )



# ---------------------------------------------------------------------------
# LLM structured calls
# ---------------------------------------------------------------------------

def expand_query(
    llm: ChatOllama,
    question: str,
    llm_logger: Any | None = None,
) -> QueryExpansion:
    """Generate rephrased queries, key terms, and synonyms for richer KB search.

    Uses structured output (Pydantic + tool calling).
    Falls back to a minimal expansion on any LLM error so the agent loop never blocks.
    When llm_logger is provided, the full LLM prompt/response is written to the
    log file under the [EXPAND_QUERY] step label.

    Args:
        llm:        ChatOllama instance bound to the conversation model.
        question:   Raw user question.
        llm_logger: Optional LlmCallLogger for file logging (may be disabled).

    Returns:
        QueryExpansion with rephrased_queries, key_terms, synonyms.
    """
    logger.debug(f"Expanding query: '{question[:80]}'")
    try:
        from llm_call_logger import LangChainFileLogger
        structured_llm = llm.with_structured_output(QueryExpansion)
        invoke_config: dict = {}
        if llm_logger and llm_logger._enabled:
            invoke_config = {"callbacks": [LangChainFileLogger(llm_logger, step_prefix="EXPAND_QUERY")]}

        result: QueryExpansion = structured_llm.invoke(
            [
                {"role": "system", "content": _build_expansion_system()},
                {"role": "user", "content": f"Вопрос пользователя: {question}"},
            ],
            config=invoke_config,
        )
        logger.info(
            f"Query expanded\n"
            f"  rephrased ({len(result.rephrased_queries)}): "
            f"{'; '.join(result.rephrased_queries[:2])}...\n"
            f"  key_terms: {result.key_terms}\n"
            f"  synonyms:  {result.synonyms}"
        )
        return result
    except Exception as exc:
        logger.warning(f"Query expansion failed ({exc}), using fallback")
        return QueryExpansion(
            rephrased_queries=[question],
            key_terms=[],
            synonyms=[],
        )


def evaluate_answer(
    llm: ChatOllama,
    question: str,
    answer: str,
    pass_number: int = 1,
    llm_logger: Any | None = None,
) -> AnswerEvaluation:
    """Ask LLM to score relevance and completeness of the given answer.

    When llm_logger is provided, the full LLM prompt/response is written to the
    log file under the [EVALUATE_PASS_N] step label.

    Args:
        llm:         ChatOllama instance.
        question:    Original user question.
        answer:      Agent's generated answer to evaluate.
        pass_number: Which pass this evaluation belongs to (1 or 2) — used in log label.
        llm_logger:  Optional LlmCallLogger for file logging.

    Returns:
        AnswerEvaluation with int scores, missing_aspects list, and reasoning.
    """
    logger.debug(f"Evaluating pass {pass_number} answer (len={len(answer)}) for: '{question[:60]}'")
    try:
        from llm_call_logger import LangChainFileLogger
        structured_llm = llm.with_structured_output(AnswerEvaluation)
        invoke_config: dict = {}
        if llm_logger and llm_logger._enabled:
            invoke_config = {
                "callbacks": [
                    LangChainFileLogger(llm_logger, step_prefix=f"EVALUATE_PASS_{pass_number}")
                ]
            }

        result: AnswerEvaluation = structured_llm.invoke(
            [
                {"role": "system", "content": _build_evaluation_system()},
                {
                    "role": "user",
                    "content": (
                        f"Вопрос: {question}\n\n"
                        f"Ответ агента:\n{answer}\n\n"
                        f"Оцени качество ответа."
                    ),
                },
            ],
            config=invoke_config,
        )
        logger.info(
            f"Pass {pass_number} evaluated\n"
            f"  relevance={result.relevance_score}/10  "
            f"completeness={result.completeness_score}/10\n"
            f"  missing ({len(result.missing_aspects)}): "
            f"{result.missing_aspects[:3]}"
        )
        return result
    except Exception as exc:
        logger.warning(f"Answer evaluation failed ({exc}), using fallback")
        return AnswerEvaluation(
            relevance_score=5,
            completeness_score=5,
            missing_aspects=[],
            reasoning="Оценка недоступна (ошибка LLM)",
        )


# ---------------------------------------------------------------------------
# Query builders
# ---------------------------------------------------------------------------

def build_expanded_query(question: str, expansion: QueryExpansion) -> str:
    """Wrap the original question with explicit tool-call instructions per term.

    Key terms are searched using a two-level exact-search strategy:
      1. multi_term_exact_search([all terms]) — single call that ranks chunks by
         how many terms they contain; ALL-term matches appear first.
      2. Individual exact_search per term — supplementary fallback for terms
         that may not appear together in the same chunk.

    Each synonym and rephrased query is listed as a separate semantic_search call.
    The format mirrors the agent's own tool syntax to make intentions unambiguous.

    Args:
        question:  Original user question.
        expansion: Generated QueryExpansion.

    Returns:
        Multi-line string to use as HumanMessage content.
    """
    parts: list[str] = [
        question,
        "",
        "━━━ ОБЯЗАТЕЛЬНЫЕ ПОИСКОВЫЕ ДЕЙСТВИЯ ━━━",
        "Выполни ВСЕ вызовы ниже ПЕРЕД формированием ответа. "
        "Каждый пункт — отдельный вызов инструмента.",
        "",
    ]

    if expansion.key_terms:
        terms_json = repr(expansion.key_terms)
        parts.append(
            f"🎯 ШАГ 1 — multi_term_exact_search по ВСЕМ терминам сразу (1 вызов):\n"
            f"  1. multi_term_exact_search({terms_json})\n"
            f"     ↳ результаты ранжированы: сначала чанки со ВСЕМИ {len(expansion.key_terms)} терминами,\n"
            f"       затем с большинством, затем с меньшинством.\n"
            f"     ↳ изучи результаты сверху вниз — верхние наиболее релевантны."
        )
        parts.append("")

        parts.append(
            f"🎯 ШАГ 2 — дополнительный exact_search по каждому термину отдельно"
            f" ({len(expansion.key_terms)} вызовов):"
        )
        for i, term in enumerate(expansion.key_terms, 1):
            parts.append(f'  {i}. exact_search("{term}")')
        parts.append("")

    if expansion.rephrased_queries:
        parts.append(f"🔄 semantic_search — каждая формулировка отдельным вызовом ({len(expansion.rephrased_queries)} вызовов):")
        for i, q in enumerate(expansion.rephrased_queries, 1):
            parts.append(f'  {i}. semantic_search("{q}")')
        parts.append("")

    if expansion.synonyms:
        parts.append(f"🔍 semantic_search — каждый синоним отдельным вызовом ({len(expansion.synonyms)} вызовов):")
        for i, s in enumerate(expansion.synonyms, 1):
            parts.append(f'  {i}. semantic_search("{s}")')
        parts.append("")

    if expansion.regex_patterns:
        parts.append(f"🔎 regex_search — каждый паттерн отдельным вызовом ({len(expansion.regex_patterns)} вызовов):")
        for i, p in enumerate(expansion.regex_patterns, 1):
            parts.append(f'  {i}. regex_search("{p}")')
        parts.append("")

    # +1 for the multi_term call
    total = (1 if expansion.key_terms else 0) + len(expansion.key_terms) + len(expansion.rephrased_queries) + len(expansion.synonyms) + len(expansion.regex_patterns)
    parts.append(
        f"Итого ожидается не менее {total} вызовов инструментов "
        f"(включая 1 multi_term_exact_search + {len(expansion.key_terms)} exact_search). "
        "Только после их выполнения формируй финальный ответ."
    )
    return "\n".join(parts)


def build_refined_query(
    question: str,
    expansion: QueryExpansion,
    evaluation: AnswerEvaluation,
) -> str:
    """Build a second-pass query focusing on missing aspects with explicit call list.

    Uses the latter half of rephrased_queries (assumes first half was tried in pass 1).
    Adds missing_aspects as high-priority exact_search and semantic_search calls.

    Args:
        question:   Original user question.
        expansion:  QueryExpansion from the first pass.
        evaluation: AnswerEvaluation from the first pass.

    Returns:
        Refined multi-line HumanMessage payload with numbered call obligations.
    """
    parts: list[str] = [
        question,
        "",
        "━━━ УТОЧНЁННЫЙ ПОИСК — ФОКУС НА ПРОБЕЛАХ ━━━",
        "Предыдущий поиск оказался неполным. "
        "Выполни ВСЕ вызовы ниже ПЕРЕД формированием ответа.",
        "",
    ]

    call_n = 0

    # High-priority: missing aspects as exact + semantic
    if evaluation.missing_aspects:
        parts.append(f"❗ ПРИОРИТЕТ — нераскрытые аспекты ({len(evaluation.missing_aspects)} аспекта/ов):")
        for aspect in evaluation.missing_aspects:
            call_n += 1
            parts.append(f'  {call_n}. exact_search("{aspect}")')
        for aspect in evaluation.missing_aspects:
            call_n += 1
            parts.append(f'  {call_n}. semantic_search("{aspect}")')
        parts.append("")

    # Second half of rephrased queries (first half used in pass 1)
    alt_queries = expansion.rephrased_queries[len(expansion.rephrased_queries) // 2:]
    if alt_queries:
        parts.append(f"🔄 Дополнительные semantic_search ({len(alt_queries)} вызовов):")
        for q in alt_queries:
            call_n += 1
            parts.append(f'  {call_n}. semantic_search("{q}")')
        parts.append("")

    if expansion.key_terms:
        parts.append(f"🎯 exact_search по ключевым терминам ({len(expansion.key_terms)} вызовов):")
        for term in expansion.key_terms:
            call_n += 1
            parts.append(f'  {call_n}. exact_search("{term}")')
        parts.append("")

    if expansion.synonyms:
        parts.append(f"🔍 semantic_search по синонимам ({len(expansion.synonyms)} вызовов):")
        for s in expansion.synonyms:
            call_n += 1
            parts.append(f'  {call_n}. semantic_search("{s}")')
        parts.append("")

    if expansion.regex_patterns:
        parts.append(f"🔎 regex_search по паттернам ({len(expansion.regex_patterns)} вызовов):")
        for p in expansion.regex_patterns:
            call_n += 1
            parts.append(f'  {call_n}. regex_search("{p}")')
        parts.append("")

    parts.append(f"Итого ожидается не менее {call_n} вызовов инструментов. "
                 "Не повторяй запросы из прохода 1. "
                 "Только после выполнения всех вызовов формируй финальный ответ.")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Score helpers
# ---------------------------------------------------------------------------

def _avg_score(ev: AnswerEvaluation) -> float:
    """Return average of relevance and completeness scores."""
    return (ev.relevance_score + ev.completeness_score) / 2.0


def _quality_delta(ev1: AnswerEvaluation, ev2: AnswerEvaluation) -> tuple[bool, str]:
    """Compare two evaluations.

    Returns:
        Tuple of (improved: bool, delta_label: str).
        delta_label is one of: "улучшился", "ухудшился", "остался прежним".
    """
    s1, s2 = _avg_score(ev1), _avg_score(ev2)
    if s2 > s1 + 0.5:
        return True, "улучшился"
    if s2 < s1 - 0.5:
        return False, "ухудшился"
    return False, "остался прежним"


# ---------------------------------------------------------------------------
# Helpers for stage detail messages
# ---------------------------------------------------------------------------

def _expansion_details(question: str, expansion: QueryExpansion) -> str:
    """Format a compact QueryExpansion summary for log_stage details."""
    lines = [f"Вопрос: {question[:120]}"]
    if expansion.rephrased_queries:
        lines.append(f"Перефразировки ({len(expansion.rephrased_queries)}):")
        for i, q in enumerate(expansion.rephrased_queries, 1):
            lines.append(f"  {i}. {q}")
    if expansion.key_terms:
        lines.append(f"Ключевые термины: {', '.join(expansion.key_terms)}")
    if expansion.synonyms:
        lines.append(f"Синонимы: {', '.join(expansion.synonyms)}")
    if expansion.regex_patterns:
        lines.append(f"Regex-паттерны: {', '.join(expansion.regex_patterns)}")
    return "\n".join(lines)


def _evaluation_details(ev: AnswerEvaluation, pass_number: int) -> str:
    """Format an AnswerEvaluation summary for log_stage details."""
    lines = [
        f"Проход {pass_number}:  relevance={ev.relevance_score}/10  "
        f"completeness={ev.completeness_score}/10  avg={_avg_score(ev):.1f}",
        f"Вывод: {ev.reasoning}",
    ]
    if ev.missing_aspects:
        lines.append(f"Нераскрытые аспекты ({len(ev.missing_aspects)}):")
        for aspect in ev.missing_aspects:
            lines.append(f"  - {aspect}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_with_refinement(
    agent: Any,
    llm: ChatOllama,
    question: str,
    chat_history: list[BaseMessage],
    memory: Any | None,
    run_agent_fn: Callable,
    llm_logger: Any | None = None,
) -> tuple[RefinementResult, list[dict]]:
    """Two-pass agent run with query expansion, self-evaluation, and refinement.

    Flow:
      1. expand_query() → rephrased queries, key terms, synonyms
      2. Pass 1: agent runs with expanded query (memory hint injected if memory≠None)
      3. evaluate_answer() → relevance + completeness scores + missing aspects
      4. Pass 2: agent runs with refined query focused on missing aspects
      5. evaluate_answer() → scores for pass 2
      6. Compare scores, select best pass, return RefinementResult

    Log file annotations (when llm_logger is provided and enabled):
      Each stage transition is annotated with ### blocks containing:
        - EXPANSION: original question, rephrased variants, key terms, synonyms
        - AGENT PASS: query length, memory hint count, pass direction
        - EVALUATION: relevance/completeness scores, missing aspects
        - REFINEMENT COMPLETE: delta scores, quality change, best pass

    Memory handling:
      - Pass 1 injects memory hint (if memory provided) but does NOT auto-save.
      - Pass 2 has no memory interaction.
      - Caller is responsible for saving the best answer to memory.

    Args:
        agent:        Compiled LangGraph agent.
        llm:          ChatOllama for expansion and evaluation LLM calls.
        question:     Original user question (preserved verbatim for memory).
        chat_history: Session chat history (without system prompt).
        memory:       SessionMemory instance (for hint injection) or None.
        run_agent_fn: Callable with signature
                      run_agent(agent, question, chat_history, memory, save_to_memory)
                      — avoids circular import.
        llm_logger:   Optional LlmCallLogger. When provided and enabled, pipeline
                      stage markers are written to the log file so you can trace
                      how the query context evolves step by step.

    Returns:
        Tuple of (RefinementResult, [raw_result_pass1, raw_result_pass2]).
        raw_result_* are the dicts returned by run_agent_fn (contain 'messages',
        '_all_tool_messages').
    """
    # ── Step 1: Query expansion ─────────────────────────────────────────────
    if llm_logger:
        llm_logger.log_stage(
            "QUERY EXPANSION START",
            f"Вопрос: {question[:200]}",
        )

    expansion = expand_query(llm, question, llm_logger=llm_logger)
    expanded_q = build_expanded_query(question, expansion)

    if llm_logger:
        llm_logger.log_stage(
            "QUERY EXPANSION COMPLETE",
            _expansion_details(question, expansion),
        )

    # ── Pass 1: expanded query, memory hint injected, no auto-save ──────────
    mem_hint_note = "с подсказкой из памяти" if memory else "без памяти"
    if llm_logger:
        llm_logger.log_stage(
            "AGENT PASS 1 START  (расширенный запрос)",
            f"Длина запроса: {len(expanded_q)} символов\n"
            f"Режим памяти: {mem_hint_note}\n"
            f"Начало запроса: {expanded_q[:300]}",
        )

    raw1: dict = run_agent_fn(
        agent=agent,
        question=expanded_q,
        chat_history=chat_history,
        memory=memory,
        save_to_memory=False,
    )
    msgs1 = raw1.get("messages", [])
    answer1 = ""
    if msgs1:
        last = msgs1[-1]
        answer1 = last.content if hasattr(last, "content") else str(last)
    tc1 = len(raw1.get("_all_tool_messages", []))

    if llm_logger:
        llm_logger.log_stage(
            "AGENT PASS 1 COMPLETE",
            f"Инструментов вызвано: {tc1}\n"
            f"Длина ответа: {len(answer1)} символов",
        )

    # ── Evaluate pass 1 ─────────────────────────────────────────────────────
    if llm_logger:
        llm_logger.log_stage("EVALUATION 1 START")

    ev1 = evaluate_answer(llm, question, answer1, pass_number=1, llm_logger=llm_logger)
    iter1 = IterationResult(
        pass_number=1,
        expanded_query=expanded_q,
        answer=answer1,
        tool_calls_count=tc1,
        evaluation=ev1,
    )

    if llm_logger:
        llm_logger.log_stage(
            "EVALUATION 1 COMPLETE",
            _evaluation_details(ev1, 1),
        )

    # ── Step 2: Refined query based on evaluation gaps ───────────────────────
    refined_q = build_refined_query(question, expansion, ev1)

    if llm_logger:
        llm_logger.log_stage(
            "AGENT PASS 2 START  (уточнённый запрос, фокус на пробелах)",
            f"Нераскрытых аспектов из оценки: {len(ev1.missing_aspects)}\n"
            f"Длина запроса: {len(refined_q)} символов\n"
            f"Фокус: {'; '.join(ev1.missing_aspects[:5]) or '—'}\n"
            f"Начало запроса: {refined_q[:300]}",
        )

    # ── Pass 2: refined query, no memory, no auto-save ──────────────────────
    raw2: dict = run_agent_fn(
        agent=agent,
        question=refined_q,
        chat_history=chat_history,
        memory=None,
        save_to_memory=False,
    )
    msgs2 = raw2.get("messages", [])
    answer2 = ""
    if msgs2:
        last = msgs2[-1]
        answer2 = last.content if hasattr(last, "content") else str(last)
    tc2 = len(raw2.get("_all_tool_messages", []))

    if llm_logger:
        llm_logger.log_stage(
            "AGENT PASS 2 COMPLETE",
            f"Инструментов вызвано: {tc2}\n"
            f"Длина ответа: {len(answer2)} символов",
        )

    # ── Evaluate pass 2 ─────────────────────────────────────────────────────
    if llm_logger:
        llm_logger.log_stage("EVALUATION 2 START")

    ev2 = evaluate_answer(llm, question, answer2, pass_number=2, llm_logger=llm_logger)
    iter2 = IterationResult(
        pass_number=2,
        expanded_query=refined_q,
        answer=answer2,
        tool_calls_count=tc2,
        evaluation=ev2,
    )

    if llm_logger:
        llm_logger.log_stage(
            "EVALUATION 2 COMPLETE",
            _evaluation_details(ev2, 2),
        )

    # ── Compare and select best ──────────────────────────────────────────────
    improved, delta = _quality_delta(ev1, ev2)
    best_pass = 2 if _avg_score(ev2) >= _avg_score(ev1) else 1
    final_answer = answer2 if best_pass == 2 else answer1
    final_ev = ev2 if best_pass == 2 else ev1

    avg1, avg2 = _avg_score(ev1), _avg_score(ev2)
    delta_sign = f"+{avg2 - avg1:.1f}" if avg2 >= avg1 else f"{avg2 - avg1:.1f}"

    if llm_logger:
        llm_logger.log_stage(
            "REFINEMENT COMPLETE",
            f"Проход 1: relevance={ev1.relevance_score}/10  "
            f"completeness={ev1.completeness_score}/10  avg={avg1:.1f}\n"
            f"Проход 2: relevance={ev2.relevance_score}/10  "
            f"completeness={ev2.completeness_score}/10  avg={avg2:.1f}\n"
            f"Изменение качества: {delta}  ({delta_sign} avg)  |  лучший проход: {best_pass}",
        )

    logger.info(
        f"Refinement завершён\n"
        f"  Проход 1: relevance={ev1.relevance_score}/10  "
        f"completeness={ev1.completeness_score}/10  avg={avg1:.1f}\n"
        f"  Проход 2: relevance={ev2.relevance_score}/10  "
        f"completeness={ev2.completeness_score}/10  avg={avg2:.1f}\n"
        f"  Качество: {delta} ({delta_sign})  (лучший проход: {best_pass})"
    )

    result = RefinementResult(
        original_question=question,
        iterations=[iter1, iter2],
        best_pass=best_pass,
        quality_improved=improved,
        quality_delta=delta,
        final_answer=final_answer,
        final_evaluation=final_ev,
    )
    return result, [raw1, raw2]

