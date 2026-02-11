import re
import nltk
import os
from pathlib import Path
from typing import List, Tuple, Optional
from datetime import datetime
from metadata_manager import ParseResultCollector

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
        language: str = 'russian',
        metadata: Optional[ParseResultCollector] = None,
        chunks_dir: Optional[str | Path] = None,
        section_idx: Optional[int] = None,
        section_file: Optional[str] = None
) -> tuple[List[Tuple[str, int, int]], dict]:
    """
    Разбивает markdown-контент на чанки с перекрытием.

    Args:
        md_content: Текст markdown-контента
        min_chunk_size: Минимальный размер чанка в символах
        max_chunk_size: Максимальный размер чанка в символах
        overlap_chars: Количество символов перекрытия между чанками
        language: Язык для токенизации ('russian', 'english', etc.)
        metadata: ParseResultCollector manager instance (optional)
        chunks_dir: Directory to save chunk files (optional)
        section_idx: Index of section (used as key for storing chunks)
        section_file: Path to source section file

    Returns:
        Tuple of (chunks_list, metadata_dict) where:
        - chunks_list: List of tuples (text, start, end)
        - metadata_dict: Dictionary of chunk metadata with idx as string keys
    """
    # Load existing metadata if metadata manager is provided
    existing_chunks = {}
    max_idx = -1

    if metadata and section_idx is not None:
        # Get existing chunks for this section
        existing_chunks = metadata.get_section_chunks(section_idx)
        # Find maximum index in existing chunks
        for idx_str in existing_chunks.keys():
            try:
                idx = int(idx_str)
                max_idx = max(max_idx, idx)
            except (ValueError, TypeError):
                pass
    elif metadata and section_file:
        # Try to find section_idx by section_file path
        all_sections = metadata.get_all_sections()
        for idx_str, section_meta in all_sections.items():
            if isinstance(section_meta, dict):
                stored_path = section_meta.get('section_file', '')
                if os.path.normpath(stored_path) == os.path.normpath(section_file):
                    try:
                        section_idx = int(idx_str)
                        existing_chunks = metadata.get_section_chunks(section_idx)
                        # Find maximum index in existing chunks
                        for chunk_idx_str in existing_chunks.keys():
                            try:
                                idx = int(chunk_idx_str)
                                max_idx = max(max_idx, idx)
                            except (ValueError, TypeError):
                                pass
                        break
                    except (ValueError, TypeError):
                        pass

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

    # Build metadata for new chunks
    chunks_metadata = existing_chunks.copy() if existing_chunks else {}
    next_idx = max_idx + 1

    # Create output directory if needed
    if chunks_dir:
        chunks_dir_path = Path(chunks_dir)
        chunks_dir_path.mkdir(parents=True, exist_ok=True)

    # Only add metadata for chunks that are not already in metadata
    # This assumes filtered_chunks are new chunks (not previously processed)
    for i, (chunk_text, start, end) in enumerate(filtered_chunks, 1):
        chunk_meta = {
            'idx': next_idx,
            'text_length': len(chunk_text),
            'start_pos': start,
            'end_pos': end,
            'processed_at': datetime.now().isoformat()
        }

        # Save chunk file and add path to metadata if chunks_dir is provided
        if chunks_dir:
            chunk_filename = f"chunk_{next_idx:05d}.txt"
            chunk_filepath = Path(chunks_dir) / chunk_filename
            chunk_filepath.write_text(chunk_text, encoding='utf-8')
            chunk_meta['chunk_file'] = str(chunk_filepath)

        # Add section file reference if provided
        if section_file:
            chunk_meta['section_file'] = section_file

        chunks_metadata[str(next_idx)] = chunk_meta
        next_idx += 1

    # Save metadata if metadata manager is provided
    if metadata and section_idx is not None and section_file:
        metadata.set_section_chunks(section_idx, section_file, chunks_metadata)

    return filtered_chunks, chunks_metadata


