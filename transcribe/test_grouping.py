#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тестовый скрипт для проверки логики группировки предложений в блоки
"""

import sys
from pathlib import Path

# Добавляем путь к t_gigaam.py
sys.path.insert(0, str(Path(__file__).parent))

from t_gigaam import (
    group_sentences_into_blocks,
    format_blocks_with_timestamps,
    MIN_PAUSE_SEC,
    MAX_BLOCK_DURATION_SEC,
    SentenceWithTimestamp
)

def test_grouping():
    """Тестирует группировку предложений"""

    # Создаём тестовые предложения с временными метками используя Pydantic модели
    test_sentences = [
        SentenceWithTimestamp(text="Первое предложение.", start=0.0, end=5.0),
        SentenceWithTimestamp(text="Второе предложение.", start=5.0, end=10.0),
        SentenceWithTimestamp(text="Третье предложение.", start=10.0, end=60.0),  # 50 сек длится

        # После большой паузы (60+ сек) должен начаться новый блок
        SentenceWithTimestamp(text="Четвёртое предложение после паузы.", start=125.0, end=130.0),
        SentenceWithTimestamp(text="Пятое предложение.", start=130.0, end=135.0),

        # Длинный блок для теста разбиения по длительности (120+ сек)
        SentenceWithTimestamp(text="Шестое предложение.", start=135.0, end=140.0),
        SentenceWithTimestamp(text="Седьмое предложение.", start=140.0, end=200.0),  # 60 сек
        SentenceWithTimestamp(text="Восьмое предложение.", start=200.0, end=260.0),  # ещё 60 сек - блок > 120 сек

        # Должен быть разрыв по длительности
        SentenceWithTimestamp(text="Девятое предложение в новом блоке.", start=260.0, end=265.0),
    ]

    print("=" * 80)
    print("ТЕСТ ГРУППИРОВКИ ПРЕДЛОЖЕНИЙ")
    print("=" * 80)
    print(f"\nПараметры:")
    print(f"  MIN_PAUSE_SEC = {MIN_PAUSE_SEC}")
    print(f"  MAX_BLOCK_DURATION_SEC = {MAX_BLOCK_DURATION_SEC}")

    print(f"\nВсего предложений: {len(test_sentences)}")
    print("\nПредложения:")
    for i, sent in enumerate(test_sentences, 1):
        print(f"  {i}. [{sent.start:.1f}s - {sent.end:.1f}s] {sent.text}")

    # Группируем
    blocks = group_sentences_into_blocks(
        test_sentences,
        MIN_PAUSE_SEC,
        MAX_BLOCK_DURATION_SEC
    )

    print(f"\n{'=' * 80}")
    print(f"РЕЗУЛЬТАТ: Создано блоков: {len(blocks)}")
    print(f"{'=' * 80}\n")

    # Форматируем и выводим
    formatted_text = format_blocks_with_timestamps(blocks)
    print(formatted_text)

    print(f"\n{'=' * 80}")
    print("ДЕТАЛИ БЛОКОВ:")
    print(f"{'=' * 80}\n")

    for i, block in enumerate(blocks, 1):
        print(f"Блок #{i}:")
        print(f"  Таймстамп: {block.start_sec:.1f}s")
        print(f"  Символов: {len(block.text)}")
        print(f"  Текст: {block.text[:100]}...")
        print()

if __name__ == "__main__":
    test_grouping()
