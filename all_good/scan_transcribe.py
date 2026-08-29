#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Первый проход по сериалу: распознаёт речь во всех видеофайлах и для каждого
сохраняет рядом текстовый файл с сегментами речи и их таймстампами
(границы сегментов — по реальным паузам речи, VAD, а не по фиксированной
нарезке). Эти файлы служат материалом для второго прохода — поиска повторов
фразы и нарезки видео.
"""

import argparse
import sys
from pathlib import Path

try:
    from rich_argparse import RichHelpFormatter
    _FORMATTER_CLASS = RichHelpFormatter
except ImportError:
    _FORMATTER_CLASS = argparse.HelpFormatter


def _d(default: object, text: str) -> str:
    """Приписывает значение по умолчанию в квадратных скобках в начало текста help."""
    return f"[default: {default}] {text}"


def _build_parser() -> argparse.ArgumentParser:
    from all_good_config import (
        GIGAAM_AVAILABLE_REVISIONS,
        SCAN_TRANSCRIBE_DEVICE,
        SCAN_TRANSCRIBE_MAX_SEGMENT_SEC,
        SCAN_TRANSCRIBE_MIN_SEGMENT_SEC,
        SCAN_TRANSCRIBE_REVISION,
    )

    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=_FORMATTER_CLASS,
    )
    parser.add_argument(
        "folders", nargs="*",
        help="Папки или видеофайлы для сканирования (например, путь на \\\\luigi\\S-Downloads)",
    )
    parser.add_argument("--revision", default=SCAN_TRANSCRIBE_REVISION, choices=GIGAAM_AVAILABLE_REVISIONS,
                        help=_d(SCAN_TRANSCRIBE_REVISION, "Ревизия модели GigaAM-v3"))
    parser.add_argument("--device", default=SCAN_TRANSCRIBE_DEVICE, choices=["auto", "cpu", "cuda"],
                        help=_d(SCAN_TRANSCRIBE_DEVICE, "Устройство для вычислений"))
    parser.add_argument("--max-segment-sec", type=float, default=SCAN_TRANSCRIBE_MAX_SEGMENT_SEC,
                        help=_d(SCAN_TRANSCRIBE_MAX_SEGMENT_SEC,
                                "Макс. длительность сегмента VAD (сек) — меньше значение -> точнее таймстампы"))
    parser.add_argument("--min-segment-sec", type=float, default=SCAN_TRANSCRIBE_MIN_SEGMENT_SEC,
                        help=_d(SCAN_TRANSCRIBE_MIN_SEGMENT_SEC, "Мин. длительность сегмента VAD (сек)"))
    parser.add_argument("--force", action="store_true",
                        help=_d(False, "Переобработать файлы с уже готовым результатом"))
    parser.add_argument("--no-recursive", dest="recursive", action="store_false",
                        help=_d(True, "Не заходить в подпапки (по умолчанию — заходит рекурсивно)"))
    parser.add_argument("--debug", action="store_true", help=_d(False, "DEBUG-уровень логирования"))
    parser.add_argument("--env", default=None,
                        help=_d(None, "Путь к .env файлу (по умолчанию — .env рядом со скриптом)"))
    return parser


if __name__ == "__main__":
    _parser = _build_parser()
    _args = _parser.parse_args()
    if not _args.folders:
        _parser.print_help()
        sys.exit(0)
    if _args.max_segment_sec >= 25.0:
        _parser.error("--max-segment-sec должен быть < 25 (лимит GigaAM на распознавание одного сегмента)")

# ============================================================================
# Тяжёлые импорты — только если есть что обрабатывать
# ============================================================================

import importlib
import tempfile
from concurrent.futures import Future, ThreadPoolExecutor

import torch
import torchaudio
from transformers import AutoModel

if _args.env:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=_args.env, override=True)

from all_good_config import AUDIO_SAMPLE_RATE, GIGAAM_MODEL_NAME, HF_TOKEN, setup_ffmpeg_path
from all_good_dto import ProcessingStatus, ScanReport, SegmentTranscript
from all_good_utils import (
    cleanup_temp_file,
    collect_video_files,
    extract_audio_from_video,
    finalize_segments_file,
    format_segment_line,
    get_partial_output_path,
    get_processing_status,
    setup_logging,
)

setup_ffmpeg_path()
logger = setup_logging(Path(__file__).stem, "DEBUG" if _args.debug else "INFO")


def determine_device(device_arg: str) -> str:
    if device_arg == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    return device_arg


def prepare_audio(video_path: Path, sample_rate: int) -> tuple[Path, Path]:
    """Извлекает звук из видео во временную папку. Выполняется в фоновом потоке
    (диск/сеть/CPU), пока GPU занят распознаванием предыдущей серии."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="all_good_"))
    audio_path = extract_audio_from_video(video_path, tmp_dir, sample_rate)
    return tmp_dir, audio_path


