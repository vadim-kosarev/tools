#!/usr/bin/env python3
"""
Скрипт для создания краткого содержания секций книги.

Поддерживает различные режимы работы:
1. Обработка одного файла с выводом в консоль или файл
2. Массовая обработка всех секций из метаданных
3. Обновление метаданных с путями к файлам саммари

Usage:
    # Обработать один файл с выводом в консоль
    python summarize.py --input section.txt

    # Обработать файл с сохранением в файл и обновлением метаданных
    python summarize.py --input section.txt --output summary.txt --metafile meta.json

    # Массовая обработка всех секций из метаданных
    python summarize.py --metafile meta.json

    # С указанием модели
    python summarize.py --metafile meta.json --model llama2:7b
"""

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import ollama

from metadata_manager import ParseResultCollector


def load_prompt_template(prompt_file: str = 'prompts/summarize.txt') -> str:
    """Load prompt template from file

    Args:
        prompt_file: Path to prompt template file

    Returns:
        Prompt template string
    """
    try:
        script_dir = Path(__file__).parent
        prompt_path = script_dir / prompt_file
        return prompt_path.read_text(encoding='utf-8')
    except Exception as e:
        print(f"Warning: Could not load prompt from {prompt_file}: {e}")
        # Fallback prompt
        return ("Создай краткое содержание этого раздела книги "
                "(максимум 500 слов). Фокус на сюжете, героях, "
                "ключевых событиях и локациях:\n\n{content}")


def generate_summary(content: str, model: str = 'qwen2.5:14b-instruct',
                    prompt_template: str = None) -> str:
    """Generate summary for given content using ollama

    Args:
        content: Text content to summarize
        model: Ollama model to use
        prompt_template: Prompt template with {content} placeholder

    Returns:
        Generated summary text

    Raises:
        Exception: If ollama call fails
    """
    if prompt_template is None:
        prompt_template = load_prompt_template()

    # Limit content to avoid token overflow
    limited_content = content[:100000]
    prompt = prompt_template.format(content=limited_content)

    try:
        response = ollama.chat(
            model=model,
            messages=[{'role': 'user', 'content': prompt}]
        )
        return response['message']['content']
    except Exception as e:
        raise Exception(f"Ollama API call failed: {e}")


