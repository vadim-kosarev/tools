import re
import nltk
from pathlib import Path
from typing import List, Tuple

# Загружаем необходимые данные NLTK (нужно выполнить один раз)
try:
    nltk.data.find('tokenizers/punkt')
except LookupError:
    nltk.download('punkt', quiet=True)

try:
    nltk.data.find('tokenizers/punkt_tab')
except LookupError:
    nltk.download('punkt_tab', quiet=True)


def split_markdown_into_chunks(
        md_content: str,
        min_chunk_size: int = 700,
        max_chunk_size: int = 1500,
        overlap_chars: int = 200,
        language: str = 'russian'
) -> List[Tuple[str, int, int]]:
    """
    Разбивает markdown-контент на чанки с перекрытием.

    Args:
        md_content: Текст markdown-контента
        min_chunk_size: Минимальный размер чанка в символах
        max_chunk_size: Максимальный размер чанка в символах
        overlap_chars: Количество символов перекрытия между чанками
        language: Язык для токенизации ('russian', 'english', etc.)

    Returns:
        Список кортежей: (текст_чанка, начало_в_символах, конец_в_символах)
    """
    text = md_content

    # Убираем лишние пустые строки, но сохраняем общую структуру
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = text.strip()

    # Разбиваем на предложения с помощью NLTK
    sentences = nltk.sent_tokenize(text, language=language)

    chunks = []
    current_chunk = []
    current_length = 0
    start_pos_global = 0  # позиция начала текущего чанка в исходном тексте

    i = 0
    while i < len(sentences):
        sentence = sentences[i]
        sentence_len = len(sentence)

        # Если добавление предложения превысит максимальный размер
        if current_length + sentence_len + 1 > max_chunk_size and current_chunk:
            # Формируем чанк
            chunk_text = " ".join(current_chunk)
            end_pos = start_pos_global + len(chunk_text)

            chunks.append((chunk_text, start_pos_global, end_pos))

            # Определяем перекрытие
            overlap_text = ""
            overlap_len = 0
            j = len(current_chunk) - 1

            # Собираем предложения с конца, пока не наберём нужное перекрытие
            while j >= 0 and overlap_len < overlap_chars:
                overlap_text = current_chunk[j] + " " + overlap_text
                overlap_len += len(current_chunk[j]) + 1
                j -= 1

            # Начинаем новый чанк с перекрытия
            current_chunk = [s.strip() for s in overlap_text.split(" ") if s.strip()]
            current_length = len(overlap_text.strip())
            start_pos_global = end_pos - current_length

        # Добавляем текущее предложение
        current_chunk.append(sentence)
        current_length += sentence_len + 1  # +1 за пробел

        i += 1

    # Не забываем последний чанк
    if current_chunk:
        chunk_text = " ".join(current_chunk)
        end_pos = start_pos_global + len(chunk_text)
        chunks.append((chunk_text, start_pos_global, end_pos))

    # Фильтруем слишком маленькие чанки (кроме последнего)
    filtered_chunks = []
    for chunk_text, start, end in chunks:
        if len(chunk_text) >= min_chunk_size or not filtered_chunks:
            filtered_chunks.append((chunk_text, start, end))
        elif filtered_chunks:
            # Присоединяем маленький кусок к предыдущему чанку
            prev_text, prev_start, _ = filtered_chunks[-1]
            filtered_chunks[-1] = (prev_text + " " + chunk_text, prev_start, end)

    return filtered_chunks


def split_markdown_into_chunks_from_file(
        file_path: str | Path,
        min_chunk_size: int = 700,
        max_chunk_size: int = 1500,
        overlap_chars: int = 200,
        language: str = 'russian'
) -> List[Tuple[str, int, int]]:
    """
    Разбивает markdown-файл на чанки с перекрытием.

    Читает файл и вызывает split_markdown_into_chunks для разбиения контента.

    Args:
        file_path: Путь к markdown-файлу
        min_chunk_size: Минимальный размер чанка в символах
        max_chunk_size: Максимальный размер чанка в символах
        overlap_chars: Количество символов перекрытия между чанками
        language: Язык для токенизации ('russian', 'english', etc.)

    Returns:
        Список кортежей: (текст_чанка, начало_в_символах, конец_в_символах)

    Raises:
        FileNotFoundError: Если файл не найден
    """
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Файл не найден: {path}")

    md_content = path.read_text(encoding='utf-8')

    return split_markdown_into_chunks(
        md_content=md_content,
        min_chunk_size=min_chunk_size,
        max_chunk_size=max_chunk_size,
        overlap_chars=overlap_chars,
        language=language
    )


def print_chunks_preview(chunks: List[Tuple[str, int, int]], max_chars: int = 2000):
    """Печатает краткий просмотр чанков"""
    print(f"Получено {len(chunks)} чанков\n")
    for i, (text, start, end) in enumerate(chunks, 1):
        preview = text[:max_chars].replace('\n', ' ').strip()
        if len(text) > max_chars:
            preview += "..."
        print(f"Чанк {i:3d} | {len(text):5} симв | {start:6}–{end:6} | {preview}")
        print("-" * 90)


# ────────────────────────────────────────────────
# Пример использования
# ────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    # Get file path from command line argument or use default
    if len(sys.argv) > 1:
        FILE_PATH = sys.argv[1]
    else:
        FILE_PATH = "book/chapter_01.md"

    try:
        chunks = split_markdown_into_chunks_from_file(
            file_path=FILE_PATH,
            min_chunk_size=700,
            max_chunk_size=1500,
            overlap_chars=220,
            language='russian'                    # или 'english'
        )

        print_chunks_preview(chunks)

        # Пример сохранения чанков в отдельные файлы (опционально)
        # out_dir = Path("chunks")
        # out_dir.mkdir(exist_ok=True)
        # for i, (text, _, _) in enumerate(chunks, 1):
        #     (out_dir / f"chunk_{i:03d}.txt").write_text(text, encoding="utf-8")

    except Exception as e:
        print(f"Ошибка: {e}")