def transcribe_audio_streaming(model: AutoModel, audio_path: Path, out_path: Path) -> int:
    """VAD-сегментация + распознавание сегмент за сегментом, с записью каждой
    строки в файл сразу после распознавания. Возвращает число сегментов."""
    gigaam_module = importlib.import_module(type(model.model).__module__)
    device = next(model.parameters()).device

    # strict_limit_duration — жёсткий потолок длины сегмента внутри VAD-нарезки.
    # Держим его заметно ниже 25 сек (LONGFORM_THRESHOLD в GigaAM) — иначе при
    # долгой непрерывной речи без пауз VAD может отдать сегмент 25-30 сек, а
    # короткая model.transcribe() на одном сегменте падает с "Too long wav file".
    segments, boundaries = gigaam_module.segment_audio_file(
        str(audio_path), AUDIO_SAMPLE_RATE, device=device,
        max_duration=_args.max_segment_sec, min_duration=_args.min_segment_sec,
        strict_limit_duration=20.0,
    )

    seg_tmp_dir = Path(tempfile.mkdtemp(prefix="all_good_seg_"))
    count = 0
    try:
        with open(out_path, "w", encoding="utf-8") as out_file:
            for waveform, (start_sec, end_sec) in zip(segments, boundaries):
                seg_path = seg_tmp_dir / "segment.wav"
                torchaudio.save(str(seg_path), waveform.unsqueeze(0).cpu(), AUDIO_SAMPLE_RATE)
                with torch.inference_mode():
                    text = model.transcribe(str(seg_path)).strip()
                cleanup_temp_file(seg_path)
                if not text:
                    continue
                line = format_segment_line(SegmentTranscript(start_sec=start_sec, end_sec=end_sec, text=text))
                out_file.write(line + "\n")
                out_file.flush()
                count += 1
    finally:
        try:
            seg_tmp_dir.rmdir()
        except OSError:
            pass
    return count


def process_video(model: AutoModel, video_path: Path, audio_path: Path, revision: str) -> bool:
    """Распознаёт уже извлечённый звук одного видеофайла. Возвращает True при успехе."""
    partial_path = get_partial_output_path(video_path, revision)
    try:
        count = transcribe_audio_streaming(model, audio_path, partial_path)
    except Exception as e:
        logger.error(f"Ошибка распознавания {video_path.name}: {type(e).__name__} -> {e}", exc_info=_args.debug)
        cleanup_temp_file(partial_path)
        return False

    output_path = finalize_segments_file(partial_path, video_path, revision)
    logger.info(f"Готово: {video_path.name} -> {output_path.name} ({count} сегментов)")
    return True


def main() -> int:
    if not HF_TOKEN:
        logger.warning(
            "HF_TOKEN не задан в .env — загрузка pyannote/segmentation-3.0 (VAD) может завершиться ошибкой доступа"
        )

    device = determine_device(_args.device)
    logger.info(f"Устройство: {device.upper()}, ревизия GigaAM: {_args.revision}")

    video_files = collect_video_files(_args.folders, _args.recursive)
    report = ScanReport(total_found=len(video_files))
    if not video_files:
        logger.error("Видеофайлы не найдены")
        return 1

    logger.info(f"Найдено видеофайлов: {len(video_files)}")

    files_to_process = []
    for video_path in video_files:
        status = get_processing_status(video_path, _args.revision)
        if status == ProcessingStatus.SUCCESS and not _args.force:
            logger.info(f"Пропускаем (уже готово): {video_path.name}")
            report.skipped_success += 1
            continue
        files_to_process.append(video_path)

    if not files_to_process:
        logger.info("Обрабатывать нечего — все файлы уже готовы")
        return 0

    logger.info(f"Загружаем GigaAM-v3 ({_args.revision}) на {device.upper()}...")
    model = AutoModel.from_pretrained(GIGAAM_MODEL_NAME, revision=_args.revision, trust_remote_code=True)
    model.to(device)
    model.eval()
    logger.info("Модель загружена")

    # Пока GPU распознаёт текущую серию, в фоновом потоке уже режется звук
    # следующей (ffmpeg — это диск/сеть/CPU, с GPU не конкурирует).
    executor = ThreadPoolExecutor(max_workers=1)
    extraction: Future = executor.submit(prepare_audio, files_to_process[0], AUDIO_SAMPLE_RATE)

    for i, video_path in enumerate(files_to_process):
        next_extraction = None
        try:
            tmp_dir, audio_path = extraction.result()
        except Exception as e:
            logger.error(f"Ошибка извлечения звука {video_path.name}: {type(e).__name__} -> {e}", exc_info=_args.debug)
            report.failed += 1
            if i + 1 < len(files_to_process):
                extraction = executor.submit(prepare_audio, files_to_process[i + 1], AUDIO_SAMPLE_RATE)
            continue

        if i + 1 < len(files_to_process):
            next_extraction = executor.submit(prepare_audio, files_to_process[i + 1], AUDIO_SAMPLE_RATE)

        logger.info(f"Обрабатываем: {video_path}")
        try:
            if process_video(model, video_path, audio_path, _args.revision):
                report.processed += 1
            else:
                report.failed += 1
        finally:
            cleanup_temp_file(audio_path)
            try:
                tmp_dir.rmdir()
            except OSError:
                pass

        if next_extraction is not None:
            extraction = next_extraction

    executor.shutdown(wait=True)

    logger.info(
        f"\n{'=' * 80}\n"
        f"РЕЗУЛЬТАТЫ ПРОХОДА:\n"
        f"  Всего найдено: {report.total_found}\n"
        f"  Обработано: {report.processed}\n"
        f"  Пропущено (уже готово): {report.skipped_success}\n"
        f"  Ошибки: {report.failed}\n"
        f"{'=' * 80}"
    )
    return 0 if report.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
