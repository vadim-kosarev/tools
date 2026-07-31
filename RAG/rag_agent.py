"""
RAG-агент по локальной документации: локальная LLM (Ollama) + инструменты базы знаний.

Агент сам выбирает инструменты из kb_tools (поиск по смыслу, точный поиск, regex,
навигация по разделам), выполняет их против ClickHouse и формирует ответ со
ссылками на разделы-источники. Цикл выбора инструментов ограничен по числу
итераций: на последней итерации модель обязана ответить без вызова инструментов.

Всё работает локально: ни один запрос не уходит за пределы Ollama и ClickHouse.

Использование:
    python rag_agent.py                              # справка
    python rag_agent.py ask "что такое КЦОИ"         # одиночный вопрос
    python rag_agent.py chat                         # интерактивный режим
    python rag_agent.py tools                        # список доступных инструментов
    python rag_agent.py --debug ask "..."            # DEBUG-логи

Переменные окружения (.env), сверх общих настроек rag_chat.py:
    AGENT_MAX_ITERATIONS   — максимум раундов вызова инструментов (по умолчанию 6)
    AGENT_MAX_TOOL_CHARS   — предел символов результата инструмента для LLM (12000)
    OLLAMA_TIMEOUT         — таймаут запроса к Ollama в секундах (300)
"""

import argparse
import sys


def _build_parser() -> argparse.ArgumentParser:
    """CLI агента. Строится до тяжёлых импортов, чтобы --help отвечал мгновенно."""
    parser = argparse.ArgumentParser(
        description="RAG-агент по локальной документации (Ollama + ClickHouse, полностью локально)",
    )
    parser.add_argument("--debug", action="store_true", help="Включить DEBUG-логирование")
    parser.add_argument("--env", metavar="PATH", help="Путь к .env файлу (по умолчанию .env рядом со скриптом)")

    subparsers = parser.add_subparsers(dest="command", metavar="command")

    ask_parser = subparsers.add_parser("ask", help="Задать один вопрос и напечатать ответ")
    ask_parser.add_argument("question", nargs="+", help="Текст вопроса")
    ask_parser.add_argument("--show-tools", action="store_true", help="Показать вызванные инструменты")

    subparsers.add_parser("chat", help="Интерактивный диалог в консоли")
    subparsers.add_parser("tools", help="Показать список доступных инструментов")

    return parser


if __name__ == "__main__":
    _parser = _build_parser()
    _args = _parser.parse_args()
    if not getattr(_args, "command", None):
        _parser.print_help()
        sys.exit(0)


# ---------------------------------------------------------------------------
# Тяжёлые импорты — только если команда задана
# ---------------------------------------------------------------------------

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langchain_ollama import ChatOllama
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Модели данных
# ---------------------------------------------------------------------------

class ToolInvocation(BaseModel):
    """Один вызов инструмента внутри цикла агента."""

    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    ok: bool = True
    duration_ms: int = 0
    error: str = ""
    result_chars: int = 0


class SectionRef(BaseModel):
    """Ссылка на раздел документа, попавший в контекст ответа."""

    source: str
    section: str = ""

    def as_text(self) -> str:
        return f"[{self.source}] {self.section}".strip()


class AgentAnswer(BaseModel):
    """Результат работы агента на один вопрос."""

    question: str
    answer: str
    iterations: int = 0
    hit_iteration_limit: bool = False
    tool_calls: list[ToolInvocation] = Field(default_factory=list)
    sections: list[SectionRef] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Системный промпт
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
Ты — аналитик по корпоративной документации. У тебя есть инструменты доступа к базе
знаний, построенной по локальным документам. Отвечай только на русском языке.

Как работать:
1. Сначала найди информацию инструментами, потом отвечай. Не выдумывай факты.
2. Выбирай инструмент под задачу:
   - semantic_search — общие и концептуальные вопросы («как устроено», «зачем»);
   - exact_search / multi_term_exact_search — конкретные термины, названия, коды;
   - search_section_by_name — когда известно название раздела или тема;
   - regex_search — IP-адреса, порты, VLAN, номера документов и прочие шаблоны;
   - search_abbreviation — расшифровка аббревиатуры;
   - get_section_content — полный текст найденного раздела;
   - get_neighbor_chunks / get_chunks_by_index — добрать контекст вокруг фрагмента;
   - read_table — значения из таблицы;
   - list_sources / list_sections / list_all_sections — если непонятно, где искать.
3. Если поиск ничего не дал — переформулируй запрос или смени инструмент, но не
   более двух дополнительных попыток на один подход.
4. Нашёл фрагмент, но он обрывается — добери контекст через get_neighbor_chunks.