def process_single_file(input_file: str, output_file: Optional[str] = None,
                       model: str = 'qwen2.5:14b-instruct',
                       metafile: Optional[str] = None,
                       prompt_file: str = 'prompts/summarize.txt') -> bool:
    """Process single input file and generate summary

    Args:
        input_file: Path to input file with content
        output_file: Optional path to output file, if None - print to console
        model: Ollama model to use
        metafile: Optional metadata file to update
        prompt_file: Path to prompt template file

    Returns:
        True if successful, False otherwise
    """
    start_time = time.time()

    # Load content from input file
    try:
        input_path = Path(input_file)
        if not input_path.is_file():
            print(f"Error: Input file not found: {input_file}")
            return False

        content = input_path.read_text(encoding='utf-8')
        print(f"Loaded content from {input_file}: {len(content)} characters")

        # Extract content (skip title line if present)
        lines = content.split('\n')
        if lines and lines[0].startswith('===') and lines[0].endswith('==='):
            content_text = '\n'.join(lines[2:])  # Skip title and empty line
            section_title = lines[0].strip('= ')
        else:
            content_text = content
            section_title = input_path.stem

    except Exception as e:
        print(f"Error reading input file: {e}")
        return False

    # Check if content is large enough
    min_size = 1000  # Minimum size for summarization
    if len(content_text.strip()) < min_size:
        print(f"Warning: Content is too small ({len(content_text)} chars), skipping summarization")
        return False

    # Generate summary
    try:
        print(f"Generating summary using model: {model}")
        summary_start = time.time()

        # Load prompt template from specified file
        prompt_template = load_prompt_template(prompt_file)
        summary = generate_summary(content_text, model, prompt_template)

        summary_time = time.time() - summary_start
        print(f"Summary generated in {summary_time:.2f} seconds")

    except Exception as e:
        print(f"Error generating summary: {e}")
        return False

    # Output summary
    if output_file:
        try:
            output_path = Path(output_file)
            output_path.parent.mkdir(parents=True, exist_ok=True)

            # Write summary with title
            with open(output_path, 'w', encoding='utf-8') as f:
                if section_title:
                    f.write(f"=== {section_title} ===\n\n")
                f.write(summary)

            print(f"Summary saved to: {output_file}")

        except Exception as e:
            print(f"Error saving summary to file: {e}")
            return False
    else:
        # Output to console
        print("\n" + "="*80)
        print("GENERATED SUMMARY:")
        print("="*80)
        if section_title:
            print(f"=== {section_title} ===\n")
        print(summary)
        print("="*80)

    # Update metadata if provided
    if metafile and output_file:
        try:
            metadata = ParseResultCollector(metafile)

            # Find section by input file path and update summary_file
            all_sections = metadata.get_all_sections()
            input_file_norm = os.path.normpath(input_file)

            for idx_str, section_meta in all_sections.items():
                if isinstance(section_meta, dict):
                    section_file = section_meta.get('section_file', '')
                    if section_file and os.path.normpath(section_file) == input_file_norm:
                        # Update metadata with summary file path
                        section_meta['summary_file'] = output_file
                        section_meta['summary_generated_at'] = datetime.now().isoformat()
                        metadata.set(f'sections.{idx_str}', section_meta)
                        print(f"Updated metadata for section {idx_str}")
                        break
            else:
                print(f"Warning: Section with input file {input_file} not found in metadata")

        except Exception as e:
            print(f"Warning: Could not update metadata: {e}")

    total_time = time.time() - start_time
    print(f"Total processing time: {total_time:.2f} seconds")

    return True


def process_section(section_meta: dict, metadata: ParseResultCollector,
                   model: str = 'qwen2.5:14b-instruct',
                   prompt_file: str = 'prompts/summarize.txt') -> bool:
    """Process single section from metadata

    Args:
        section_meta: Section metadata dictionary
        metadata: ParseResultCollector instance
        model: Ollama model to use
        prompt_file: Path to prompt template file

    Returns:
        True if summary was generated, False otherwise
    """
    section_idx = section_meta.get('idx')
    section_title = section_meta.get('title', '')
    section_file_path = section_meta.get('section_file', '')
    existing_summary_file = section_meta.get('summary_file')

    print(f"\nProcessing section {section_idx}: '{section_title}'")

    # Skip if summary already exists
    if existing_summary_file and os.path.exists(existing_summary_file):
        print(f"  Summary already exists: {existing_summary_file}, skipping")
        return False

    # Skip if section file doesn't exist
    if not section_file_path or not os.path.exists(section_file_path):
        print(f"  Section file not found: {section_file_path}, skipping")
        return False

    # Check if content is large enough
    try:
        with open(section_file_path, 'r', encoding='utf-8') as f:
            file_content = f.read()

        # Extract content (skip title line if present)
        lines = file_content.split('\n')
        if lines and lines[0].startswith('===') and lines[0].endswith('==='):
            content = '\n'.join(lines[2:])  # Skip title and empty line
        else:
            content = file_content

        min_size = 5000
        if len(content.strip()) < min_size:
            print(f"  Content too small ({len(content)} chars), skipping")
            return False

        print(f"  Content length: {len(content):,} characters")

    except Exception as e:
        print(f"  Error reading section file: {e}")
        return False

    # Create summary file path
    section_path = Path(section_file_path)
    summary_dir = section_path.parent.parent / 'summaries'
    summary_dir.mkdir(exist_ok=True)
    summary_file = summary_dir / section_path.name

    # Check if summary file already exists
    if summary_file.exists():
        print(f"  Summary file already exists: {summary_file}, updating metadata only")
        # Update metadata with existing file path
        section_meta['summary_file'] = str(summary_file)
        section_meta['summary_updated_at'] = datetime.now().isoformat()
        metadata.set(f'sections.{section_idx}', section_meta)
        return False

    # Use process_single_file to generate summary
    try:
        success = process_single_file(
            input_file=section_file_path,
            output_file=str(summary_file),
            model=model,
            metafile=metadata.metadata_file,
            prompt_file=prompt_file
        )

        if success:
            print(f"  Summary generated and saved to: {summary_file}")
            return True
        else:
            print(f"  Failed to generate summary for section {section_idx}")
            return False

    except Exception as e:
        print(f"  Error processing section {section_idx}: {e}")
        return False


