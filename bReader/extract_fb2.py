from bs4 import BeautifulSoup
import os
import logging
import re
import argparse
from datetime import datetime
from typing import Optional, List
from pathlib import Path
from metadata_manager import ParseResultCollector

# Настройка логирования с таймстампом
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


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


def split_long_section_by_paragraphs(content: str, title: str, max_size: int = 100000) -> List[tuple[str, str]]:
    """Split long section content into approximately equal parts by paragraphs

    Args:
        content: Section content text
        title: Section title for generating part titles
        max_size: Maximum size in characters before splitting

    Returns:
        List of tuples (part_title, part_content) for each part
    """
    if len(content) <= max_size:
        return [(title, content)]

    # Split by paragraphs (double newlines)
    paragraphs = content.split('\n\n')

    # Calculate target size per part
    total_chars = len(content)
    num_parts = (total_chars + max_size - 1) // max_size  # Ceiling division
    target_size = total_chars // num_parts

    parts = []
    current_part = []
    current_size = 0
    part_num = 1

    for paragraph in paragraphs:
        paragraph_size = len(paragraph) + 2  # +2 for double newline

        # If adding this paragraph would exceed target and we have content
        if current_size + paragraph_size > target_size and current_part:
            # Create a part
            part_content = '\n\n'.join(current_part)
            part_title = f"{title} - часть {part_num}"
            parts.append((part_title, part_content))

            # Start new part
            current_part = [paragraph]
            current_size = len(paragraph)
            part_num += 1
        else:
            # Add to current part
            current_part.append(paragraph)
            current_size += paragraph_size

    # Don't forget the last part
    if current_part:
        part_content = '\n\n'.join(current_part)
        part_title = f"{title} - часть {part_num}"
        parts.append((part_title, part_content))

    logger.info(f"Split long section '{title}' ({len(content):,} chars) into {len(parts)} parts")
    for i, (part_title, part_content) in enumerate(parts, 1):
        logger.debug(f"  Part {i}: '{part_title}' ({len(part_content):,} chars)")

    return parts


def check_section_exists_by_title(metadata: ParseResultCollector, title: str) -> bool:
    """Check if section with same title already exists in metadata

    Args:
        metadata: ParseResultCollector instance
        title: Section title to check for

    Returns:
        True if section with same title exists, False otherwise
    """
    if not title:
        return False

    all_sections = metadata.get_all_sections()
    for section_meta in all_sections.values():
        if isinstance(section_meta, dict):
            existing_title = section_meta.get('title', '')
            if existing_title == title:
                logger.info(f"Section with title '{title}' already exists, skipping")
                return True
    return False


def extract_sections_content(soup, metadata: ParseResultCollector, section_counter, parent_title=''):
    """Extract sections content from FB2 and save to files

    Only extracts content and saves files, no summary generation.
    Works recursively through section hierarchy.

    Args:
        soup: BeautifulSoup parsed FB2 content
        metadata: ParseResultCollector instance
        section_counter: List with current section index [current_idx]
        parent_title: Parent section title for hierarchy building
    """
    # Find body and process its sections
    body = soup.find('body')
    if body:
        # Skip title in body and process sections
        for child in body.children:
            if hasattr(child, 'name') and child.name == 'section':
                extract_section_recursive(child, metadata, section_counter, parent_title)
    else:
        logger.warning("No body element found in FB2 content")


def extract_section_recursive(section_elem, metadata: ParseResultCollector, section_counter, parent_title=''):
    """Recursively extract section content and save to files

    Args:
        section_elem: BeautifulSoup section element
        metadata: ParseResultCollector instance
        section_counter: List with next index [current_idx]
        parent_title: Title of parent section for building hierarchy
    """
    title = get_section_title(section_elem)

    # Build full title
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
        for nested_section in nested_sections:
            extract_section_recursive(
                nested_section,
                metadata,
                section_counter,
                full_title  # Pass full_title as parent for proper hierarchy
            )
    else:
        # If no nested sections and has content, save as section
        if content.strip():
            logger.info(f"Extracting section content: '{full_title}', length: {len(content)} chars")

            # Check if content is too long and needs splitting
            max_section_size = 100000
            if len(content) > max_section_size:
                logger.info(f"Section '{full_title}' is too long ({len(content):,} chars), splitting into parts")
                parts = split_long_section_by_paragraphs(content, full_title, max_section_size)

                for part_title, part_content in parts:
                    # Check if section with same title already exists
                    if check_section_exists_by_title(metadata, part_title):
                        logger.info(f"Section with title '{part_title}' already exists, skipping extraction")
                        continue

                    idx = section_counter[0]
                    section_counter[0] += 1

                    # Calculate file path and check if file already exists
                    unique_filename = generate_unique_filename(part_title, idx)
                    section_file_path = os.path.join(metadata.output_dir, 'sections', f'{unique_filename}.txt')

                    if os.path.exists(section_file_path):
                        logger.info(f"Section file already exists: {section_file_path}, skipping extraction")
                        continue

                    logger.info(f"Extracting section {idx}: '{part_title}', length: {len(part_content)} chars")

                    # Check if section already exists in metadata
                    existing_section = metadata.get(f'sections.{idx}')
                    if existing_section:
                        logger.info(f"Section {idx} already exists in metadata, skipping extraction")
                        continue

                    # Use add_section method to save content without summary
                    section_metadata = metadata.add_section(
                        idx=idx,
                        section_title=part_title,
                        section_content=part_content,
                        section_summary_content=None  # No summary in step 1
                    )

                    logger.info(f"Section {idx} part extracted and saved")
            else:
                # Check if section with same title already exists
                if check_section_exists_by_title(metadata, full_title):
                    logger.info(f"Section with title '{full_title}' already exists, skipping extraction")
                    return

                # Normal-sized section, process as usual
                idx = section_counter[0]
                section_counter[0] += 1

                # Calculate file path and check if file already exists
                unique_filename = generate_unique_filename(full_title, idx)
                section_file_path = os.path.join(metadata.output_dir, 'sections', f'{unique_filename}.txt')

                if os.path.exists(section_file_path):
                    logger.info(f"Section file already exists: {section_file_path}, skipping extraction")
                    return

                logger.info(f"Extracting section {idx}: '{full_title}', length: {len(content)} chars")

                # Check if section already exists in metadata
                existing_section = metadata.get(f'sections.{idx}')
                if existing_section:
                    logger.info(f"Section {idx} already exists in metadata, skipping extraction")
                    return

                # Use add_section method to save content without summary
                section_metadata = metadata.add_section(
                    idx=idx,
                    section_title=full_title,
                    section_content=content,
                    section_summary_content=None  # No summary - extraction only
                )

                logger.info(f"Section {idx} content extracted and saved")


