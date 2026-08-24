# all_good

Инструмент для нарезки повторяющейся фразы из сериала в один видеофайл.

Работает в два прохода, по аналогии с [`transcribe/`](../transcribe/README.md) (та же модель GigaAM-v3):

1. **Проход 1 (реализован)** — `scan_transcribe.py` один раз просматривает все серии, распознаёт речь
   и для каждого видеофайла сохраняет рядом текстовый файл с сегментами речи и их таймстампами.
2. **Проход 2 (ещё не реализован)** — по текстовым файлам из прохода 1 находятся все места,
   где встречается заданная фраза, из исходных видео вырезаются соответствующие куски
   и склеиваются в один итоговый файл.

## Проход 1 — `scan_transcribe.py`

**Особенности:**
- Границы сегментов — по реальным паузам речи (VAD, `pyannote/segmentation-3.0`), а не по
  фиксированной нарезке по времени. Это даёт заметно более точные таймстампы начала/конца
  фразы, чем при пропорциональной оценке по длине текста внутри чанка.
- Таймстампы — с точностью до миллисекунд (`HH:MM:SS.mmm`).
- Идемпотентно и безопасно для повторного запуска: если рядом с видео уже есть файл-результат
  непустого размера — файл пропускается (можно прервать и продолжить позже, `--force` — пересчитать).
- Результат сохраняется рядом с исходным видео (сеть/сетевой диск — не в репозитории).

**Использование:**
```powershell
# Показывает HELP (без аргументов — справка, без ошибки)
python scan_transcribe.py

# Просмотреть все серии из обеих папок сериала на сетевом диске
python scan_transcribe.py "\\luigi\S-Downloads\From.S03.WEB-DLRip.LF" "\\luigi\S-Downloads\From.S04.WEB-DLRip.LF"

# С явным устройством и более короткими сегментами (точнее таймстампы, медленнее)
python scan_transcribe.py "\\luigi\S-Downloads\From.S03.WEB-DLRip.LF" --device cuda --max-segment-sec 8

# Пересчитать уже готовые файлы
python scan_transcribe.py "\\luigi\S-Downloads\From.S03.WEB-DLRip.LF" --force
```

**Выход:** рядом с каждым `<серия>.avi` создаётся `<серия>.gigaam-<revision>.segments.txt`:
```
[00:00:12.340 - 00:00:18.900] Текст первого сегмента речи.
[00:00:19.100 - 00:00:26.450] Текст следующего сегмента речи.
```

## Конфигурация

`.env` (копия — `.env.example`):

| Переменная | Назначение |
|---|---|
| `HF_TOKEN` | Токен Hugging Face для `pyannote/segmentation-3.0` (VAD внутри GigaAM `transcribe_longform`) |
| `FFMPEG_BIN` | Путь к DLL FFmpeg (Windows) |
| `scan_transcribe.config.revision` | Ревизия GigaAM-v3 (`e2e_rnnt` по умолчанию, `e2e_ctc`) |
| `scan_transcribe.config.device` | `auto` / `cpu` / `cuda` |
| `scan_transcribe.config.max-segment-sec` | Макс. длительность сегмента VAD — меньше значение → точнее таймстампы, но больше проходов ASR |
| `scan_transcribe.config.min-segment-sec` | Мин. длительность сегмента VAD |

CLI-флаг всегда переопределяет значение из `.env`.

**Важно:** на Hugging Face нужно принять условия доступа к модели
[`pyannote/segmentation-3.0`](https://huggingface.co/pyannote/segmentation-3.0) под аккаунтом,
чьим токеном указан `HF_TOKEN`.

## Установка

```powershell
cd all_good
pip install -r requirements.txt
```

## Структура

| Файл | Назначение |
|---|---|
| `scan_transcribe.py` | Проход 1: распознавание речи всех видео с таймстампами по VAD-сегментам |
| `all_good_config.py` | Константы, пути, значения из `.env` |
| `all_good_dto.py` | Pydantic-модели (`SegmentTranscript`, `ScanReport`, `ProcessingStatus`) |
| `all_good_utils.py` | ffmpeg, поиск видеофайлов, статус обработки, логирование |
