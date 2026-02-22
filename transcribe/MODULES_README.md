# Модульная структура транскрибации

**Дата создания:** 2026-02-22

## 📋 Обзор

Общая функциональность из скриптов `t_gigaam*.py` вынесена в отдельные модули для повторного использования и упрощения поддержки.

## 📦 Модули

### 1. `transcribe_dto.py` - Data Transfer Objects

**Назначение:** Все Pydantic модели для типизированной работы с данными

**Модели:**

#### Аудио обработка:
- `ChunkInfo` - информация об аудио-чанке (начало, путь)
- `ChunkBoundary` - границы чанка (начало, конец)
- `AudioChunkingResult` - результат нарезки (список чанков, длительность)

#### Транскрипция:
- `SentenceWithTimestamp` - предложение с временными метками и спикером
- `TextBlock` - блок текста с таймстампом и спикером

#### Диаризация:
- `SpeakerSegment` - сегмент одного спикера

**Пример использования:**
```python
from transcribe_dto import ChunkInfo, AudioChunkingResult

chunk = ChunkInfo(start_sec=0.0, file_path=Path("chunk.wav"))
result = AudioChunkingResult(chunks=[chunk], total_duration_sec=120.0)
```

---

### 2. `transcribe_config.py` - Конфигурация

**Назначение:** Все константы, параметры и настройки

**Секции:**

#### FFmpeg Configuration:
- `FFMPEG_BIN` - путь к FFmpeg
- `setup_ffmpeg_path()` - настройка PATH

#### Audio Processing:
- `CHUNK_SEC = 20.0` - длина чанка
- `OVERLAP_SEC = 1.0` - перекрытие
- `AUDIO_SAMPLE_RATE = 16000` - частота дискретизации
- `AUDIO_CHANNELS = 1` - mono
- `AUDIO_CODEC = "pcm_s16le"` - кодек

#### Text Segmentation:
- `MIN_PAUSE_SEC = 60` - минимальная пауза для нового блока
- `MAX_BLOCK_DURATION_SEC = 600` - максимальная длительность блока
- `MIN_BLOCK_DURATION_SEC = 120` - для двухминутных блоков

#### Speaker Diarization:
- `MIN_SEGMENT_DURATION_SEC = 0.8` - минимальная длительность сегмента
- `DEFAULT_NUM_SPEAKERS = 2` - количество спикеров по умолчанию

#### File Extensions:
- `AUDIO_EXTENSIONS` - поддерживаемые аудио форматы
- `VIDEO_EXTENSIONS` - поддерживаемые видео форматы
- `MEDIA_EXTENSIONS` - все медиа форматы

#### Funny Names:
- `FUNNY_SPEAKER_NAMES` - список прикольных имен для спикеров

#### Models:
- `GIGAAM_MODEL_NAME` - имя модели GigaAM
- `GIGAAM_DEFAULT_REVISION` - ревизия по умолчанию
- `PYANNOTE_MODEL_NAME` - имя модели pyannote

**Пример использования:**
```python
from transcribe_config import CHUNK_SEC, setup_ffmpeg_path

setup_ffmpeg_path()
print(f"Длина чанка: {CHUNK_SEC} сек")
```

---

### 3. `transcribe_utils.py` - Утилиты

**Назначение:** Общие функции для работы с FFmpeg, файлами, временем, текстом

**Категории функций:**

#### Время и форматирование:
- `seconds_to_hhmmss(total_sec)` - конвертация секунд в [HH:mm:ss]

#### Проверка типов файлов:
- `is_video_file(file_path)` - проверка видео
- `is_audio_file(file_path)` - проверка аудио
- `is_media_file(file_path)` - проверка медиафайла

#### FFmpeg: Получение информации:
- `get_audio_duration_from_ffmpeg(input_path)` - длительность аудио/видео

#### FFmpeg: Извлечение аудио:
- `extract_audio_from_video(video_path, output_dir)` - извлечь аудио из видео
- `extract_audio_chunk_with_ffmpeg(input_path, start_sec, end_sec, output_path)` - извлечь чанк

#### Нарезка аудио на чанки:
- `calculate_chunk_boundaries(total_sec, chunk_sec, overlap_sec)` - вычислить границы
- `generate_chunk_filename(start_sec, tmp_dir)` - сгенерировать имя файла
- `create_temp_directory_for_chunks()` - создать временную директорию
- `cut_audio_to_chunks(input_path, chunk_sec, overlap_sec)` - нарезать на чанки

#### Работа с текстом:
- `split_into_sentences(text)` - разбить текст на предложения
- `calculate_text_similarity(text1, text2)` - вычислить похожесть (Jaccard)

