"""
Тестовый скрипт для проверки что semantic_search возвращает score (confidence).
"""

import json
from pathlib import Path
from rag_chat import build_vectorstore, settings
from kb_tools import create_kb_tools


def test_semantic_search_with_score():
    """Тест что semantic_search возвращает score в результатах"""
    print("🔍 Тестирование semantic_search с confidence/score...")

    # Создаем vectorstore и tools
    vectorstore = build_vectorstore(force_reindex=False)
    knowledge_dir = Path(settings.knowledge_dir)
    tools_list = create_kb_tools(vectorstore, knowledge_dir, semantic_top_k=5)

    # Находим semantic_search tool
    semantic_search_tool = None
    for tool in tools_list:
        if tool.name == "semantic_search":
            semantic_search_tool = tool
            break

    if not semantic_search_tool:
        print("❌ semantic_search tool не найден!")
        return False

    # Вызываем semantic_search
    print("\n📝 Запрос: 'АРМ оператора программное обеспечение установлено'")
    result = semantic_search_tool.invoke({
        "query": "АРМ оператора программное обеспечение установлено",
        "top_k": 3
    })

    # Проверяем результат
    print(f"\n✅ Получено результатов: {result.total_found}")

    # Проверяем что у каждого чанка есть score
    has_scores = True
    for i, chunk in enumerate(result.chunks, 1):
        if chunk.score is None:
            print(f"❌ Чанк #{i} не имеет score!")
            has_scores = False
        else:
            print(f"✓ Чанк #{i}:")
            print(f"  - source: {chunk.metadata.source}")
            print(f"  - section: {chunk.metadata.section[:60]}...")
            print(f"  - score: {chunk.score:.4f}")
            print(f"  - content: {chunk.content[:80]}...")

    if has_scores:
        print("\n✅ Все чанки имеют score (confidence)!")

        # Выводим полный JSON для первого результата
        print("\n📄 Пример JSON с score:")
        first_chunk_json = result.chunks[0].model_dump_json(indent=2)
        print(first_chunk_json)

        return True
    else:
        print("\n❌ Некоторые чанки не имеют score!")
        return False


if __name__ == "__main__":
    try:
        success = test_semantic_search_with_score()
        exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        exit(1)

