#!/usr/bin/env python3
"""
Test script for summary generation using LangChain LLM API with DTO objects and external prompts.

Usage:
    python test_prompt.py --content_file path/to/content.txt
    python test_prompt.py --content_file content.txt --system_prompt prompts/custom_system.json
    python test_prompt.py --content_file content.txt --user_profile prompts/custom_profile.json
    python test_prompt.py --content_file content.txt --prompt_template prompts/custom_template.txt
"""

import argparse
import os
import sys
import time
from pathlib import Path

from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from llm_config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, DEEPSEEK_TEMPERATURE
from llm_dto import SystemPromptConfig, UserProfile, LLMRequest
from logging_config import get_logger

logger = get_logger(__name__)

# Ollama local configuration
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")

# Models that should use OpenAI-compatible API (DeepSeek)
OPENAI_MODELS = ['deepseek-chat', 'deepseek-reasoner', 'gpt-3.5-turbo', 'gpt-4']

# Default paths for prompt files
DEFAULT_SYSTEM_PROMPT = "prompts/system_prompt_default.json"
DEFAULT_USER_PROFILE = "prompts/user_profile_default.json"
DEFAULT_PROMPT_TEMPLATE = "prompts/summarize_template.txt"


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


def create_llm_request(
        content: str,
        text: str,
        system_prompt_file: str,
        user_profile_file: str,
        prompt_template: str
) -> LLMRequest:
    """Create LLMRequest DTO from files and content

    Args:
        content: Content to summarize
        system_prompt_file: Path to system prompt JSON file
        user_profile_file: Path to user profile JSON file
        prompt_template: Prompt template string with {content} placeholder

    Returns:
        LLMRequest DTO object
    """
    # Load SystemPromptConfig from file
    system_prompt = SystemPromptConfig(load_from_file=system_prompt_file)
    logger.info(f"Loaded SystemPromptConfig from: {system_prompt_file}")

    # Load UserProfile from file (optional)
    user_profile = None
    if user_profile_file and Path(user_profile_file).exists():
        user_profile = UserProfile(load_from_file=user_profile_file)
        logger.info(f"Loaded UserProfile from: {user_profile_file}")

    # Format user query with prompt template and content
    # Limit content to 100k characters to avoid token limits
    limited_content = content[:100000]
    limited_text = text[:100000] if text else ""
    user_query = prompt_template.format(content=limited_content, text=limited_text)

    # Create LLMRequest
    request = LLMRequest(
        system_prompt=system_prompt,
        user_profile=user_profile,
        user_query=user_query,
        chat_history=[]
    )

    return request


def call_ollama_with_dto(request: LLMRequest, model: str = None) -> str:
    """Call LLM using LangChain API with LLMRequest DTO as single combined message

    Args:
        request: LLMRequest DTO object
        model: Model name to use for generation (default: from config)
               - OpenAI models: deepseek-chat, deepseek-reasoner, gpt-*
               - Ollama models: qwen2.5:14b-instruct, llama2:7b, etc.

    Returns:
        Generated summary text

    Raises:
        Exception: If LLM API call fails
    """
    try:
        # Use provided model or default from config
        llm_model = model or DEEPSEEK_MODEL

        # Determine if we should use OpenAI API or local Ollama
        use_openai = any(llm_model.startswith(m) for m in OPENAI_MODELS)

        # Initialize LLM based on model type
        if use_openai:
            logger.info(f"Using OpenAI-compatible API for model: {llm_model}")
            llm = ChatOpenAI(
                model=llm_model,
                openai_api_key=DEEPSEEK_API_KEY,
                openai_api_base=DEEPSEEK_BASE_URL,
                temperature=DEEPSEEK_TEMPERATURE
            )
        else:
            logger.info(f"Using local Ollama for model: {llm_model} at {OLLAMA_BASE_URL}")
            llm = ChatOllama(
                model=llm_model,
                base_url=OLLAMA_BASE_URL,
                temperature=DEEPSEEK_TEMPERATURE
            )

        # Build single combined message from LLMRequest components
        message_parts = []

        # Add system prompt
        message_parts.append(request.get_system_prompt_text())

        # Add user profile if present
        if request.user_profile:
            message_parts.append(request.get_user_profile_text())

        # Add conversation summary if present
        if request.conversation_summary:
            message_parts.append(request.get_conversation_summary_text())

        # Add key facts if present
        key_facts_text = request.get_key_facts_text()
        if key_facts_text:
            message_parts.append(key_facts_text)

        # Add chat history if present
        chat_history_text = request.get_chat_history_text()
        if chat_history_text:
            message_parts.append(chat_history_text)

        # Add current user message
        message_parts.append(f"<current_user_message>\n{request.user_query}\n</current_user_message>")

        # Combine all parts into single message
        combined_message = "\n\n".join(message_parts)

        # Log the combined message with preview
        preview = combined_message[:1000] + "..." if len(combined_message) > 1000 else combined_message
        api_type = "OpenAI API" if use_openai else "Ollama"
        logger.info(
            f"\nCalling {api_type} ({llm_model}) with combined message ({len(combined_message):,} chars):\n"
            f"{'=' * 80}\n"
            f"{preview}\n"
            f"{'=' * 80}"
        )

        # Call LLM via LangChain
        response = llm.invoke(combined_message)

        return response.content

    except Exception as e:
        raise Exception(f"LLM API call failed: {e}")


