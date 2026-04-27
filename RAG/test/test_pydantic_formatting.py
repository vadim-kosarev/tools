"""
Тестовый скрипт для проверки форматирования результатов поиска.
"""
from pydantic import BaseModel, Field
from pydantic_utils import pydantic_to_markdown


class SectionInfo(BaseModel):
    """Информация о разделе (упрощённая)"""
    source: str = Field(description="Имя файла")
    section: str = Field(description="Название раздела")
    line_start: int = Field(description="Начальная строка")
    chunks_count: int = Field(description="Количество чанков")


class SearchSectionsResult(BaseModel):
    """Результат поиска разделов"""
    query: str = Field(description="Исходный запрос")
    sections: list[SectionInfo] = Field(description="Найденные разделы")
    total_found: int = Field(description="Всего найдено разделов")
    returned_count: int = Field(description="Возвращено разделов")


# Создаем тестовые данные
test_result = SearchSectionsResult(
    query="АРМ эксплуатационного персонала СОИБ КЦОИ",
    sections=[
        SectionInfo(
            source="technical_spec.md",
            section="АРМ эксплуатационного персонала СОИБ КЦОИ",
            line_start=150,
            chunks_count=25
        ),
        SectionInfo(
            source="security_requirements.md",
            section="СОИБ КЦОИ - основные компоненты",
            line_start=230,
            chunks_count=18
        ),
        SectionInfo(
            source="installation_guide.md",
            section="Установка АРМ персонала",
            line_start=45,
            chunks_count=12
        ),
        SectionInfo(
            source="user_manual.md",
            section="Эксплуатация АРМ СОИБ",
            line_start=89,
            chunks_count=30
        ),
        SectionInfo(
            source="architecture.md",
            section="Архитектура КЦОИ",
            line_start=120,
            chunks_count=22
        ),
        SectionInfo(
            source="configuration.md",
            section="Конфигурация АРМ",
            line_start=75,
            chunks_count=15
        ),
    ],
    total_found=210,
    returned_count=6
)

# Форматируем и выводим
print("Тип первого элемента sections:", type(test_result.sections[0]))
print("Является ли SectionInfo:", isinstance(test_result.sections[0], SectionInfo))
print()

formatted_result = pydantic_to_markdown(test_result)
print(formatted_result)
print("\n" + "="*80 + "\n")

# Также проверим краткое резюме
from pydantic_utils import format_result_summary
summary = format_result_summary(test_result)
print(f"Краткое резюме: {summary}")

