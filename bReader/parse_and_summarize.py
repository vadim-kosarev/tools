from bs4 import BeautifulSoup
import ollama
import os
import logging
import re
import argparse
from datetime import datetime
from typing import Optional
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




def extract_sections_content(soup, metadata: ParseResultCollector, section_counter, parent_title=''):
    """Step 1: Extract sections content from FB2 and save to files

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
            idx = section_counter[0]
            section_counter[0] += 1

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
                section_summary_content=None  # No summary in step 1
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

    Args:
        fb2_content: FB2 XML content as string
        output_dir: Directory to save processed sections and summaries
        metadata_file: Path to metadata file (if None, uses output_dir/sections_metadata.json)
        source_file_path: Path to source FB2 file (optional, for metadata tracking)

    Returns:
        Path to output directory
    """
    if metadata_file is None:
        metadata_file = os.path.join(output_dir, 'sections_metadata.json')

    parse_start_time = datetime.now()
    logger.info(f"Starting parsing FB2 content")

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

    # Step 1: Extract sections content and save to files
    extract_sections_content(soup, metadata, section_counter)

    # Get final section count for reporting
    final_sections = metadata.get_all_sections()
    processed_count = len(final_sections) - len(existing_sections)
    total_sections = len(final_sections)
    total_duration = (datetime.now() - parse_start_time).total_seconds()
    logger.info(f"Completed extracting {processed_count} new sections (total: {total_sections}). Duration: {total_duration:.2f}s")

    return output_dir


def parse_and_summarize_fb2_from_file(
        file_path: str | Path,
        output_dir: str = 'processed_book',
        metadata_file: Optional[str] = None,
        model: str = 'qwen2.5:14b-instruct'
) -> str:
    """Parse FB2 file, extract sections, and generate summaries

    Reads FB2 file and processes it in two steps:
    1. Extract sections content and save to files
    2. Generate summaries for sections that need them

    Args:
        file_path: Path to FB2 file
        output_dir: Directory to save processed sections and summaries
        metadata_file: Path to metadata file (if None, uses output_dir/sections_metadata.json)
        model: Ollama model to use for summary generation

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

    # Step 1: Extract sections content
    output_dir = parse_fb2_content(
        fb2_content=fb2_content,
        output_dir=output_dir,
        metadata_file=metadata_file,
        source_file_path=str(path)
    )

    # Step 2: Generate summaries
    if metadata_file is None:
        metadata_file = os.path.join(output_dir, 'sections_metadata.json')

    metadata = ParseResultCollector(metadata_file, output_dir)
    summaries_generated = generate_summaries_for_sections(metadata, summary_model=model)

    logger.info(f"Total processing completed. Generated {summaries_generated} summaries. Results saved to: {output_dir}")

    return output_dir


def generate_summaries_for_sections(
        metadata: ParseResultCollector,
        summary_model: str = 'qwen2.5:14b-instruct',
        min_section_size: int = 5000,
        prompt_file: str = 'prompts/summarize.txt'
) -> int:
    """Step 2: Generate summaries for sections that don't have them yet

    Reads section files from disk and generates summaries for large sections.

    Args:
        metadata: ParseResultCollector instance
        summary_model: Model to use for summary generation
        min_section_size: Minimum section size to generate summary
        prompt_file: Path to prompt template file

    Returns:
        Number of summaries generated
    """
    summaries_generated = 0
    all_sections = metadata.get_all_sections()

    # Load prompt template from file
    try:
        script_dir = Path(__file__).parent
        prompt_path = script_dir / prompt_file
        prompt_template = prompt_path.read_text(encoding='utf-8')
        logger.debug(f"Loaded prompt template from {prompt_path}")
    except Exception as e:
        logger.error(f"Could not load prompt from {prompt_file}: {e}")
        # Fallback to hardcoded prompt
        prompt_template = ("Создай краткое содержание этого раздела книги "
                          "(максимум 500 слов). Фокус на сюжете, героях, "
                          "ключевых событиях и локациях:\n\n{content}")
        logger.warning("Using fallback hardcoded prompt")

    logger.info(f"Starting summary generation for {len(all_sections)} sections")

    # Sort sections by index to process in order
    sorted_sections = sorted(all_sections.items(), key=lambda x: int(x[0]))

    for idx_str, section_meta in sorted_sections:
        if not isinstance(section_meta, dict):
            continue

        section_idx = section_meta.get('idx')
        section_title = section_meta.get('title', '')
        section_file_path = section_meta.get('section_file', '')
        existing_summary_file = section_meta.get('summary_file')

        # Skip if summary already exists
        if existing_summary_file and os.path.exists(existing_summary_file):
            logger.debug(f"Section {section_idx} already has summary, skipping")
            continue

        # Skip if section file doesn't exist
        if not section_file_path or not os.path.exists(section_file_path):
            logger.warning(f"Section {section_idx} file not found: {section_file_path}")
            continue

        try:
            # Read section content from file
            with open(section_file_path, 'r', encoding='utf-8') as f:
                file_content = f.read()

            # Extract content (skip title line if present)
            lines = file_content.split('\n')
            if lines and lines[0].startswith('===') and lines[0].endswith('==='):
                content = '\n'.join(lines[2:])  # Skip title and empty line
            else:
                content = file_content

            # Check if section is large enough for summary
            if len(content) < min_section_size:
                logger.debug(f"Section {section_idx} too small ({len(content)} chars), skipping summary")
                continue

            # Generate summary
            summary_start_time = datetime.now()
            logger.info(f"Generating summary for section {section_idx}: '{section_title}'")

            # Format prompt with content (limit to 100k chars)
            prompt = prompt_template.format(content=content[:100000])

            response = ollama.chat(model=summary_model, messages=[{'role': 'user', 'content': prompt}])
            summary_content = response['message']['content']

            # Save summary using metadata manager
            success = metadata.set_section_summary_content(section_idx, summary_content)
            if success:
                summaries_generated += 1
                summary_duration = (datetime.now() - summary_start_time).total_seconds()
                logger.info(f"Generated summary for section {section_idx} (duration: {summary_duration:.2f}s)")

            else:
                logger.error(f"Failed to save summary for section {section_idx}")

        except Exception as e:
            logger.error(f"Error generating summary for section {section_idx}: {e}")
            continue

    logger.info(f"Summary generation completed. Generated {summaries_generated} summaries")
    return summaries_generated




if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='Parse FB2 files and generate summaries',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  python parse_and_summarize.py book.fb2
  python parse_and_summarize.py book.fb2 --output processed --metafile meta.json
  python parse_and_summarize.py book.fb2 --model llama2:7b
  python parse_and_summarize.py book.fb2 --model gemma:7b --output results
        '''
    )
    parser.add_argument('fb2_file', help='Path to FB2 file to process')
    parser.add_argument(
        '--output',
        default='processed_book',
        help='Output directory for processed sections and summaries (default: processed_book)'
    )
    parser.add_argument(
        '--metafile',
        default=None,
        help='Path to metadata file (default: output_dir/sections_metadata.json)'
    )
    parser.add_argument(
        '--model',
        default='qwen2.5:14b-instruct',
        help='Ollama model to use for summary generation (default: qwen2.5:14b-instruct)'
    )

    args = parser.parse_args()

    try:
        output_dir = parse_and_summarize_fb2_from_file(
            file_path=args.fb2_file,
            output_dir=args.output,
            metadata_file=args.metafile,
            model=args.model
        )
        logger.info(f"Processing completed. Results saved to: {output_dir}")
    except FileNotFoundError as e:
        logger.error(f"Error: {e}")
        exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        exit(1)