def split_markdown_into_chunks_from_file(
        file_path: str | Path,
        min_chunk_size: int = 700,
        max_chunk_size: int = 1500,
        overlap_chars: int = 200,
        language: str = 'russian',
        metadata: Optional[ParseResultCollector] = None,
        chunks_dir: Optional[str | Path] = None,
        section_idx: Optional[int] = None,
        section_file: Optional[str] = None
) -> tuple[List[Tuple[str, int, int]], dict]:
    """
    Разбивает markdown-файл на чанки с перекрытием.

    Читает файл и вызывает split_markdown_into_chunks для разбиения контента.

    Args:
        file_path: Путь к markdown-файлу
        min_chunk_size: Минимальный размер чанка в символах
        max_chunk_size: Максимальный размер чанка в символах
        overlap_chars: Количество символов перекрытия между чанками
        language: Язык для токенизации ('russian', 'english', etc.)
        metadata: ParseResultCollector manager instance (optional)
        chunks_dir: Directory to save chunk files (optional)
        section_idx: Index of section (used as key for storing chunks)
        section_file: Path to source section file

    Returns:
        Tuple of (chunks_list, metadata_dict)

    Raises:
        FileNotFoundError: Если файл не найден
    """
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Файл не найден: {path}")

    md_content = path.read_text(encoding='utf-8')

    # If section_file is not provided, use the input file_path as the section file reference
    if section_file is None:
        section_file = str(path)

    return split_markdown_into_chunks(
        md_content=md_content,
        min_chunk_size=min_chunk_size,
        max_chunk_size=max_chunk_size,
        overlap_chars=overlap_chars,
        language=language,
        metadata=metadata,
        chunks_dir=chunks_dir,
        section_idx=section_idx,
        section_file=section_file
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
    import argparse

    parser = argparse.ArgumentParser(
        description='Split markdown file into chunks with overlap',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  python md_to_chunks.py section_file.txt --metafile meta.json --section-file "processed_book\\sections\\0000_section.txt"
  python md_to_chunks.py book.md --min-chunk 500 --max-chunk 2000 --overlap 300
  python md_to_chunks.py book.md --min-chunk 500 --max-chunk 2000 --language english
  python md_to_chunks.py book.md --metafile chunks_meta.json --section-file "path/to/section.txt"
  
Note: If --section-file is provided and --metafile exists, section_idx will be auto-detected from metadata.
If --section-idx is explicitly provided, it takes precedence.
        '''
    )
    parser.add_argument('file_path', help='Path to markdown file to process')
    parser.add_argument(
        '--min-chunk',
        type=int,
        default=700,
        help='Minimum chunk size in characters (default: 700)'
    )
    parser.add_argument(
        '--max-chunk',
        type=int,
        default=1500,
        help='Maximum chunk size in characters (default: 1500)'
    )
    parser.add_argument(
        '--overlap',
        type=int,
        default=220,
        help='Overlap size in characters between chunks (default: 220)'
    )
    parser.add_argument(
        '--language',
        default='russian',
        help='Language for tokenization (default: russian)'
    )
    parser.add_argument(
        '--metafile',
        default=None,
        help='Path to metadata file to save chunk metadata (optional)'
    )
    parser.add_argument(
        '--section-file',
        default=None,
        help='Path to source section file for metadata tracking (optional)'
    )
    parser.add_argument(
        '--section-idx',
        type=int,
        default=None,
        help='Index of section for organizing chunks in metadata (optional)'
    )

    args = parser.parse_args()

    try:
        # Define output directory for chunks
        out_dir = Path("processed_book/chunks")

        # Create metadata manager if metafile is provided
        metadata_manager = None
        if args.metafile:
            # Use parent directory of chunks as output_dir
            base_output_dir = out_dir.parent
            metadata_manager = ParseResultCollector(args.metafile, str(base_output_dir))

        chunks, metadata = split_markdown_into_chunks_from_file(
            file_path=args.file_path,
            min_chunk_size=args.min_chunk,
            max_chunk_size=args.max_chunk,
            overlap_chars=args.overlap,
            language=args.language,
            metadata=metadata_manager,
            chunks_dir=str(out_dir),
            section_idx=args.section_idx,
            section_file=args.section_file
        )

        print_chunks_preview(chunks, 500)


        print(f"\nChunks saved to: {out_dir}")
        if args.metafile:
            print(f"Metadata saved to: {args.metafile}")

    except FileNotFoundError as e:
        print(f"Error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