def process_all_sections(metafile: str, model: str = 'qwen2.5:14b-instruct',
                        prompt_file: str = 'prompts/summarize.txt') -> int:
    """Process all sections from metadata file

    Args:
        metafile: Path to metadata file
        model: Ollama model to use
        prompt_file: Path to prompt template file

    Returns:
        Number of summaries generated
    """
    try:
        metadata = ParseResultCollector(metafile)
        all_sections = metadata.get_all_sections()

        if not all_sections:
            print("No sections found in metadata file")
            return 0

        print(f"Found {len(all_sections)} sections in metadata")

    except Exception as e:
        print(f"Error loading metadata: {e}")
        return 0

    summaries_generated = 0

    # Sort sections by index
    sorted_sections = sorted(all_sections.items(), key=lambda x: int(x[0]))

    for idx_str, section_meta in sorted_sections:
        if not isinstance(section_meta, dict):
            continue

        # Use process_section to handle individual section processing
        if process_section(section_meta, metadata, model, prompt_file):
            summaries_generated += 1

    print(f"\nCompleted processing. Generated {summaries_generated} summaries")
    return summaries_generated


def main():
    parser = argparse.ArgumentParser(
        description='Generate summaries for book sections',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  # Process single file with console output
  python summarize.py --input section.txt
  
  # Process file with output to file
  python summarize.py --input section.txt --output summary.txt
  
  # Process file with metadata update
  python summarize.py --input section.txt --output summary.txt --metafile meta.json
  
  # Process all sections from metadata
  python summarize.py --metafile meta.json
  
  # Use different model and prompt file
  python summarize.py --metafile meta.json --model llama2:7b --prompt_file custom_prompt.txt
        '''
    )

    parser.add_argument(
        '--input',
        help='Input file with content to summarize'
    )

    parser.add_argument(
        '--output',
        help='Output file to save summary (if not specified, output to console)'
    )

    parser.add_argument(
        '--metafile',
        help='Metadata file to read sections from or update'
    )

    parser.add_argument(
        '--model',
        default='qwen2.5:14b-instruct',
        help='Ollama model to use for summary generation (default: qwen2.5:14b-instruct)'
    )

    parser.add_argument(
        '--prompt_file',
        default='prompts/summarize.txt',
        help='Path to prompt template file (default: prompts/summarize.txt)'
    )

    args = parser.parse_args()

    # Validate arguments
    if not args.input and not args.metafile:
        print("Error: Either --input or --metafile must be specified")
        sys.exit(1)

    if args.input and not os.path.exists(args.input):
        print(f"Error: Input file not found: {args.input}")
        sys.exit(1)

    if args.metafile and not os.path.exists(args.metafile):
        print(f"Error: Metadata file not found: {args.metafile}")
        sys.exit(1)

    try:
        if args.input:
            # Single file mode
            success = process_single_file(
                input_file=args.input,
                output_file=args.output,
                model=args.model,
                metafile=args.metafile,
                prompt_file=args.prompt_file
            )

            if not success:
                sys.exit(1)

        elif args.metafile:
            # Batch processing mode
            summaries_count = process_all_sections(
                metafile=args.metafile,
                model=args.model,
                prompt_file=args.prompt_file
            )

            if summaries_count == 0:
                print("No summaries were generated")
            else:
                print(f"Successfully generated {summaries_count} summaries")

    except KeyboardInterrupt:
        print("\nOperation interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