def parse_fb2_content(
        fb2_content: str,
        output_dir: str = 'processed_book',
        metadata_file: Optional[str] = None,
        source_file_path: Optional[str] = None
) -> str:
    """Parse FB2 content and extract sections with metadata-based processing

    Uses global continuous numbering across entire book.
    Only works with metadata object - no file existence checks.
    Only extracts sections - no summary generation.

    Args:
        fb2_content: FB2 XML content as string
        output_dir: Directory to save processed sections
        metadata_file: Path to metadata file (if None, uses output_dir/sections_metadata.json)
        source_file_path: Path to source FB2 file (optional, for metadata tracking)

    Returns:
        Path to output directory
    """
    if metadata_file is None:
        metadata_file = os.path.join(output_dir, 'sections_metadata.json')

    parse_start_time = datetime.now()
    logger.info(f"Starting FB2 content extraction")

    os.makedirs(output_dir, exist_ok=True)

    # Initialize metadata manager
    metadata = ParseResultCollector(metadata_file, output_dir)

    # Set source file if provided
    if source_file_path:
        metadata.set_source_file(source_file_path)

    # Get existing sections count from metadata only
    existing_sections = metadata.get_all_sections()
    max_metadata_idx = -1
    for idx_str in existing_sections.keys():
        try:
            idx = int(idx_str)
            max_metadata_idx = max(max_metadata_idx, idx)
        except (ValueError, TypeError):
            pass

    logger.info(f"Loaded {len(existing_sections)} sections from metadata (max idx: {max_metadata_idx})")

    # Parse FB2 as XML
    try:
        soup = BeautifulSoup(fb2_content, 'xml')
    except Exception as e:
        logger.error(f"Error parsing FB2 content: {e}")
        raise

    # section_counter starts from max metadata index + 1 for continuous numbering
    section_counter = [max_metadata_idx + 1]

    # Extract sections content and save to files
    extract_sections_content(soup, metadata, section_counter)

    # Get final section count for reporting
    final_sections = metadata.get_all_sections()
    processed_count = len(final_sections) - len(existing_sections)
    total_sections = len(final_sections)
    total_duration = (datetime.now() - parse_start_time).total_seconds()
    logger.info(f"Completed extracting {processed_count} new sections (total: {total_sections}). Duration: {total_duration:.2f}s")

    return output_dir


def extract_fb2_from_file(
        file_path: str | Path,
        output_dir: str = 'processed_book',
        metadata_file: Optional[str] = None
) -> str:
    """Extract sections from FB2 file

    Reads FB2 file and extracts sections to files with metadata tracking.

    Args:
        file_path: Path to FB2 file
        output_dir: Directory to save processed sections
        metadata_file: Path to metadata file (if None, uses output_dir/sections_metadata.json)

    Returns:
        Path to output directory

    Raises:
        FileNotFoundError: If FB2 file not found
    """
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"FB2 file not found: {path}")

    logger.info(f"Reading FB2 file: {path}")
    fb2_content = path.read_text(encoding='utf-8')

    # Extract sections content
    output_dir = parse_fb2_content(
        fb2_content=fb2_content,
        output_dir=output_dir,
        metadata_file=metadata_file,
        source_file_path=str(path)
    )

    logger.info(f"FB2 extraction completed. Results saved to: {output_dir}")

    return output_dir


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='Extract sections from FB2 files',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  python extract_fb2.py book.fb2
  python extract_fb2.py book.fb2 --output processed --metafile meta.json
  python extract_fb2.py book.fb2 --output results
        '''
    )
    parser.add_argument('fb2_file', help='Path to FB2 file to process')
    parser.add_argument(
        '--output',
        default='processed_book',
        help='Output directory for processed sections (default: processed_book)'
    )
    parser.add_argument(
        '--metafile',
        default=None,
        help='Path to metadata file (default: output_dir/sections_metadata.json)'
    )

    args = parser.parse_args()

    try:
        output_dir = extract_fb2_from_file(
            file_path=args.fb2_file,
            output_dir=args.output,
            metadata_file=args.metafile
        )
        logger.info(f"Extraction completed. Results saved to: {output_dir}")
    except FileNotFoundError as e:
        logger.error(f"Error: {e}")
        exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        exit(1)