Формат ответа:
- Сначала прямой ответ по существу, структурированно.
- Приводи конкретные значения (адреса, названия, версии) точно как в документе.
- В конце блок «Источники» со списком разделов вида «[файл] — раздел».
- Если данных в базе знаний нет, так и скажи: не додумывай.
"""

FINAL_ANSWER_NUDGE = """\
Достигнут лимит вызовов инструментов. Сформулируй лучший возможный ответ на основе
уже полученных данных. Инструменты больше не вызывай. Если данных не хватило —
честно скажи, что именно найти не удалось.
"""


# ---------------------------------------------------------------------------
# Агент
# ---------------------------------------------------------------------------

class KnowledgeBaseAgent:
    """Tool-calling агент над инструментами базы знаний с ограниченным циклом.

    Цикл: LLM выбирает инструменты -> инструменты выполняются -> результаты
    возвращаются модели. После `max_iterations` раундов модель принудительно
    переводится в режим финального ответа без инструментов.
    """

    def __init__(
        self,
        llm: ChatOllama,
        tools: list[BaseTool],
        max_iterations: int = 6,
        max_tool_chars: int = 12_000,
    ) -> None:
        self._tools = {tool.name: tool for tool in tools}
        self._llm_with_tools = llm.bind_tools(tools)
        self._llm_plain = llm
        self._max_iterations = max(1, max_iterations)
        self._max_tool_chars = max(500, max_tool_chars)

    @property
    def tool_names(self) -> list[str]:
        return list(self._tools)

    # -- Выполнение инструментов ---------------------------------------------

    def _run_tool(self, name: str, arguments: dict[str, Any]) -> tuple[str, ToolInvocation, list[SectionRef]]:
        """Выполняет инструмент, возвращая (текст для LLM, запись о вызове, разделы)."""
        started = time.monotonic()
        tool = self._tools.get(name)
        if tool is None:
            invocation = ToolInvocation(name=name, arguments=arguments, ok=False,
                                        error="unknown tool")
            available = ", ".join(sorted(self._tools))
            return f"Инструмент '{name}' не существует. Доступны: {available}", invocation, []

        try:
            raw_result = tool.invoke(arguments)
        except Exception as exc:
            duration = int((time.monotonic() - started) * 1000)
            logger.warning(f"Инструмент {name} завершился ошибкой: {exc}")
            invocation = ToolInvocation(name=name, arguments=arguments, ok=False,
                                        duration_ms=duration, error=str(exc))
            return f"Ошибка вызова '{name}': {exc}", invocation, []

        duration = int((time.monotonic() - started) * 1000)
        payload = _to_jsonable(raw_result)
        sections = _extract_sections(payload)
        text = _truncate(json.dumps(payload, ensure_ascii=False, indent=2), self._max_tool_chars)

        invocation = ToolInvocation(name=name, arguments=arguments, ok=True,
                                    duration_ms=duration, result_chars=len(text))
        logger.info(f"Инструмент {name} -> {len(text)} символов за {duration} мс")
        return text, invocation, sections

    # -- Основной цикл --------------------------------------------------------

    def ask(self, question: str, history: Optional[list[BaseMessage]] = None) -> AgentAnswer:
        """Отвечает на вопрос, самостоятельно выбирая и вызывая инструменты."""
        messages: list[BaseMessage] = [SystemMessage(content=SYSTEM_PROMPT)]
        if history:
            messages.extend(history)
        messages.append(HumanMessage(content=question))

        result = AgentAnswer(question=question, answer="")
        seen_sections: dict[str, SectionRef] = {}

        for iteration in range(1, self._max_iterations + 1):
            result.iterations = iteration
            logger.debug(f"Итерация {iteration}/{self._max_iterations}")

            response = self._llm_with_tools.invoke(messages)
            messages.append(response)

            tool_calls = _tool_calls_of(response)
            if not tool_calls:
                result.answer = _text_of(response)
                result.sections = list(seen_sections.values())
                return result

            for call in tool_calls:
                name = call.get("name", "")
                arguments = call.get("args") or {}
                text, invocation, sections = self._run_tool(name, arguments)
                result.tool_calls.append(invocation)
                for ref in sections:
                    seen_sections.setdefault(ref.as_text(), ref)
                messages.append(ToolMessage(
                    content=text,
                    tool_call_id=call.get("id") or name,
                    name=name,
                ))

        # Лимит итераций исчерпан — вынуждаем финальный ответ без инструментов
        logger.info(f"Достигнут лимит итераций ({self._max_iterations}), запрашиваем финальный ответ")
        messages.append(HumanMessage(content=FINAL_ANSWER_NUDGE))
        final = self._llm_plain.invoke(messages)

        result.hit_iteration_limit = True
        result.answer = _text_of(final)
        result.sections = list(seen_sections.values())
        return result


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def _tool_calls_of(message: BaseMessage) -> list[dict[str, Any]]:
    """Извлекает вызовы инструментов из ответа модели (пустой список — финальный ответ)."""
    if isinstance(message, AIMessage) and message.tool_calls:
        return list(message.tool_calls)
    return []


def _text_of(message: BaseMessage) -> str:
    """Текст ответа модели: content бывает строкой или списком блоков."""
    content = message.content
    if isinstance(content, str):
        return content.strip()
    parts = [
        block.get("text", "") if isinstance(block, dict) else str(block)
        for block in content
    ]
    return "".join(parts).strip()


def _to_jsonable(value: Any) -> Any:
    """Приводит результат инструмента (обычно Pydantic-модель) к JSON-совместимому виду."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _to_jsonable(item) for key, item in value.items()}
    return value


