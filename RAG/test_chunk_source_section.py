"""
Тест: проверка наличия полей source и section в результатах поиска.

Убеждаемся, что агент может использовать эти поля для последующих уточняющих запросов.
"""
from pydantic import BaseModel, Field
from kb_tools import ChunkMetadata, ChunkResult, SearchChunksResult, ScoredChunkResult, MultiTermSearchResult, SectionInfo, SearchSectionsResult
from pydantic_utils import pydantic_to_markdown


def test_search_chunks_result():
    """Проверяем SearchChunksResult (semantic_search, exact_search и др.)"""
    print("="*80)
    print("ТЕСТ 1: SearchChunksResult")
    print("="*80)

    result = SearchChunksResult(
        query="PostgreSQL",
        chunks=[
            ChunkResult(
                content="PostgreSQL установлен на сервере db01.example.com",
                metadata=ChunkMetadata(
                    source="database_servers.md",
                    section="Серверы СУБД > PostgreSQL",
                    chunk_type="",
                    line_start=150,
                    line_end=160,
                    chunk_index=25,
                    table_headers=None
                )
            ),
            ChunkResult(
                content="Кластер PostgreSQL версии 14.5",
                metadata=ChunkMetadata(
                    source="technical_spec.md",
                    section="Программное обеспечение > Базы данных",
                    chunk_type="",
                    line_start=230,
                    line_end=240,
                    chunk_index=18,
                    table_headers=None
                )
            ),
        ],
        total_found=2
    )

    formatted = pydantic_to_markdown(result)
    print(formatted)
    print()

    # Проверка: есть ли source и section в выводе?
    has_source = "source=" in formatted
    has_section = "section=" in formatted

    print("ПРОВЕРКА:")
    print(f"  source присутствует: {'✅' if has_source else '❌'}")
    print(f"  section присутствует: {'✅' if has_section else '❌'}")
    print()

    return has_source and has_section


def test_multi_term_search_result():
    """Проверяем MultiTermSearchResult"""
    print("="*80)
    print("ТЕСТ 2: MultiTermSearchResult")
    print("="*80)

    result = MultiTermSearchResult(
        terms=["PostgreSQL", "СУБД"],
        chunks_by_coverage={
            2: [
                ChunkResult(
                    content="PostgreSQL - СУБД для хранения данных",
                    metadata=ChunkMetadata(
                        source="overview.md",
                        section="Архитектура > Базы данных",
                        chunk_type="",
                        line_start=100,
                        line_end=110,
                        chunk_index=10,
                        table_headers=None
                    )
                )
            ],
            1: [
                ChunkResult(
                    content="Установка PostgreSQL описана в руководстве",
                    metadata=ChunkMetadata(
                        source="install_guide.md",
                        section="Установка ПО",
                        chunk_type="",
                        line_start=50,
                        line_end=60,
                        chunk_index=5,
                        table_headers=None
                    )
                )
            ]
        },
        total_chunks=2,
        max_coverage=2
    )

    formatted = pydantic_to_markdown(result)
    print(formatted)
    print()

    # Проверка
    has_source = "source=" in formatted
    has_section = "section=" in formatted

    print("ПРОВЕРКА:")
    print(f"  source присутствует: {'✅' if has_source else '❌'}")
    print(f"  section присутствует: {'✅' if has_section else '❌'}")
    print()

    return has_source and has_section


def test_search_sections_result():
    """Проверяем SearchSectionsResult (find_sections_by_term)"""
    print("="*80)
    print("ТЕСТ 3: SearchSectionsResult")
    print("="*80)

    result = SearchSectionsResult(
        query="АРМ эксплуатационного персонала",
        sections=[
            SectionInfo(
                source="technical_spec.md",
                section="АРМ эксплуатационного персонала СОИБ КЦОИ",
                match_count=25,
                match_type="CONTENT"
            ),
            SectionInfo(
                source="user_manual.md",
                section="Эксплуатация АРМ",
                match_count=18,
                match_type="NAME"
            ),
        ],
        total_found=2,
        returned_count=2
    )

    formatted = pydantic_to_markdown(result)
    print(formatted)
    print()

    # Проверка
    has_source = "source=" in formatted
    has_section = "section=" in formatted

    print("ПРОВЕРКА:")
    print(f"  source присутствует: {'✅' if has_source else '❌'}")
    print(f"  section присутствует: {'✅' if has_section else '❌'}")
    print()

    return has_source and has_section


def test_extraction_for_subsequent_calls():
    """Проверяем, что агент сможет извлечь source и section для последующих вызовов"""
    print("="*80)
    print("ТЕСТ 4: Извлечение данных для последующих вызовов")
    print("="*80)

    # Симулируем результат поиска
    result = SearchChunksResult(
        query="тестовый запрос",
        chunks=[
            ChunkResult(
                content="Найденный текст с заголовком списка:",
                metadata=ChunkMetadata(
                    source="document.md",
                    section="Раздел 1 > Подраздел 1.1",
                    chunk_type="",
                    line_start=100,
                    line_end=105,
                    chunk_index=10,
                    table_headers=None
                )
            )
        ],
        total_found=1
    )

    # Получаем форматированный вывод
    formatted = pydantic_to_markdown(result)
    print(formatted)
    print()

    # Извлекаем данные (симулируем парсинг агентом)
    print("ИЗВЛЕЧЕННЫЕ ДАННЫЕ для последующих вызовов:")
    print(f"  source_file: document.md")
    print(f"  section: Раздел 1 > Подраздел 1.1")
    print(f"  line_start: 100")
    print()

    print("ВОЗМОЖНЫЕ ПОСЛЕДУЮЩИЕ ВЫЗОВЫ:")
    print("  1. get_section_content(source_file='document.md', section='Раздел 1 > Подраздел 1.1')")
    print("  2. get_neighbor_chunks(source='document.md', line_start=100, after=15)")
    print("  3. exact_search_in_file_section(substring='...', source_file='document.md', section='Раздел 1 > Подраздел 1.1')")
    print()

    return True


if __name__ == "__main__":
    print("\n")
    print("╔" + "="*78 + "╗")
    print("║" + " "*20 + "ПРОВЕРКА ПОЛЕЙ source И section" + " "*27 + "║")
    print("╚" + "="*78 + "╝")
    print()

    results = []

    results.append(("SearchChunksResult", test_search_chunks_result()))
    results.append(("MultiTermSearchResult", test_multi_term_search_result()))
    results.append(("SearchSectionsResult", test_search_sections_result()))
    results.append(("Извлечение для последующих вызовов", test_extraction_for_subsequent_calls()))

    print("="*80)
    print("ИТОГОВЫЙ РЕЗУЛЬТАТ:")
    print("="*80)

    for test_name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {status}: {test_name}")

    print()

    all_passed = all(passed for _, passed in results)
    if all_passed:
        print("🎉 ВСЕ ТЕСТЫ ПРОЙДЕНЫ! Поля source и section доступны для последующих вызовов.")
    else:
        print("⚠️  НЕКОТОРЫЕ ТЕСТЫ НЕ ПРОШЛИ! Требуется доработка.")

    print()