#### Маппинг спикеров:
- `create_speaker_name_mapping(speaker_ids)` - создать маппинг на прикольные имена

#### Очистка:
- `cleanup_chunk_files(chunks)` - удалить временные чанки
- `cleanup_temp_file(file_path)` - удалить временный файл

#### Сохранение:
- `save_transcription_to_file(full_text, input_path, revision, suffix)` - сохранить результат

**Пример использования:**
```python
from transcribe_utils import cut_audio_to_chunks, is_video_file

if is_video_file(Path("video.mp4")):
    audio_path = extract_audio_from_video(video_path, tmp_dir)

result = cut_audio_to_chunks("audio.wav", chunk_sec=20.0)
print(f"Создано {len(result.chunks)} чанков")
```

---

## 🔄 Миграция существующих скриптов

### Шаг 1: Добавить импорты

```python
from transcribe_config import (
    setup_ffmpeg_path,
    CHUNK_SEC,
    OVERLAP_SEC,
    MIN_PAUSE_SEC,
    MAX_BLOCK_DURATION_SEC
)
from transcribe_dto import (
    ChunkInfo,
    AudioChunkingResult,
    SentenceWithTimestamp,
    TextBlock
)
from transcribe_utils import (
    seconds_to_hhmmss,
    is_video_file,
    extract_audio_from_video,
    cut_audio_to_chunks,
    split_into_sentences,
    cleanup_chunk_files,
    save_transcription_to_file
)
```

### Шаг 2: Заменить локальные реализации на вызовы модулей

**Было:**
```python
def seconds_to_hhmmss(total_sec: float) -> str:
    td = timedelta(seconds=int(total_sec))
    return f"[{str(td).zfill(8)}]"
```

**Стало:**
```python
from transcribe_utils import seconds_to_hhmmss
```

### Шаг 3: Использовать DTO вместо dict/tuple

**Было:**
```python
chunks = [(0.0, Path("chunk.wav"))]
```

**Стало:**
```python
chunks = [ChunkInfo(start_sec=0.0, file_path=Path("chunk.wav"))]
```

---

## 📊 Преимущества новой структуры

### ✅ Повторное использование кода
- Нет дублирования функций между скриптами
- Единая реализация = меньше багов

### ✅ Типизация
- Pydantic модели для валидации данных
- IDE автодополнение и проверка типов
- Меньше ошибок времени выполнения

### ✅ Централизованная конфигурация
- Все настройки в одном месте
- Легко изменить параметры для всех скриптов

### ✅ Упрощенное тестирование
- Каждый модуль можно тестировать отдельно
- Легко создавать mock-объекты

### ✅ Читаемость
- Меньше кода в основных скриптах
- Фокус на бизнес-логике, а не на утилитах

---

## 📝 Следующие шаги

1. **Рефакторинг существующих скриптов:**
   - [ ] `t_gigaam.py` - использовать новые модули
   - [ ] `t_gigaam_1.py` - использовать новые модули
   - [ ] `t_gigaam_2.py` - использовать новые модули
   - [ ] `t_directory.py` - обновить импорты

2. **Тестирование:**
   - [ ] Создать unit-тесты для `transcribe_utils.py`
   - [ ] Создать тесты для DTO моделей
   - [ ] Интеграционные тесты

3. **Документация:**
   - [ ] Добавить docstrings для всех публичных функций
   - [ ] Создать примеры использования
   - [ ] API документация

---

## 🎯 Использование

```python
# Минимальный пример использования модулей

from pathlib import Path
from transcribe_config import setup_ffmpeg_path, CHUNK_SEC
from transcribe_utils import (
    is_video_file,
    extract_audio_from_video,
    cut_audio_to_chunks,
    cleanup_chunk_files
)

# Настройка
setup_ffmpeg_path()

# Обработка файла
input_file = Path("video.mp4")
tmp_dir = Path(tempfile.mkdtemp())

# Извлечь аудио если видео
if is_video_file(input_file):
    audio_path = extract_audio_from_video(input_file, tmp_dir)
else:
    audio_path = input_file

# Нарезать на чанки
result = cut_audio_to_chunks(str(audio_path), CHUNK_SEC)
print(f"Создано чанков: {len(result.chunks)}")
print(f"Длительность: {result.total_duration_sec} сек")

# Обработка чанков...
# ...

# Очистка
cleanup_chunk_files(result.chunks)
```

---

## 📚 См. также

- `CLAUDE.md` - общие правила разработки
- `20260222.001_fix_t_gigaam_2.md` - история исправлений
- Документация Pydantic: https://docs.pydantic.dev/