def _extract_sections(payload: Any) -> list[SectionRef]:
    """Собирает пары (source, section) из результата инструмента любой формы."""
    found: dict[str, SectionRef] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            source = node.get("source")
            if isinstance(source, str) and source:
                ref = SectionRef(source=source, section=str(node.get("section") or ""))
                found.setdefault(ref.as_text(), ref)
            for item in node.values():
                walk(item)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return list(found.values())


def _truncate(text: str, limit: int) -> str:
    """Обрезает длинный результат инструмента, оставляя пометку об усечении."""
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n... [результат усечён: {len(text)} символов, показано {limit}]"


# ---------------------------------------------------------------------------
# Сборка агента
# ---------------------------------------------------------------------------

def build_agent() -> KnowledgeBaseAgent:
    """Создаёт агента: подключение к ClickHouse, инструменты, локальная LLM."""
    from kb_tools import create_kb_tools
    from rag_chat import build_vectorstore, settings

    vectorstore = build_vectorstore(force_reindex=False)
    tools = create_kb_tools(vectorstore, semantic_top_k=settings.retriever_top_k)

    llm = ChatOllama(
        model=settings.ollama_model,
        base_url=settings.ollama_base_url,
        temperature=0.1,
        num_predict=4096,
        client_kwargs={"timeout": settings.ollama_timeout},
    )
    logger.info(
        f"Агент готов\n"
        f"  LLM:         {settings.ollama_model} ({settings.ollama_base_url})\n"
        f"  Инструменты: {len(tools)}\n"
        f"  Итераций:    до {settings.agent_max_iterations}"
    )
    return KnowledgeBaseAgent(
        llm=llm,
        tools=tools,
        max_iterations=settings.agent_max_iterations,
        max_tool_chars=settings.agent_max_tool_chars,
    )


# ---------------------------------------------------------------------------
# Вывод
# ---------------------------------------------------------------------------

SEP = "=" * 70


def print_answer(result: AgentAnswer, show_tools: bool = False) -> None:
    """Печатает ответ агента, источники и (опционально) трассу вызовов."""
    print(f"\n{SEP}")
    print(f"Вопрос: {result.question}")
    print(SEP)
    print(result.answer or "(пустой ответ модели)")

    if result.sections:
        print("\nРазделы, попавшие в контекст:")
        for ref in result.sections:
            print(f"  - {ref.as_text()}")

    if show_tools and result.tool_calls:
        print("\nВызовы инструментов:")
        for call in result.tool_calls:
            status = "ok" if call.ok else f"ошибка: {call.error}"
            print(f"  - {call.name}({_short_args(call.arguments)}) — {status}, {call.duration_ms} мс")

    if result.hit_iteration_limit:
        print(f"\nПримечание: достигнут лимит в {result.iterations} итераций.")
    print(SEP)


def _short_args(arguments: dict[str, Any], limit: int = 80) -> str:
    """Компактное представление аргументов инструмента для консоли."""
    text = ", ".join(f"{key}={value!r}" for key, value in arguments.items())
    return text if len(text) <= limit else text[:limit] + "..."


# ---------------------------------------------------------------------------
# Команды
# ---------------------------------------------------------------------------

def cmd_ask(args: argparse.Namespace) -> None:
    agent = build_agent()
    result = agent.ask(" ".join(args.question))
    print_answer(result, show_tools=args.show_tools)


def cmd_chat(args: argparse.Namespace) -> None:
    agent = build_agent()
    print(f"\n{SEP}")
    print("RAG-агент по локальной документации")
    print("  Вопрос              → агент сам подберёт инструменты")
    print("  exit / quit / выход → выйти")
    print(f"{SEP}\n")

    while True:
        try:
            question = input("Вопрос: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nВыход.")
            return
        if not question:
            continue
        if question.lower() in ("exit", "quit", "выход"):
            print("До свидания!")
            return
        try:
            print_answer(agent.ask(question), show_tools=True)
        except Exception as exc:
            logger.exception("Ошибка обработки вопроса")
            print(f"Ошибка: {exc}")


def cmd_tools(args: argparse.Namespace) -> None:
    from kb_tools import get_tool_registry

    registry = get_tool_registry()
    print(f"\nДоступно инструментов: {len(registry)}\n")
    width = max(len(name) for name in registry)
    for name, description in registry.items():
        print(f"  {name.ljust(width)}  {description}")
    print()


_COMMANDS = {
    "ask":   cmd_ask,
    "chat":  cmd_chat,
    "tools": cmd_tools,
}


def main(args: argparse.Namespace) -> None:
    """Точка входа: настраивает окружение, логирование и выполняет команду."""
    env_path = Path(args.env) if args.env else Path(__file__).with_name(".env")
    load_dotenv(env_path)
    if args.debug:
        os.environ["LOG_LEVEL"] = "DEBUG"

    from logging_config import setup_logging
    setup_logging("rag_agent")

    _COMMANDS[args.command](args)


if __name__ == "__main__":
    main(_args)
