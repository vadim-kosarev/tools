# 🎙️ Transcribe — Система транскрибации GigaAM-v3

Полнофункциональная система для транскрибации аудио и видеофайлов с использованием модели **GigaAM-v3** от SberDevices.

## 📋 Содержание

- [Скрипты](#скрипты)
- [Модули](#модули)
- [Быстрый старт](#быстрый-старт)
- [Конфигурация](#конфигурация)

---

## 🚀 Скрипты

### `t_gigaam_simple.py` — ⭐ Простая транскрибация (РЕКОМЕНДУЕТСЯ)

**Назначение:** Преобразование аудио/видео в текст без анализа спикеров.

**Особенности:**
- ✅ **Простая** — минимум опций, работает "из коробки"
- ✅ **Быстрая** — полная транскрипция без диаризации
- ✅ **Поддержка видео** — автоматически извлекает аудио
- ✅ **Поддержка чанков** — обрабатывает длинные файлы
- ✅ **Использует модули** — переиспользует config, dto, utils

**Использование:**
```powershell
python t_gigaam_simple.py "audio.wav"
python t_gigaam_simple.py "video.mp4" --revision e2e_rnnt
python t_gigaam_simple.py "audio.wav" --device cuda
```

**Выход:**
```
audio.gigaam-e2e_ctc-2026-05-02_12-34-56.txt  # Текст всего файла
```

**Когда использовать:**
- 🟢 Нужен просто текст аудио
- 🟢 Однодикторные файлы
- 🟢 Быстрая обработка важнее деталей

---

### `t_gigaam_blocks.py` — Транскрибация с блоками

**Назначение:** Разделение текста на блоки (абзацы) по паузам в речи.

**Особенности:**
- Блоки текста — каждый блок = отдельное высказывание (разделено паузой)
- Временные метки — начало каждого блока [HH:mm:ss]
- Без диаризации — спикеры не определяются
- Нарезка по паузам — минимальная пауза между высказываниями: 10 секунд
- Полная схема — использует все Pydantic модели из DTO

**Параметры нарезки:**
- Минимальная пауза: 60 секунд (пауза больше этого значения = начало нового блока)

**Использование:**
```powershell
python t_gigaam_blocks.py "audio.wav"
python t_gigaam_blocks.py "video.mp4" --revision e2e_ctc
```

**Выход:**
```
[00:00:00] Текст первого высказывания...
[01:23:45] Текст второго высказывания...
[02:04:20] Текст третьего высказывания...
```

**Когда использовать:**
- Нужно разделить текст на отдельные высказывания по паузам
- Важны временные позиции начала каждого высказывания
- Без информации о том, кто говорит

---

### `t_gigaam_speakers.py` — Полная транскрибация с диаризацией

**Назначение:** Полный анализ: кто говорит, когда, что говорит.

**Особенности:**
- ✅ **Диаризация спикеров** — определение кто говорит (Пикачу, Бублик, и т.д.)
- ✅ **Блоки по спикерам** — раздельные блоки для каждого говорящего
- ✅ **Полная информация:**
  - Время начала каждого блока
  - Имя спикера (или "Неизвестный")
  - Текст высказывания
- ✅ **Использует pyannote.audio** — продвинутая диаризация
- ✅ **Обработка длинных файлов** — нарезает на чанки, потом склеивает

**Использование:**
```powershell
python t_gigaam_speakers.py "audio.wav"
python t_gigaam_speakers.py "video.mp4" --revision e2e_rnnt
python t_gigaam_speakers.py "audio.wav" --device cuda --num-speakers 2
python t_gigaam_speakers.py "podcast.mp3" --num-speakers 3
```

**Выход:**
```
[00:00:00] ПИКАЧУ: Привет, это первое высказывание
[00:05:23] БУБЛИК: Ответ второго спикера
[00:12:45] ПИКАЧУ: Продолжение первого спикера
```

**Когда использовать:**
- 🔴 Многодикторные файлы (подкасты, интервью, встречи)
- 🔴 Нужно знать, кто что говорил
- 🔴 Правильное время выполнения не критично

---

### `t_directory.py` — Пакетная обработка директорий

**Назначение:** Рекурсивная обработка всех медиафайлов в папке и подпапках.

**Особенности:**
- ✅ **Рекурсивный обход** — ищет медиафайлы во всех подпапках
- ✅ **Фильтрация по статусу:**
  - 🔄 Не обработанные (`NOT_ATTEMPTED`) — в очередь
  - ✅ Успешно обработанные (`SUCCESS`) — пропускаются
  - ❌ Ошибка (`FAILED`) — пускаются повторно или пропускаются
- ✅ **Выбор скрипта транскрибации:**
  - `t_gigaam_simple.py` (по умолчанию) — быстро
  - `t_gigaam_blocks.py` — с блоками
  - `t_gigaam.py` — с диаризацией
- ✅ **Параллельная обработка** (опционально)
- ✅ **Отчёт о прогрессе** — статистика обработки

**Использование:**
```powershell
# Обработать всю папку простой транскрибацией (рекурсивный обход - по умолчанию)
python t_directory.py "D:\Podcasts"

# Только файлы в текущей папке (БЕЗ подпапок)
python t_directory.py "D:\Podcasts" --no-recursive

# С полной диаризацией спикеров (рекурсивный обход)
python t_directory.py "D:\Podcasts" --script t_gigaam_speakers.py --device cuda

# С блоками, только текущую папку
python t_directory.py "D:\Podcasts" --script t_gigaam_blocks.py --device cuda --no-recursive

# С диаризацией и параметрами для вложенного скрипта
python t_directory.py "D:\Podcasts" --script t_gigaam_speakers.py --device cuda --num-speakers 3

# Конкретная ревизия модели
python t_directory.py "D:\Podcasts" --revision e2e_rnnt

# Пересчитать ошибки (переобработать все, только текущая папка)
python t_directory.py "D:\Podcasts" --force --device cuda --no-recursive
```

**Примечание**: 
- По умолчанию используется **рекурсивный обход** (ищет файлы во всех подпапках)
- Используйте `--no-recursive` чтобы искать только в текущей директории
- Все параметры после `--script`, `--revision`, `--device`, `--force`, `--recursive` будут пробросаны в вложенный скрипт

**Выход:**
```
📊 РЕЗУЛЬТАТЫ ОБРАБОТКИ:
├─ Всего файлов найдено: 45
├─ Успешно обработано: 40 ✅
├─ Ошибки при обработке: 3 ❌
└─ Не обработано: 2 ⏸️

Детали:
- audio_01.wav: SUCCESS ✅
- audio_02.mp4: SUCCESS ✅
- audio_03.wav: FAILED ❌ (причина: ...)
```

**Когда использовать:**
- 🔵 Нужно обработать много файлов
- 🔵 Организованы в папках
- 🔵 Нужно продолжить с того же момента

---

### `organize.py` — Организация файлов по датам

**Назначение:** Автоматическая организация медиафайлов в папки по датам из имён файлов.

**Особенности:**
- Рекурсивный обход — ищет файлы во всех подпапках
- Автоматическое извлечение даты — из имени файла
- Множество форматов дат: YYYY-MM-DD, YYYY_MM_DD, YYYYMMDD
- Валидация даты — проверка года и месяца
- Маркер запуска — требует файл .organize в текущей директории
- Автоматическое создание папок — формат YYYY-MM
- Вытаскивание файлов — собирает из подпапок в папки текущего каталога

**Использование:**
```powershell
# 1. Создать маркер-файл в папке (один раз)
touch .organize

# 2. Организовать файлы
python organize.py
```

**Когда использовать:**
- Нужно организовать много файлов по датам
- Файлы разбросаны по подпапкам
- Дата есть в имени файла

---

## 📦 Модули

### `transcribe_config.py` — Конфигурация

**Назначение:** Централизованные настройки, константы и пути для всей системы.

**Содержит:**
- 🔧 **Пути FFmpeg** — `setup_ffmpeg_path()`
- 🔧 **Расширения файлов** — `AUDIO_EXTENSIONS`, `VIDEO_EXTENSIONS`
- 🔧 **Параметры обработки:**
  - Длина чанка: `CHUNK_SEC = 20.0`
  - Перекрытие: `OVERLAP_SEC = 1.0`
  - Минимальная пауза: `MIN_PAUSE_SEC = 60`
  - Макс длина блока: `MAX_BLOCK_DURATION_SEC = 600`
- 🔧 **Модели:**
  - `GIGAAM_MODEL_NAME` — модель GigaAM
  - `GIGAAM_DEFAULT_REVISION` — ревизия по умолчанию
  - `PYANNOTE_MODEL_NAME` — модель диаризации
- 🔧 **Прикольные имена спикеров** — `FUNNY_SPEAKER_NAMES`

**Использование:**
```python
from transcribe_config import (
    setup_ffmpeg_path,
    CHUNK_SEC,
    GIGAAM_DEFAULT_REVISION
)

setup_ffmpeg_path()
print(f"Длина чанка: {CHUNK_SEC} сек")
print(f"Ревизия: {GIGAAM_DEFAULT_REVISION}")
```

---

### `transcribe_dto.py` — Data Transfer Objects

**Назначение:** Pydantic модели для типизации данных.

**Модели:**

| Модель | Назначение |
|--------|-----------|
| `ChunkInfo` | Информация об аудио-чанке (начало, путь) |
| `ChunkBoundary` | Границы чанка (начало, конец) |
| `AudioChunkingResult` | Результат нарезки (список чанков, длительность) |
| `SentenceWithTimestamp` | Предложение с временем и спикером |
| `TextBlock` | Блок текста с временной меткой |
| `SpeakerSegment` | Сегмент одного спикера |

**Использование:**
```python
from transcribe_dto import ChunkInfo, AudioChunkingResult

chunks = [
    ChunkInfo(start_sec=0.0, file_path=Path("chunk_00.wav")),
    ChunkInfo(start_sec=20.0, file_path=Path("chunk_01.wav")),
]
result = AudioChunkingResult(chunks=chunks, total_duration_sec=150.0)
```

---

### `transcribe_utils.py` — Утилиты

**Назначение:** Общие функции для всех скриптов.

**Категории:**

| Категория | Функции |
|-----------|---------|
| **Время** | `seconds_to_hhmmss()` |
| **Проверка типов** | `is_video_file()`, `is_audio_file()`, `is_media_file()` |
| **FFmpeg** | `get_audio_duration_from_ffmpeg()`, `extract_audio_from_video()` |
| **Нарезка** | `cut_audio_to_chunks()`, `calculate_chunk_boundaries()` |
| **Текст** | `split_into_sentences()`, `calculate_text_similarity()` |
| **Спикеры** | `create_speaker_name_mapping()` |
| **Очистка** | `cleanup_chunk_files()`, `cleanup_temp_file()` |
| **Сохранение** | `save_transcription_to_file()` |

**Использование:**
```python
from transcribe_utils import (
    is_video_file,
    extract_audio_from_video,
    cut_audio_to_chunks,
    cleanup_chunk_files
)

if is_video_file(Path("video.mp4")):
    audio = extract_audio_from_video(Path("video.mp4"), Path("/tmp"))

result = cut_audio_to_chunks(str(audio), chunk_sec=20.0)
print(f"Создано чанков: {len(result.chunks)}")

cleanup_chunk_files(result.chunks)
```

---

## 🚀 Быстрый старт

### 1️⃣ Установка зависимостей

```powershell
cd transcribe

# Активировать виртуальное окружение (если есть)
.\.venv\Scripts\Activate.ps1

# Установить зависимости
pip install -r requirements.txt
```

### ⚡ Оптимизация: быстрое вывода справки

```powershell
# ⚡ Выводится МГНОВЕННО - тяжелые библиотеки (torch, transformers, pyannote) НЕ загружаются!
python t_gigaam_speakers.py --help
```

**Как это работает:** Все тяжелые зависимости загружаются **ТОЛЬКО после парсинга аргументов**, поэтому с флагом `--help` или при ошибке параметров система отвечает мгновенно.

### 2️⃣ Настройка FFmpeg

```powershell
# Windows - установить в PATH или указать в .env
# Linux/Mac - обычно уже установлен

ffmpeg -version  # Проверка
```

### 3️⃣ Простая транскрибация

```powershell
# Быстро и просто (рекомендуется)
python t_gigaam_simple.py "audio.wav"

# Результат: audio.gigaam-e2e_ctc-2026-05-02_12-34-56.txt
```

### 4️⃣ С информацией о спикерах (для многодикторных)

```powershell
python t_gigaam.py "podcast.mp3" --device cuda

# Результат: podcast.gigaam-e2e_ctc-2026-05-02_12-34-56.txt
# Содержит: [00:00:00] СПИКЕР1: Текст...
```

### 5️⃣ Обработка папки

```powershell
python t_directory.py "D:\MediaFiles" --device cuda

# Обработает все медиафайлы рекурсивно
```

---

## ⚙️ Конфигурация

### `.env` файл

```env
# FFmpeg
FFMPEG_BIN=C:\Tools\ffmpeg-8.0.1-full_build-shared\bin

# Модели
GIGAAM_MODEL_NAME=model/gigaam-v3
GIGAAM_DEFAULT_REVISION=e2e_ctc

# Обработка
CHUNK_SEC=20.0
OVERLAP_SEC=1.0
MIN_PAUSE_SEC=60
MAX_BLOCK_DURATION_SEC=600

# Диаризация
MIN_SEGMENT_DURATION_SEC=0.8
DEFAULT_NUM_SPEAKERS=2

# Логирование
LOG_LEVEL=INFO
LOG_FORMAT=%(asctime)s - %(levelname)s - %(message)s
```

### Параметры командной строки

| Параметр | Значение | Описание |
|----------|----------|---------|
| `--revision` | `e2e_ctc` ⭐, `e2e_rnnt` | Ревизия модели GigaAM |
| `--device` | `auto` ⭐, `cuda`, `cpu` | Устройство GPU/CPU |
| `--num-speakers` | `2` ⭐, число | Количество спикеров (для `t_gigaam_speakers.py`) |
| `--force` | флаг | Пересчитать ошибки (для `t_directory.py`) |
| `--script` | имя | Скрипт для `t_directory.py` |
| `--recursive` / `--no-recursive` | флаги | Рекурсивный обход подпапок (по умолчанию: рекурсивный) |

---

## 📊 Сравнение скриптов

| Функция | `simple` | `blocks` | `speakers` | `directory` |
|---------|----------|---------|---------|------------|
| Транскрибация | ✅ | ✅ | ✅ | ✅ (через другие) |
| Блоки текста | ❌ | ✅ | ✅ | ✅ |
| Диаризация | ❌ | ❌ | ✅ | ✅ |
| Видео | ✅ | ✅ | ✅ | ✅ |
| Скорость | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐ |
| Сложность | ⭐ | ⭐⭐ | ⭐⭐⭐ | ⭐⭐ |
| Рекомендуется для | Просто текст | Структура | Подкасты/встречи | Пакетная обработка |

---

## 📚 Дополнительная документация

- **`MODULES_README.md`** — Подробное описание модулей
- **`transcribe_config.py`** — Все настройки в одном файле
- **`.env.example`** — Пример конфигурации

---

## 🎯 Примеры использования

### Пример 1: Быстрая транскрибация видео

```powershell
python t_gigaam_simple.py "interview.mp4"
```

### Пример 2: Подкаст с определением спикеров

```powershell
python t_gigaam_speakers.py "podcast.mp3" --device cuda --num-speakers 2
```

### Пример 3: Семинар с блоками

```powershell
python t_gigaam_blocks.py "seminar.wav"
```

### Пример 4: Обработка всей папки

```powershell
python t_directory.py "D:\Recordings" --script t_gigaam_speakers.py --device cuda
```

### Пример 5: Переобработка ошибок

```powershell
python t_directory.py "D:\Recordings" --force-failed
```

---

## ✅ Статус

- ✅ `t_gigaam_simple.py` — готов к использованию
- ✅ `t_gigaam_blocks.py` — готов к использованию
- ✅ `t_gigaam_speakers.py` — готов к использованию
- ✅ `t_directory.py` — готов к использованию
- ✅ `transcribe_config.py` — все настройки
- ✅ `transcribe_dto.py` — Pydantic модели
- ✅ `transcribe_utils.py` — утилиты

---

**Рекомендация:** Начните с `t_gigaam_simple.py` — он самый простой и быстрый! 🚀

