#!/usr/bin/env python3
"""
Скрипт для тестирования различных моделей Ollama на одном контенте.

Принимает входной файл, список моделей, промпт файл и создает сравнительный отчет
с результатами работы каждой модели.

Usage:
    python test_ollama_models.py --input content.txt --output results.txt --prompt prompts/summarize.txt
    python test_ollama_models.py --input content.txt --output results.txt --models "llama2:7b,gemma:7b" --prompt custom.txt
"""

import argparse
import os
import sys
import time
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import List, Optional



def get_available_models() -> List[str]:
    """Get list of available models from ollama list command

    Returns:
        List of model names
    """
    try:
        result = subprocess.run(['ollama', 'list'],
                              capture_output=True,
                              text=True,
                              check=True)

        models = []
        lines = result.stdout.strip().split('\n')

        # Skip header line and parse model names
        for line in lines[1:]:
            if line.strip():
                # Model name is the first column
                model_name = line.split()[0]
                if model_name and model_name != 'NAME':
                    models.append(model_name)

        return models

    except subprocess.CalledProcessError as e:
        print(f"Error getting ollama models: {e}")
        return []
    except FileNotFoundError:
        print("Error: ollama command not found. Make sure Ollama is installed and in PATH")
        return []


def load_prompt_template(prompt_file: str) -> str:
    """Load prompt template from file

    Args:
        prompt_file: Path to prompt template file

    Returns:
        Prompt template string
    """
    try:
        path = Path(prompt_file)
        if not path.is_file():
            raise FileNotFoundError(f"Prompt file not found: {prompt_file}")

        return path.read_text(encoding='utf-8')

    except Exception as e:
        print(f"Error loading prompt template: {e}")
        # Fallback prompt
        return ("Создай краткое содержание этого текста "
                "(максимум 500 слов). Фокус на ключевых моментах:\n\n{content}")


def load_input_content(input_file: str) -> tuple[str, str]:
    """Load content from input file

    Args:
        input_file: Path to input file

    Returns:
        Tuple of (content_text, title) where title is extracted from first line if present
    """
    try:
        path = Path(input_file)
        if not path.is_file():
            raise FileNotFoundError(f"Input file not found: {input_file}")

        content = path.read_text(encoding='utf-8')

        # Extract title if present (first line with === markers)
        lines = content.split('\n')
        if lines and lines[0].startswith('===') and lines[0].endswith('==='):
            title = lines[0].strip('= ')
            content_text = '\n'.join(lines[2:])  # Skip title and empty line
        else:
            title = path.stem
            content_text = content

        return content_text.strip(), title

    except Exception as e:
        raise Exception(f"Error loading input file: {e}")


def test_model(model: str, input_file: str, prompt_file: str, max_retries: int = 2) -> tuple[str, float, Optional[str]]:
    """Test a single model with the given input file and prompt using summarize.py

    Args:
        model: Model name to test
        input_file: Path to original input file
        prompt_file: Path to prompt template file
        max_retries: Maximum number of retry attempts

    Returns:
        Tuple of (result_text, duration_seconds, error_message)
    """
    for attempt in range(max_retries + 1):
        try:
            print(f"    Testing {model} (attempt {attempt + 1}/{max_retries + 1})...")

            start_time = time.time()

            # Create temporary output file for summarize.py result
            with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as temp_output:
                temp_output_path = temp_output.name

            try:
                # Temporarily modify sys.argv to pass arguments to summarize.py
                original_argv = sys.argv.copy()
                sys.argv = [
                    'summarize.py',
                    '--input', input_file,
                    '--output', temp_output_path,
                    '--model', model,
                    '--prompt_file', prompt_file
                ]

                print(f"    Command: python {' '.join(sys.argv)}")  # Show command being executed

                try:
                    # Import and execute summarize.py main function
                    import summarize
                    summarize.main()

                except SystemExit:
                    # summarize.py might call sys.exit(), which is normal
                    pass

                finally:
                    # Restore original argv
                    sys.argv = original_argv

                # Read the result from temporary file
                with open(temp_output_path, 'r', encoding='utf-8') as f:
                    result_content = f.read()

                # Extract just the summary text (skip title if present)
                lines = result_content.split('\n')
                if lines and lines[0].startswith('===') and lines[0].endswith('==='):
                    result_text = '\n'.join(lines[2:]).strip()  # Skip title and empty line
                else:
                    result_text = result_content.strip()

            finally:
                # Cleanup temporary file
                try:
                    os.unlink(temp_output_path)
                except:
                    pass

            duration = time.time() - start_time

            # Show what we captured in console
            print(f"    ✓ {model} completed in {duration:.2f}s")
            print(f"    Generated summary ({len(result_text)} chars):")
            # Show first few lines of the summary
            preview_lines = result_text.split('\n')[:3]
            for line in preview_lines:
                print(f"      {line}")
            if len(result_text.split('\n')) > 3:
                print(f"      ... (and {len(result_text.split('\n')) - 3} more lines)")

            return result_text, duration, None


        except Exception as e:
            error_msg = f"summarize.py failed: {str(e)}"
            print(f"    ❌ {model} failed (attempt {attempt + 1}): {error_msg}")

            if attempt < max_retries:
                print(f"    Retrying {model}...")
                time.sleep(2)  # Wait before retry
            else:
                return "", 0.0, error_msg


