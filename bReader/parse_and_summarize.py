from bs4 import BeautifulSoup
import ollama
import os
import json
import logging
import re
from datetime import datetime

# Настройка логирования с таймстампом
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


def load_sections_metadata(output_dir: str) -> tuple[list, int]:
    """Load sections metadata from file

    Returns tuple of (sections_list, max_idx)
    where max_idx is the highest index used across all sections
    """
    metadata_file = os.path.join(output_dir, 'sections_metadata.json')
    max_idx = -1
    sections_list = []

    if os.path.exists(metadata_file):
        try:
            with open(metadata_file, 'r', encoding='utf-8') as f:
                sections_list = json.load(f)
                # Find maximum index across all sections
                for section in sections_list:
                    if 'idx' in section:
                        max_idx = max(max_idx, section['idx'])
        except Exception as e:
            logger.warning(f"Could not load sections metadata: {e}")
            sections_list = []
            max_idx = -1

    return sections_list, max_idx


def save_sections_metadata(output_dir: str, sections_list: list) -> None:
    """Save sections metadata to file in output_dir

    Saves as JSON list of section metadata dictionaries
    """
    metadata_file = os.path.join(output_dir, 'sections_metadata.json')
    try:
        with open(metadata_file, 'w', encoding='utf-8') as f:
            json.dump(sections_list, f, ensure_ascii=False, indent=2)
        logger.debug(f"Saved metadata for {len(sections_list)} sections")
    except Exception as e:
        logger.error(f"Could not save sections metadata: {e}")


def is_section_processed(section_idx: int, output_dir: str, full_title: str = '') -> bool:
    """Check if section has already been processed by checking for artifact files

    A section is considered processed if it has:
    - Section text file: XXXX*.txt (with or without title suffix)
    - AND either: summary file or section is small (<5000 chars)

    Returns True if all required artifacts exist, False otherwise
    """
    sections_dir = os.path.join(output_dir, 'sections')

    # Try exact filename with title suffix first (new format)
    if full_title:
        unique_filename = generate_unique_filename(full_title, section_idx)
        section_file = os.path.join(sections_dir, f'{unique_filename}.txt')
        if os.path.exists(section_file):
            summary_file = os.path.join(output_dir, 'summaries', f'{unique_filename}.txt')
            try:
                with open(section_file, 'r', encoding='utf-8') as f:
                    content_length = len(f.read())
                    if content_length < 5000:
                        return True
            except Exception:
                return False
            return os.path.exists(summary_file)

    # Fallback: try old filename format (section_XXXX.txt)
    section_file = os.path.join(sections_dir, f'section_{section_idx:04d}.txt')
    summary_file = os.path.join(output_dir, 'summaries', f'section_{section_idx:04d}.txt')

    if not os.path.exists(section_file):
        # Also try new format without title suffix
        section_file = os.path.join(sections_dir, f'{section_idx:04d}.txt')
        if not os.path.exists(section_file):
            return False
        summary_file = os.path.join(output_dir, 'summaries', f'{section_idx:04d}.txt')

    try:
        with open(section_file, 'r', encoding='utf-8') as f:
            content_length = len(f.read())
            if content_length < 5000:
                return True
    except Exception:
        return False

    return os.path.exists(summary_file)



def get_section_title(section_elem):
    """Извлекает заголовок секции если он есть"""
    title_elem = section_elem.find('title')
    if title_elem:
        title_text = title_elem.get_text(separator=' ', strip=True)
        return title_text
    return None


def get_section_numeric_id(section_elem) -> int | None:
    """Extract numeric section ID from title element

    FB2 structure: <section><title><p>1</p></title>...</section>
    Returns the numeric value from <title><p>NUMBER</p></title>
    Returns None if not found or not numeric
    """
    title_elem = section_elem.find('title')
    if title_elem:
        p_elem = title_elem.find('p')
        if p_elem:
            text = p_elem.get_text(strip=True)
            try:
                return int(text)
            except ValueError:
                # If not purely numeric, return None
                return None
    return None


def extract_section_content(section_elem):
    """Извлекает текст секции исключая вложенные секции и заголовок"""
    content = []
    for child in section_elem.children:
        if isinstance(child, str):
            text = child.strip()
            if text:
                content.append(text)
        elif hasattr(child, 'name'):
            if child.name == 'p':
                text = child.get_text(strip=True)
                if text:
                    content.append(text)
            elif child.name == 'section':
                # Пропускаем вложенные секции
                continue
            elif child.name != 'title':
                text = child.get_text(strip=True)
                if text:
                    content.append(text)
    return '\n'.join(content)


def extract_numeric_suffix(title: str) -> int | None:
    """Extract numeric suffix from title

    Example: "ЧАСТЬ ПЕРВАЯ - 1" -> 1
    Returns None if no number found
    """
    if not title:
        return None

    # Match pattern: "... - NUMBER" at the end of string
    match = re.search(r'-\s*(\d+)\s*$', title)
    if match:
        return int(match.group(1))

    return None