def main():
    parser = argparse.ArgumentParser(
        description='Test summary generation using LangChain LLM API with DTO objects',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  # Using default model (DeepSeek from config)
  python test_prompt.py --content_file section.txt

  # Using custom system prompt
  python test_prompt.py --content_file book_chapter.txt --system_prompt prompts/custom_system.json

  # Using custom user profile
  python test_prompt.py --content_file content.txt --user_profile prompts/vadim.json

  # Using OpenAI-compatible model (DeepSeek)
  python test_prompt.py --content_file content.txt --model deepseek-chat

  # Using local Ollama model
  python test_prompt.py --content_file content.txt --model qwen2.5:14b-instruct
  python test_prompt.py --content_file content.txt --model llama2:7b
        '''
    )

    parser.add_argument(
        '--content_file',
        required=True,
        help='Path to file containing data for `content` placeholder (if used in prompt template)'
    )

    parser.add_argument(
        '--text_file',
        required=False,
        help='Path to file containing data for `text` placeholder (if used in prompt template)'
    )

    parser.add_argument(
        '--system_prompt',
        default=DEFAULT_SYSTEM_PROMPT,
        help=f'Path to system prompt JSON file (default: {DEFAULT_SYSTEM_PROMPT})'
    )

    parser.add_argument(
        '--user_profile',
        default=DEFAULT_USER_PROFILE,
        help=f'Path to user profile JSON file (default: {DEFAULT_USER_PROFILE})'
    )

    parser.add_argument(
        '--prompt_template',
        default=DEFAULT_PROMPT_TEMPLATE,
        help=f'Path to prompt template file (default: {DEFAULT_PROMPT_TEMPLATE})'
    )

    parser.add_argument(
        '--model',
        default=None,
        help=f'Model name (default: {DEEPSEEK_MODEL}). '
             f'OpenAI models: deepseek-chat, deepseek-reasoner, gpt-4. '
             f'Ollama models: qwen2.5:14b-instruct, llama2:7b, etc.'
    )

    parser.add_argument(
        '--output',
        help='Output file path (optional, prints to console if not specified)'
    )

    args = parser.parse_args()

    # Start timing
    start_time = time.time()
    statistics = {
        'content_chars': 0,
        'prompt_template_chars': 0,
        'formatted_query_chars': 0,
        'summary_chars': 0,
        'file_load_time': 0.0,
        'dto_creation_time': 0.0,
        'llm_call_time': 0.0,
        'total_time': 0.0
    }

    try:
        # Track file loading time
        file_load_start = time.time()

        prompt_template = load_file_content(args.prompt_template)
        statistics['prompt_template_chars'] = len(prompt_template)
        logger.info(f"Loaded prompt template from: {args.prompt_template} ({statistics['prompt_template_chars']:,} chars)")

        content = load_file_content(args.content_file)
        statistics['content_chars'] = len(content)
        logger.info(f"Loaded content from: {args.content_file} ({statistics['content_chars']:,} chars)")

        text = load_file_content(args.text_file)

        statistics['file_load_time'] = time.time() - file_load_start

        # Show previews of prompts and content (first 1000 chars with "..." if longer)
        preview_template = prompt_template[:1000] + "..." if len(prompt_template) > 1000 else prompt_template
        preview_content = content[:1000] + "..." if len(content) > 1000 else content

        logger.info(
            f"\n{'=' * 80}\n"
            f"📄 PROMPT TEMPLATE CONTENT:\n"
            f"{'=' * 80}\n"
            f"{preview_template}\n"
            f"{'=' * 80}\n"
            f"\n"
            f"📝 CONTENT TO PROCESS:\n"
            f"{'=' * 80}\n"
            f"{preview_content}\n"
            f"{'=' * 80}"
        )

        # Track DTO creation time
        dto_start = time.time()

        llm_request = create_llm_request(
            content=content,
            text=text,
            system_prompt_file=args.system_prompt,
            user_profile_file=args.user_profile,
            prompt_template=prompt_template
        )

        llm_request.system_prompt.assistant_name += f" ({args.model})"

        statistics['formatted_query_chars'] = len(llm_request.user_query)
        statistics['dto_creation_time'] = time.time() - dto_start

        logger.info(
            f"\n{'=' * 80}\n"
            f"LLMRequest DTO created:\n"
            f"  System prompt: {llm_request.system_prompt.assistant_name}\n"
            f"  User profile: {llm_request.user_profile.name if llm_request.user_profile else 'None'}\n"
            f"  Query length: {statistics['formatted_query_chars']:,} chars\n"
            f"{'=' * 80}"
        )

        logger.info(f"Using model: {args.model or DEEPSEEK_MODEL}")
        logger.info("Calling LLM for summary generation...")

        # Track LLM call time
        llm_start = time.time()

        summary = call_ollama_with_dto(llm_request, args.model)


        statistics['llm_call_time'] = time.time() - llm_start
        statistics['summary_chars'] = len(summary)

        # Calculate total time
        statistics['total_time'] = time.time() - start_time

        # Output results
        logger.info(f"\nLLM RESPONSE:\n{'=' * 80}\n{summary}\n{'=' * 80}")

        # Save to file if output specified
        if args.output:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(summary, encoding='utf-8')
            logger.info(f"Summary saved to: {args.output}")

        # Print detailed statistics
        compression_ratio = statistics['content_chars'] / statistics['summary_chars'] if statistics['summary_chars'] > 0 else 0
        chars_per_sec = statistics['summary_chars'] / statistics['llm_call_time'] if statistics['llm_call_time'] > 0 else 0

        logger.info(
            f"\n"
            f"📊 STATISTICS:\n"
            f"{'=' * 80}\n"
            f"Content length:         {statistics['content_chars']:,} characters\n"
            f"Prompt template length: {statistics['prompt_template_chars']:,} characters\n"
            f"Formatted query length: {statistics['formatted_query_chars']:,} characters\n"
            f"Summary length:         {statistics['summary_chars']:,} characters\n"
            f"\n"
            f"⏱️  TIMING:\n"
            f"File loading:           {statistics['file_load_time']:.3f} seconds\n"
            f"DTO creation:           {statistics['dto_creation_time']:.3f} seconds\n"
            f"LLM API call:           {statistics['llm_call_time']:.3f} seconds\n"
            f"Total elapsed:          {statistics['total_time']:.3f} seconds\n"
            f"\n"
            f"📈 PERFORMANCE:\n"
            f"Characters per second:  {chars_per_sec:,.0f} chars/sec (generation)\n"
            f"Compression ratio:      {compression_ratio:.1f}:1 (content → summary)\n"
            f"{'=' * 80}"
        )

    except FileNotFoundError as e:
        logger.error(f"Error: {e}")
        sys.exit(1)
    except IOError as e:
        logger.error(f"Error: {e}")
        sys.exit(1)
    except KeyboardInterrupt as e:
        logger.warning("Process interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
