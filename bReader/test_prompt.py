#!/usr/bin/env python3
"""
Test script for summary generation using ollama with external prompts.

Usage:
    python test_prompt.py --content_file path/to/content.txt --prompt_file path/to/prompt.txt
"""

import argparse
import sys
import time
from pathlib import Path

import ollama


def load_file_content(file_path: str) -> str:
    """Load content from file

    Args:
        file_path: Path to file to read

    Returns:
        File content as string

    Raises:
        FileNotFoundError: If file doesn't exist
        IOError: If file can't be read
    """
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")

    try:
        return path.read_text(encoding='utf-8')
    except Exception as e:
        raise IOError(f"Could not read file {path}: {e}")


def format_prompt_with_content(prompt_template: str, content: str) -> str:
    """Format prompt template with content

    Args:
        prompt_template: Prompt template with {content} placeholder
        content: Content to insert into template

    Returns:
        Formatted prompt string
    """
    # Replace {content} placeholder with actual content
    # Limit content to 100k characters to avoid token limits
    limited_content = content[:100000]
    return prompt_template.format(content=limited_content)


def call_ollama_summarize(prompt: str, model: str = 'qwen2.5:14b-instruct') -> str:
    """Call ollama to generate summary

    Args:
        prompt: Formatted prompt to send to ollama
        model: Model name to use for generation

    Returns:
        Generated summary text

    Raises:
        Exception: If ollama call fails
    """
    try:
        response = ollama.chat(
            model=model,
            messages=[{'role': 'user', 'content': prompt}]
        )
        return response['message']['content']
    except Exception as e:
        raise Exception(f"Ollama API call failed: {e}")


def main():
    parser = argparse.ArgumentParser(
        description='Test summary generation using ollama with external prompts',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  python test_prompt.py --content_file section.txt --prompt_file prompts/summarize.txt
  python test_prompt.py --content_file book_chapter.txt --prompt_file custom_prompt.txt
  python test_prompt.py --content_file content.txt --prompt_file prompts/summarize.txt --model llama2:7b
        '''
    )

    parser.add_argument(
        '--content_file',
        required=True,
        help='Path to file containing content to summarize'
    )

    parser.add_argument(
        '--prompt_file',
        required=True,
        help='Path to file containing prompt template (use {content} placeholder)'
    )

    parser.add_argument(
        '--model',
        default='qwen2.5:14b-instruct',
        help='Ollama model to use for generation (default: qwen2.5:14b-instruct)'
    )

    args = parser.parse_args()

    # Start timing
    start_time = time.time()
    statistics = {
        'content_chars': 0,
        'prompt_chars': 0,
        'formatted_prompt_chars': 0,
        'summary_chars': 0,
        'file_load_time': 0,
        'prompt_format_time': 0,
        'ollama_call_time': 0,
        'total_time': 0
    }

    try:
        # Track file loading time
        file_load_start = time.time()

        content = load_file_content(args.content_file)
        statistics['content_chars'] = len(content)
        print(f"Loading content from: {args.content_file}: {statistics['content_chars']} chars")

        prompt_template = load_file_content(args.prompt_file)
        statistics['prompt_chars'] = len(prompt_template)
        print(f"Loading prompt from: {args.prompt_file} : {statistics['prompt_chars']} chars")
        print("-" * 40)
        print(f"{prompt_template}")
        print("-" * 40)

        statistics['file_load_time'] = time.time() - file_load_start

        # Track prompt formatting time
        format_start = time.time()
        formatted_prompt = format_prompt_with_content(prompt_template, content)
        statistics['formatted_prompt_chars'] = len(formatted_prompt)
        statistics['prompt_format_time'] = time.time() - format_start

        print(f"\nUsing model: {args.model}")
        print("Calling ollama for summary generation...")
        print("=" * 80)

        # Track ollama call time
        ollama_start = time.time()
        summary = call_ollama_summarize(formatted_prompt, args.model)
        statistics['ollama_call_time'] = time.time() - ollama_start
        statistics['summary_chars'] = len(summary)

        # Calculate total time
        statistics['total_time'] = time.time() - start_time

        # Output results
        print("\nGENERATED SUMMARY:")
        print("=" * 80)
        print(summary)
        print("=" * 80)

        # Print detailed statistics
        print(f"\n📊 STATISTICS:")
        print("=" * 80)
        print(f"Content length:         {statistics['content_chars']:,} characters")
        print(f"Prompt template length: {statistics['prompt_chars']:,} characters")
        print(f"Formatted prompt length:{statistics['formatted_prompt_chars']:,} characters")
        print(f"Summary length:         {statistics['summary_chars']:,} characters")
        print()
        print(f"⏱️  TIMING:")
        print(f"File loading:           {statistics['file_load_time']:.3f} seconds")
        print(f"Prompt formatting:      {statistics['prompt_format_time']:.3f} seconds")
        print(f"Ollama API call:        {statistics['ollama_call_time']:.3f} seconds")
        print(f"Total elapsed:          {statistics['total_time']:.3f} seconds")
        print()
        print(f"📈 PERFORMANCE:")
        print(f"Characters per second:  {statistics['summary_chars'] / statistics['ollama_call_time']:,.0f} chars/sec (generation)")
        print(f"Compression ratio:      {statistics['content_chars'] / statistics['summary_chars']:.1f}:1 (content → summary)")
        print("=" * 80)

    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except IOError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
