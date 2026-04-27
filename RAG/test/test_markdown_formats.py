"""
Демонстрация двух форматов вывода результатов поиска.
"""
from kb_tools import ChunkMetadata, ChunkResult, SearchChunksResult, MultiTermSearchResult
from pydantic_utils import pydantic_to_markdown, pydantic_to_markdown_detailed


# Создаем тестовые данные
test_result = SearchChunksResult(
    query="PostgreSQL",
    chunks=[
        ChunkResult(
            content="PostgreSQL СУБД установлена на серверах db01, db02 и db03. Версия 14.5 Enterprise Edition.",
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
            content="Кластер PostgreSQL включает в себя: Primary (db01), Standby (db02, db03), Pooler (pgbouncer)",
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
        ChunkResult(
            content="Настройки подключения к PostgreSQL: порт 5432, SSL обязателен, max_connections=200",
            metadata=ChunkMetadata(
                source="configuration.md",
                section="Конфигурация > База данных > PostgreSQL",
                chunk_type="",
                line_start=75,
                line_end=85,
                chunk_index=8,
                table_headers=None
            )
        ),
    ],
    total_found=3
)

test_multiterm = MultiTermSearchResult(
    terms=["PostgreSQL", "СУБД", "репликация"],
    chunks_by_coverage={
        3: [
            ChunkResult(
                content="PostgreSQL СУБД с настроенной репликацией между узлами",
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
        2: [
            ChunkResult(
                content="Установка и настройка PostgreSQL СУБД согласно документации",
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
        ],
        1: [
            ChunkResult(
                content="Репликация данных между серверами осуществляется в реальном времени",
                metadata=ChunkMetadata(
                    source="replication.md",
                    section="Репликация > Механизмы",
                    chunk_type="",
                    line_start=120,
                    line_end=130,
                    chunk_index=15,
                    table_headers=None
                )
            )
        ]
    },
    total_chunks=3,
    max_coverage=3
)

print("╔" + "="*78 + "╗")
print("║" + " "*20 + "ФОРМАТ 1: КОМПАКТНЫЙ (текущий)" + " "*21 + "║")
print("╚" + "="*78 + "╝\n")

print(pydantic_to_markdown(test_result))

print("\n\n" + "="*80 + "\n\n")

print("╔" + "="*78 + "╗")
print("║" + " "*20 + "ФОРМАТ 2: ТАБЛИЧНЫЙ (новый)" + " "*24 + "║")
print("╚" + "="*78 + "╝\n")

print(pydantic_to_markdown_detailed(test_result))

print("\n\n" + "="*80 + "\n\n")

print("╔" + "="*78 + "╗")
print("║" + " "*15 + "MultiTermSearchResult - КОМПАКТНЫЙ" + " "*22 + "║")
print("╚" + "="*78 + "╝\n")

print(pydantic_to_markdown(test_multiterm))

print("\n\n" + "="*80 + "\n\n")

print("╔" + "="*78 + "╗")
print("║" + " "*15 + "MultiTermSearchResult - ТАБЛИЧНЫЙ" + " "*23 + "║")
print("╚" + "="*78 + "╝\n")

print(pydantic_to_markdown_detailed(test_multiterm))

print("\n\n" + "="*80)
print("ИТОГ:")
print("="*80)
print("""
✅ КОМПАКТНЫЙ формат (pydantic_to_markdown):
   - Более краткий
   - Показывает source и section явно в строке
   - Удобен для быстрого просмотра
   - ChunkResult(source=..., section=..., line=..., content=...)

✅ ТАБЛИЧНЫЙ формат (pydantic_to_markdown_detailed):
   - Структурированный вид
   - Легко читать множество результатов
   - Source и Section в отдельных колонках таблицы
   - Удобен для анализа больших наборов данных

🎯 Оба формата ЯВНО показывают source и section для последующих вызовов!
""")