def sanitize_filename(text: str, max_length: int = 50) -> str:
    """Convert text to valid filename component

    - Replace spaces with underscores
    - Remove special characters
    - Convert to lowercase
    - Limit length
    - Example: "ЧАСТЬ ПЕРВАЯ - 1" -> "часть_первая_1"
    """
    if not text:
        return ""

    # Convert to lowercase
    text = text.lower()

    # Replace spaces and dashes with underscores
    text = re.sub(r'[\s\-]+', '_', text)

    # Remove special characters, keep only alphanumeric, underscores, and Cyrillic
    text = re.sub(r'[^\w\u0400-\u04FF]', '', text)

    # Remove consecutive underscores
    text = re.sub(r'_+', '_', text)

    # Remove leading/trailing underscores
    text = text.strip('_')

    # Limit length
    if len(text) > max_length:
        text = text[:max_length].rstrip('_')

    return text


def generate_unique_filename(full_title: str, section_idx: int) -> str:
    """Generate unique filename from full section title and index

    Example: "часть вторая - 1" -> "0001_часть_вторая_1"
    """
    sanitized = sanitize_filename(full_title)

    if sanitized:
        return f"{section_idx:04d}_{sanitized}"
    else:
        return f"{section_idx:04d}"


def process_sections_recursive(section_elem, output_dir, sections_list, section_counter, parent_title='', last_section_title=''):
    """Recursively process section hierarchy with artifact-based deduplication

    Uses numeric ID from FB2 title element instead of processing order counter.
    Correctly handles nested sections by combining parent and child titles.

    Args:
        section_counter: List with next fallback index if no numeric ID found
        last_section_title: Track previous section title to auto-increment if no title found
        parent_title: Title of parent section for building hierarchy
    """
    title = get_section_title(section_elem)

    # Try to extract numeric ID from title element
    numeric_id = get_section_numeric_id(section_elem)

    # If no title, generate from previous one
    if not title and last_section_title:
        num = extract_numeric_suffix(last_section_title)
        if num is not None:
            # Extract the prefix part (everything before the number)
            prefix_match = re.match(r'^(.+?)\s*-\s*\d+\s*$', last_section_title)
            if prefix_match:
                prefix = prefix_match.group(1)
                title = f"{prefix} - {num + 1}"
            else:
                title = last_section_title

    # Build full title only if we have a title for THIS section
    if title:
        full_title = f"{parent_title} - {title}" if parent_title else title
    else:
        full_title = parent_title

    # Extract content of current section (without nested sections)
    content = extract_section_content(section_elem)

    # Find nested sections
    nested_sections = [child for child in section_elem.children if hasattr(child, 'name') and child.name == 'section']

    # If there are nested sections, process them recursively
    if nested_sections:
        current_last_title = title if title else last_section_title
        for nested_section in nested_sections:
            # Pass current section's title as parent_title for nested sections
            process_sections_recursive(
                nested_section,
                output_dir,
                sections_list,
                section_counter,
                full_title,  # Pass full_title as parent for proper hierarchy
                current_last_title
            )
            # Update last_section_title with the processed nested section
            nested_title = get_section_title(nested_section)
            if nested_title:
                current_last_title = nested_title
    else:
        # If no nested sections, process current section as a leaf node
        if content.strip():
            # Always use counter for global continuous numbering
            # Numeric IDs in source file are only used for title construction, not for actual indexing
            idx = section_counter[0]
            section_counter[0] += 1

            logger.debug(f"Assigning global idx {idx} to section: '{full_title}'")

            # Check if section already processed by artifact files
            if is_section_processed(idx, output_dir, full_title):
                logger.info(f"Skipping already processed section {idx}: '{full_title}'")
                unique_filename = generate_unique_filename(full_title, idx)
                section_file = os.path.join(output_dir, 'sections', f'{unique_filename}.txt')
                summary_file_path = os.path.join(output_dir, 'summaries', f'{unique_filename}.txt')
                summary_file = summary_file_path if os.path.exists(summary_file_path) else None
                section_metadata = {
                    'idx': idx,
                    'title': full_title,
                    'section_file': section_file,
                    'summary_file': summary_file
                }
                sections_list.append(section_metadata)
                return

            section_start_time = datetime.now()

            logger.info(f"Processing section {idx}: '{full_title}', length: {len(content)} chars")

            # Generate unique filename from title
            unique_filename = generate_unique_filename(full_title, idx)

            # Save raw section text to disk
            section_file = os.path.join(output_dir, 'sections', f'{unique_filename}.txt')
            with open(section_file, 'w', encoding='utf-8') as f:
                if full_title:
                    f.write(f"=== {full_title} ===\n\n")
                f.write(content)
            logger.info(f"Saved section {idx} to disk as {unique_filename}.txt")

            # Generate summary if section is large (>5000 chars)
            summary_file = None
            if len(content) > 5000:
                summary_start_time = datetime.now()
                logger.info(f"Generating summary for section {idx}")
                prompt = (f"Создай краткое содержание этого раздела книги "
                          f"(максимум 500 слов). Фокус на сюжете, героях,"
                          f" ключевых событиях и локациях:\n\n{content[:100000]}") # Ограничение на 100к символов для модели
                response = ollama.chat(model='qwen2.5:14b-instruct', messages=[{'role': 'user', 'content': prompt}])
                summary = response['message']['content']

                # Save summary
                summary_file = os.path.join(output_dir, 'summaries', f'{unique_filename}.txt')
                with open(summary_file, 'w', encoding='utf-8') as f:
                    if full_title:
                        f.write(f"=== {full_title} ===\n\n")
                    f.write(summary)

                summary_duration = (datetime.now() - summary_start_time).total_seconds()
                logger.info(f"Saved summary for section {idx} (duration: {summary_duration:.2f}s)")

            # Add to metadata
            section_metadata = {
                'idx': idx,
                'title': full_title,
                'section_file': section_file,
                'summary_file': summary_file,
                'processed_at': datetime.now().isoformat()
            }
            sections_list.append(section_metadata)

            # Save metadata after each new section for persistence
            # Extract output_dir from section_file path
            save_sections_metadata(os.path.dirname(os.path.dirname(section_file)), sections_list)

            section_duration = (datetime.now() - section_start_time).total_seconds()
            logger.info(f"Section {idx} processing completed (total duration: {section_duration:.2f}s)")