def write_results_header(output_file: str, input_info: dict, total_models: int):
    """Write header and summary table header to output file

    Args:
        output_file: Path to output file
        input_info: Information about input file
        total_models: Total number of models to test
    """
    try:
        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, 'w', encoding='utf-8') as f:
            # Write header
            f.write("=" * 80 + "\n")
            f.write("OLLAMA MODELS COMPARISON TEST RESULTS\n")
            f.write("=" * 80 + "\n")
            f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Input file: {input_info['file']}\n")
            f.write(f"Content title: {input_info['title']}\n")
            f.write(f"Content length: {input_info['length']:,} characters\n")
            f.write(f"Prompt file: {input_info['prompt_file']}\n")
            f.write(f"Models to test: {total_models}\n")
            f.write("\n")

            # Write summary table header (will be filled as we go)
            f.write("SUMMARY TABLE:\n")
            f.write("-" * 80 + "\n")
            f.write(f"{'Model':<25} {'Status':<10} {'Duration':<12} {'Result Length':<15}\n")
            f.write("-" * 80 + "\n")

    except Exception as e:
        raise Exception(f"Error writing results header: {e}")


def append_result_to_file(output_file: str, result: dict, is_summary_line: bool = True):
    """Append a single result to output file

    Args:
        output_file: Path to output file
        result: Result dictionary
        is_summary_line: If True, write to summary table; if False, write detailed result
    """
    try:
        with open(output_file, 'a', encoding='utf-8') as f:
            if is_summary_line:
                # Write summary table line
                status = "✓ Success" if result['success'] else "❌ Failed"
                duration = f"{result['duration']:.2f}s" if result['success'] else "N/A"
                result_len = f"{len(result['result'])}" if result['success'] else "N/A"

                f.write(f"{result['model']:<25} {status:<10} {duration:<12} {result_len:<15}\n")
                f.flush()  # Ensure immediate write to disk
            else:
                # Write detailed result
                f.write("\n" + "=" * 80 + "\n")
                f.write(f"== {result['model']}\n")
                f.write("=" * 80 + "\n")
                f.write(f"Processing time: {result['duration']:.2f} seconds\n")

                if result['success']:
                    f.write(f"Status: ✓ SUCCESS\n")
                    f.write(f"Result length: {len(result['result'])} characters\n")
                    f.write("\n")
                    f.write(result['result'])
                else:
                    f.write("Status: ❌ FAILED\n")
                    f.write(f"Error: {result['error']}\n")

                f.write("\n")
                f.flush()  # Ensure immediate write to disk

    except Exception as e:
        print(f"Warning: Could not append result to file: {e}")


def finalize_results_file(output_file: str, summary_stats: dict):
    """Write final summary statistics to results file

    Args:
        output_file: Path to output file
        summary_stats: Dictionary with summary statistics
    """
    try:
        with open(output_file, 'a', encoding='utf-8') as f:
            f.write("\n" + "=" * 80 + "\n")
            f.write("FINAL STATISTICS\n")
            f.write("=" * 80 + "\n")
            f.write(f"Total models tested: {summary_stats['total_models']}\n")
            f.write(f"Successful tests: {summary_stats['successful_tests']}\n")
            f.write(f"Failed tests: {summary_stats['failed_tests']}\n")
            f.write(f"Total processing time: {summary_stats['total_time']:.2f} seconds\n")
            f.write(f"Average time per model: {summary_stats['avg_time_per_model']:.2f} seconds\n")

            if summary_stats['failed_models']:
                f.write(f"Failed models: {', '.join(summary_stats['failed_models'])}\n")

            f.write("\n" + "=" * 80 + "\n")
            f.write("END OF RESULTS\n")
            f.write("=" * 80 + "\n")

    except Exception as e:
        print(f"Warning: Could not write final statistics: {e}")


