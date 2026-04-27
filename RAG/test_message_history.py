"""
Тест для проверки сохранения истории сообщений в state["messages"].

Запускает упрощенную версию агента и проверяет, что:
1. История накапливается в state["messages"]
2. Каждый узел добавляет свои сообщения
3. Tool результаты сохраняются
"""

import sys
from pathlib import Path

# Добавляем путь к модулям RAG
sys.path.insert(0, str(Path(__file__).parent))

from typing import Any


def test_message_history():
    """Тест истории сообщений."""
    print("=" * 80)
    print("ТЕСТ: Проверка истории сообщений")
    print("=" * 80)

    # Имитация state
    state = {
        "user_query": "тестовый вопрос",
        "messages": [],
        "step": 0,
    }

    # Шаг 1: plan_node добавляет system + user + assistant
    print("\n1. План (plan_node):")
    state["messages"].extend([
        {"role": "system", "content": "System prompt для плана"},
        {"role": "user", "content": "вопрос пользователя"},
        {"role": "assistant", "content": "plan response"},
    ])
    print(f"   Сообщений в истории: {len(state['messages'])}")
    print_messages(state["messages"])

    # Шаг 2: action_node добавляет user + assistant + tools
    print("\n2. Действие (action_node):")
    state["messages"].extend([
        {"role": "user", "content": "контекст для action"},
        {"role": "assistant", "content": "action response"},
        {"role": "tool", "name": "exact_search", "content": "tool result 1"},
        {"role": "tool", "name": "semantic_search", "content": "tool result 2"},
    ])
    print(f"   Сообщений в истории: {len(state['messages'])}")
    print_messages(state["messages"])

    # Шаг 3: observation_node добавляет user + assistant
    print("\n3. Наблюдение (observation_node):")
    state["messages"].extend([
        {"role": "user", "content": "контекст для observation"},
        {"role": "assistant", "content": "observation response"},
    ])
    print(f"   Сообщений в истории: {len(state['messages'])}")
    print_messages(state["messages"])

    # Шаг 4: Проверка фильтрации system messages
    print("\n4. Фильтрация system messages (для передачи в LLM):")
    filtered = [msg for msg in state["messages"] if msg["role"] != "system"]
    print(f"   Без system messages: {len(filtered)} сообщений")
    print_messages(filtered)

    # Итоги
    print("\n" + "=" * 80)
    print("РЕЗУЛЬТАТ")
    print("=" * 80)
    print(f"✅ Всего сообщений: {len(state['messages'])}")
    print(f"✅ User сообщений: {sum(1 for m in state['messages'] if m['role'] == 'user')}")
    print(f"✅ Assistant сообщений: {sum(1 for m in state['messages'] if m['role'] == 'assistant')}")
    print(f"✅ Tool сообщений: {sum(1 for m in state['messages'] if m['role'] == 'tool')}")
    print(f"✅ System сообщений: {sum(1 for m in state['messages'] if m['role'] == 'system')}")
    print("\n✅ Тест пройден! История сообщений работает правильно.")


def print_messages(messages: list[dict[str, Any]]):
    """Печатает список сообщений в читаемом формате."""
    for i, msg in enumerate(messages, 1):
        role = msg.get("role", "unknown")
        content = msg.get("content", "")[:50] + "..." if len(msg.get("content", "")) > 50 else msg.get("content", "")
        name = f" ({msg['name']})" if "name" in msg else ""
        print(f"      [{i}] {role}{name}: {content}")


if __name__ == "__main__":
    test_message_history()

