from bs4 import BeautifulSoup
import ollama
import os
import json
import logging

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def get_section_title(section_elem):
    """Извлекает заголовок секции если он есть"""
    title_elem = section_elem.find('title')
    if title_elem:
        title_text = title_elem.get_text(separator=' ', strip=True)
        return title_text
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


def process_sections_recursive(section_elem, output_dir, sections_list, section_counter, parent_title=''):
    """Рекурсивно обрабатывает иерархию секций"""
    title = get_section_title(section_elem)
    full_title = f"{parent_title} - {title}" if parent_title and title else (title or parent_title)

    # Извлекаем содержимое текущей секции (без вложенных)
    content = extract_section_content(section_elem)

    # Ищем вложенные секции
    nested_sections = [child for child in section_elem.children if hasattr(child, 'name') and child.name == 'section']

    # Если есть вложенные секции, обрабатываем их
    if nested_sections:
        for nested_section in nested_sections:
            process_sections_recursive(nested_section, output_dir, sections_list, section_counter, full_title)
    else:
        # Если нет вложенных, сохраняем текущую секцию
        if content.strip():
            idx = section_counter[0]
            section_counter[0] += 1

            logger.info(f"Processing section {idx}: '{full_title}', length: {len(content)}")

            # Сохраняем сырой текст раздела на диск
            section_file = os.path.join(output_dir, 'sections', f'section_{idx:04d}.txt')
            with open(section_file, 'w', encoding='utf-8') as f:
                if full_title:
                    f.write(f"=== {full_title} ===\n\n")
                f.write(content)
            logger.info(f"Saved section {idx} to {section_file}")

            # Генерация саммари, если раздел большой (>5000 символов)
            summary_file = None
            if len(content) > 5000:
                logger.info(f"Generating summary for section {idx}")
                prompt = f"Создай краткое содержание этого раздела книги (максимум 300 слов). Фокус на сюжете, героях и ключевых событиях:\n\n{content[:8000]}"
                response = ollama.chat(model='qwen2.5:14b-instruct', messages=[{'role': 'user', 'content': prompt}])
                summary = response['message']['content']

                # Сохраняем саммари
                summary_file = os.path.join(output_dir, 'summaries', f'summary_{idx:04d}.txt')
                with open(summary_file, 'w', encoding='utf-8') as f:
                    if full_title:
                        f.write(f"=== {full_title} ===\n\n")
                    f.write(summary)
                logger.info(f"Saved summary for section {idx} to {summary_file}")

            # Добавляем в метаданные
            sections_list.append({
                'idx': idx,
                'title': full_title,
                'section_file': section_file,
                'summary_file': summary_file
            })


def parse_and_summarize_fb2(file_path, output_dir='processed_book'):
    logger.info(f"Starting parsing FB2 file: {file_path}")
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(os.path.join(output_dir, 'sections'), exist_ok=True)
    os.makedirs(os.path.join(output_dir, 'summaries'), exist_ok=True)

    # Парсим FB2 как XML
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            soup = BeautifulSoup(f, 'xml')
    except Exception as e:
        logger.error(f"Error parsing FB2 file: {e}")
        raise

    sections = []  # Список метаданных для конвейера
    section_counter = [0]  # Счётчик секций в списке

    # Находим body и обрабатываем его секции
    body = soup.find('body')
    if body:
        # Пропускаем title в body и обрабатываем секции
        for child in body.children:
            if hasattr(child, 'name') and child.name == 'section':
                process_sections_recursive(child, output_dir, sections, section_counter)
    else:
        logger.warning("No body element found in FB2 file")

    # Сохраняем метаданные
    with open(os.path.join(output_dir, 'sections_metadata.json'), 'w', encoding='utf-8') as f:
        json.dump(sections, f, ensure_ascii=False, indent=2)
    logger.info(f"Completed processing {len(sections)} sections. Saved metadata to {os.path.join(output_dir, 'sections_metadata.json')}")

    return output_dir  # Возвращаем директорию для следующего шага

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        parse_and_summarize_fb2(sys.argv[1])
    else:
        print("Usage: python parse_and_summarize.py <fb2_file_path>")