def write_results(output_file: str, results: List[dict], input_info: dict):
    """Write test results to output file (legacy function for compatibility)

    Args:
        output_file: Path to output file
        results: List of result dictionaries
        input_info: Information about input file
    """
    print("Note: Using legacy write_results. Consider using streaming approach for better performance.")

    # Calculate summary stats
    successful_tests = sum(1 for r in results if r['success'])
    failed_tests = len(results) - successful_tests
    total_time = sum(r['duration'] for r in results)
    avg_time = total_time / len(results) if results else 0
    failed_models = [r['model'] for r in results if not r['success']]

    summary_stats = {
        'total_models': len(results),
        'successful_tests': successful_tests,
        'failed_tests': failed_tests,
        'total_time': total_time,
        'avg_time_per_model': avg_time,
        'failed_models': failed_models
    }

    # Write header
    write_results_header(output_file, input_info, len(results))

    # Write summary table
    for result in results:
        append_result_to_file(output_file, result, is_summary_line=True)

    # Write detailed results
    for result in results:
        append_result_to_file(output_file, result, is_summary_line=False)

    # Write final statistics
    finalize_results_file(output_file, summary_stats)

    print(f"Results written to: {output_file}")


def main():
    parser = argparse.ArgumentParser(
        description='Test multiple Ollama models with the same content and prompt',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  # Test with default models from ollama list
  python test_ollama_models.py --input content.txt --output results.txt --prompt prompts/summarize.txt
  
  # Test specific models
  python test_ollama_models.py --input content.txt --output results.txt --models "llama2:7b,gemma:7b,qwen2.5:14b-instruct" --prompt prompts/summarize.txt
  
  # Test with brief summary prompt
  python test_ollama_models.py --input section.txt --output comparison.txt --prompt prompts/brief_summary.txt
        '''
    )

    parser.add_argument(
        '--input',
        required=True,
        help='Input file with content to test'
    )

    parser.add_argument(
        '--output',
        required=True,
        help='Output file to save comparison results'
    )

    parser.add_argument(
        '--models',
        help='Comma-separated list of model names (if not specified, use ollama list)'
    )

    parser.add_argument(
        '--prompt',
        required=True,
        help='Prompt template file (use {content} placeholder)'
    )

    args = parser.parse_args()

    try:
        # Load input content
        print("Loading input content...")
        content_text, title = load_input_content(args.input)
        print(f"Content loaded: {len(content_text):,} characters")

        # Load prompt template
        print("Loading prompt template...")
        prompt_template = load_prompt_template(args.prompt)
        print(f"Prompt template loaded: {len(prompt_template)} characters")

        # Verify prompt template has {content} placeholder
        if '{content}' not in prompt_template:
            print("Warning: Prompt template does not contain {content} placeholder")

        # Get models to test
        if args.models:
            models = [m.strip() for m in args.models.split(',')]
            print(f"Using specified models: {models}")
        else:
            print("Getting available models from ollama...")
            models = get_available_models()
            if not models:
                print("No models found. Make sure Ollama is running and has models installed.")
                sys.exit(1)
            print(f"Found {len(models)} models: {models}")

        # Test each model with streaming results
        print(f"\nTesting {len(models)} models...")
        print(f"Results will be written to: {args.output}")

        # Initialize results file
        input_info = {
            'file': args.input,
            'title': title,
            'length': len(content_text),
            'prompt_file': args.prompt
        }

        write_results_header(args.output, input_info, len(models))

        # Track statistics
        successful_tests = 0
        failed_tests = 0
        failed_models = []
        total_start_time = time.time()

        for i, model in enumerate(models, 1):
            print(f"\n[{i}/{len(models)}] Testing model: {model}")

            # Pass original input file directly to test_model
            result_text, duration, error = test_model(model, args.input, args.prompt)

            result = {
                'model': model,
                'result': result_text,
                'duration': duration,
                'success': error is None,
                'error': error
            }

            # Update statistics
            if result['success']:
                successful_tests += 1
            else:
                failed_tests += 1
                failed_models.append(model)

            # Write result to file immediately
            append_result_to_file(args.output, result, is_summary_line=True)
            append_result_to_file(args.output, result, is_summary_line=False)

            print(f"    Result written to file ({successful_tests + failed_tests}/{len(models)} completed)")

        total_duration = time.time() - total_start_time

        # Write final statistics
        summary_stats = {
            'total_models': len(models),
            'successful_tests': successful_tests,
            'failed_tests': failed_tests,
            'total_time': total_duration,
            'avg_time_per_model': total_duration / len(models) if models else 0,
            'failed_models': failed_models
        }

        finalize_results_file(args.output, summary_stats)

        # Print summary
        print(f"\nTesting completed!")
        print(f"Total time: {total_duration:.2f} seconds")
        print(f"Successful tests: {successful_tests}/{len(models)}")
        print(f"Results saved to: {args.output}")

        if successful_tests < len(models):
            print(f"Failed models: {failed_models}")

    except KeyboardInterrupt:
        print("\nTest interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