def parse_and_summarize_fb2(file_path, output_dir='processed_book'):
    """Parse FB2 file and generate summaries with artifact-based deduplication

    Uses global continuous numbering across entire book:
    Part 1: sections 1-9
    Part 2: sections 10-15 (continues from 9, not starts from 1)
    """
    parse_start_time = datetime.now()
    logger.info(f"Starting parsing FB2 file: {file_path}")

    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(os.path.join(output_dir, 'sections'), exist_ok=True)
    os.makedirs(os.path.join(output_dir, 'summaries'), exist_ok=True)

    # Load existing metadata from file and get max index
    sections, max_metadata_idx = load_sections_metadata(output_dir)
    logger.info(f"Loaded {len(sections)} sections from metadata (max idx: {max_metadata_idx})")

    # Find maximum section index from actual files (for safety)
    sections_dir = os.path.join(output_dir, 'sections')
    existing_sections = [f for f in os.listdir(sections_dir) if f.endswith('.txt')] if os.path.exists(sections_dir) else []

    max_file_idx = -1
    last_section_title_from_disk = None

    if existing_sections:
        # Extract indices from filenames and find the maximum
        for filename in existing_sections:
            try:
                # Try new format first: 0001_title.txt or 0001.txt
                match = re.match(r'^(\d+)', filename)
                if match:
                    idx = int(match.group(1))
                    max_file_idx = max(max_file_idx, idx)
                # Fallback to old format: section_0001...
                elif filename.startswith('section_'):
                    match = re.match(r'section_(\d+)', filename)
                    if match:
                        idx = int(match.group(1))
                        max_file_idx = max(max_file_idx, idx)
            except ValueError:
                pass

        # Load the title of the last processed section from the file
        if max_file_idx >= 0:
            last_section_file = None
            for filename in existing_sections:
                if filename.startswith(f'{max_file_idx:04d}') or filename.startswith(f'section_{max_file_idx:04d}'):
                    last_section_file = os.path.join(sections_dir, filename)
                    break

            if last_section_file:
                try:
                    with open(last_section_file, 'r', encoding='utf-8') as f:
                        first_line = f.readline().strip()
                        if first_line.startswith('===') and first_line.endswith('==='):
                            last_section_title_from_disk = first_line.replace('===', '').strip()
                except Exception as e:
                    logger.warning(f"Could not read last section title from disk: {e}")

        logger.info(f"Found {len(existing_sections)} section files (max file idx: {max_file_idx})")

    # Use maximum from both metadata and files
    max_section_idx = max(max_metadata_idx, max_file_idx)
    logger.info(f"Using global max index: {max_section_idx}")

    # Parse FB2 as XML
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            soup = BeautifulSoup(f, 'xml')
    except Exception as e:
        logger.error(f"Error parsing FB2 file: {e}")
        raise

    # section_counter starts from max global index + 1 for continuous numbering
    section_counter = [max_section_idx + 1]

    # Find body and process its sections
    body = soup.find('body')
    if body:
        # Skip title in body and process sections
        for child in body.children:
            if hasattr(child, 'name') and child.name == 'section':
                process_sections_recursive(child, output_dir, sections, section_counter, '', last_section_title_from_disk)
    else:
        logger.warning("No body element found in FB2 file")

    # Save metadata for all sections (new + existing)
    save_sections_metadata(output_dir, sections)

    processed_count = len(sections) - len(load_sections_metadata(output_dir)[0])
    total_sections = len(sections)
    total_duration = (datetime.now() - parse_start_time).total_seconds()
    logger.info(f"Completed processing {processed_count} new sections (total: {total_sections}). Duration: {total_duration:.2f}s")

    return output_dir  # Return directory for next step

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        parse_and_summarize_fb2(sys.argv[1])
    else:
        print("Usage: python parse_and_summarize.py <fb2_file_path>")
