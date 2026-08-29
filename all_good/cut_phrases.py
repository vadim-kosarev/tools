#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Второй проход по сериалу: ищет заданные фразы в файлах-результатах прохода 1
(scan_transcribe.py, *.gigaam-<revision>.segments.txt) и вырезает из исходных
видео все совпадающие куски. Для каждой фразы — своя папка: отдельные короткие
клипы на каждое совпадение и один склеенный файл со всеми повторами подряд.
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
    from all_good_config import CUT_PHRASES_OUTPUT_DIR, CUT_PHRASES_PADDING_MS

    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=_FORMATTER_CLASS,
    )
    parser.add_argument(
        "phrases", nargs="*",
        help='Фразы для поиска, например "всё в порядке" "всё хорошо"',
    )
    parser.add_argument(
        "--folders", nargs="+", default=None,
        help=_d(None, "Папки с *.segments.txt и видео из прохода 1 (обязательно, если заданы фразы)"),
    )
    parser.add_argument("--output-dir", default=CUT_PHRASES_OUTPUT_DIR,
                        help=_d(CUT_PHRASES_OUTPUT_DIR, "Куда складывать нарезку"))
    parser.add_argument("--padding-ms", type=int, default=CUT_PHRASES_PADDING_MS,
                        help=_d(CUT_PHRASES_PADDING_MS, "Запас по времени до/после сегмента (мс)"))
    parser.add_argument(
        "--edge-only", action="store_true",
        help=_d(False, "Резать только совпадения в начале или в конце сегмента речи, пропускать "
                "середину (в середине вырезался бы весь сегмент целиком, а это лишний посторонний текст)"),
    )
    parser.add_argument(
        "--isolate-silence", action="store_true",
        help=_d(False, "Для совпадений на краю сегмента (см. --edge-only) ищет паузу с внутренней "
                "стороны фразы — там, где сегмент продолжается другим текстом — и режет по ней, вместо "
                "всего сегмента. Ищет только внутри уже известных границ сегмента, наружу не выходит. "
                "Если фразы в середине сегмента (без --edge-only) или паузы внутри нет — не сужает"),
    )
    parser.add_argument(
        "--best-of-sec", type=float, default=None,
        help=_d(None, "Не вся нарезка, а только лучшие совпадения на ~N секунд суммарно (не строго "
                "— последний клип может слегка превысить порог). 'Лучшесть': сначала совпадения, "
                "реально изолированные тишиной (--isolate-silence), затем — чем короче итоговый "
                "клип, тем выше. Включает --isolate-silence автоматически"),
    )
    parser.add_argument("--no-recursive", dest="recursive", action="store_false",
                        help=_d(True, "Не заходить в подпапки (по умолчанию — заходит рекурсивно)"))
    parser.add_argument("--debug", action="store_true", help=_d(False, "DEBUG-уровень логирования"))
    parser.add_argument("--env", default=None,
                        help=_d(None, "Путь к .env файлу (по умолчанию — .env рядом со скриптом)"))
    return parser


if __name__ == "__main__":
    _parser = _build_parser()
    _args = _parser.parse_args()
    if not _args.phrases:
        _parser.print_help()
        sys.exit(0)
    if not _args.folders:
        _parser.error("нужно указать --folders с папками, где лежат *.segments.txt и видео")

# ============================================================================
# Тяжёлые импорты — только если есть что обрабатывать
# ============================================================================

import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import NamedTuple

if _args.env:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=_args.env, override=True)

from all_good_config import (
    SILENCE_TRIM_MIN_SEC,
    SILENCE_TRIM_NOISE_DB,
    SILENCE_TRIM_PROBE_SEC,
    setup_ffmpeg_path,
)
from all_good_dto import PhraseMatch
from all_good_utils import (
    collect_segments_files,
    detect_speech_spans,
    find_source_video,
    is_edge_match,
    normalize_for_match,
    parse_segments_file,
    safe_filename,
    seconds_to_timestamp,
    setup_logging,
    trim_leading_silence,
)

setup_ffmpeg_path()
logger = setup_logging(Path(__file__).stem, "DEBUG" if _args.debug else "INFO")

# Единый fps для всех клипов (см. cut_clip) — исходники разных сезонов по-разному округляют 23.976fps.
CLIP_TARGET_FPS = "24000/1001"

# Проба тишины на совпадение — это 1-2 вызова ffmpeg (сеть/диск), не CPU — параллелим потоками.
ISOLATE_WORKERS = 6


def find_matches(segments_files: list[Path], phrases: list[str], edge_only: bool) -> list[PhraseMatch]:
    """Ищет сегменты, подходящие под ЛЮБУЮ из фраз (ИЛИ) — регистронезависимо, без пунктуации.
    Каждый сегмент даёт не больше одного совпадения, даже если подходит нескольким фразам сразу."""
    normalized_phrases = {phrase: normalize_for_match(phrase) for phrase in phrases}
    matches: list[PhraseMatch] = []
    skipped_middle = 0
    for seg_file in segments_files:
        video_path = find_source_video(seg_file)
        if video_path is None:
            logger.warning(f"Не найдено исходное видео для {seg_file.name}, пропускаем")
            continue
        for seg in parse_segments_file(seg_file):
            norm_text = normalize_for_match(seg.text)
            candidates = [phrase for phrase, norm_phrase in normalized_phrases.items() if norm_phrase in norm_text]
            if not candidates:
                continue
            if edge_only:
                candidates = [phrase for phrase in candidates if is_edge_match(seg.text, phrase)]
                if not candidates:
                    skipped_middle += 1
                    continue
            matches.append(PhraseMatch(
                video_path=video_path, phrase=candidates[0],
                start_sec=seg.start_sec, end_sec=seg.end_sec, text=seg.text,
            ))
    if edge_only and skipped_middle:
        logger.info(f"Пропущено совпадений в середине сегмента (--edge-only): {skipped_middle}")
    return matches


def isolate_edge_phrase(match: PhraseMatch) -> tuple[float, float] | None:
    """Для совпадения на краю сегмента ищет реальную паузу с внутренней стороны фразы — строго
    внутри уже известных границ сегмента (start_sec/end_sec), наружу не выходит, поэтому не может
    зацепить что-то за пределами уже проверенного сегмента. Возвращает None, если фраза не на
    краю сегмента, или паузы внутри нет (речь идёт без пауз до следующего предложения)."""
    norm_text = normalize_for_match(match.text)
    norm_phrase = normalize_for_match(match.phrase)
    at_start = norm_text.startswith(norm_phrase)
    at_end = norm_text.endswith(norm_phrase)

    if at_start and at_end:
        # Сегмент — это и есть вся фраза целиком, сужать по паузам внутри нечего.
        cut_start = trim_leading_silence(
            match.video_path, match.start_sec, SILENCE_TRIM_PROBE_SEC, SILENCE_TRIM_NOISE_DB, SILENCE_TRIM_MIN_SEC)
        return cut_start, match.end_sec

    if at_start:
        cut_start = trim_leading_silence(
            match.video_path, match.start_sec, SILENCE_TRIM_PROBE_SEC, SILENCE_TRIM_NOISE_DB, SILENCE_TRIM_MIN_SEC)
        spans = detect_speech_spans(
            match.video_path, cut_start, match.end_sec - cut_start, SILENCE_TRIM_NOISE_DB, SILENCE_TRIM_MIN_SEC)
        if not spans:
            return None
        span_end = spans[0][1]
        if span_end >= match.end_sec - 0.05:
            return None  # паузы внутри сегмента нет — вся оставшаяся часть речи идёт без разрыва
        return cut_start, span_end

    if at_end:
        spans = detect_speech_spans(
            match.video_path, match.start_sec, match.end_sec - match.start_sec,
            SILENCE_TRIM_NOISE_DB, SILENCE_TRIM_MIN_SEC)
        if not spans:
            return None
        span_start = spans[-1][0]
        if span_start <= match.start_sec + 0.05:
            return None
        return span_start, match.end_sec

    return None  # фраза в середине сегмента — искать паузу внутри неё самой не имеет смысла


class ResolvedCut(NamedTuple):
    """Одно совпадение с уже вычисленным окном вырезки."""
    match: PhraseMatch
    cut_start: float
    cut_end: float
    isolated: bool


def resolve_cut(match: PhraseMatch, isolate_silence: bool) -> ResolvedCut:
    """Вычисляет окно вырезки для одного совпадения: изоляция тишиной, если включена и удалась,
    иначе весь сегмент с подрезкой ведущей тишины."""
    span = isolate_edge_phrase(match) if isolate_silence else None
    if span is not None:
        return ResolvedCut(match, span[0], span[1], True)

    # Подрезка ведущей тишины: VAD-сегмент иногда захватывает соседний беззвучный спан, для
    # которого ASR не выдал текста, и реальная речь начинается чуть позже.
    cut_start = trim_leading_silence(
        match.video_path, match.start_sec, SILENCE_TRIM_PROBE_SEC, SILENCE_TRIM_NOISE_DB, SILENCE_TRIM_MIN_SEC)
    return ResolvedCut(match, cut_start, match.end_sec, False)


def resolve_all(matches: list[PhraseMatch], isolate_silence: bool) -> list[ResolvedCut]:
    """Вычисляет окно вырезки для каждого совпадения. С isolate_silence=True это 1-2 вызова ffmpeg
    на совпадение (сеть/диск, не CPU) — считаем параллельно и логируем прогресс, а не молчим."""
    if not isolate_silence or len(matches) <= 1:
        return [resolve_cut(match, isolate_silence) for match in matches]

    logger.info(f"Анализ пауз вокруг фразы (--isolate-silence): {len(matches)} совпадений...")
    order = {id(match): idx for idx, match in enumerate(matches)}
    resolved = []
    with ThreadPoolExecutor(max_workers=ISOLATE_WORKERS) as executor:
        futures = {executor.submit(resolve_cut, match, isolate_silence): match for match in matches}
        for i, future in enumerate(as_completed(futures), 1):
            rc = future.result()
            resolved.append(rc)
            logger.info(
                f"  [{i}/{len(matches)}] {rc.match.video_path.name} [{seconds_to_timestamp(rc.match.start_sec)}] "
                f"{'изолировано' if rc.isolated else 'без изоляции'}"
            )
    # Потоки завершаются в произвольном порядке — возвращаем исходный (хронологический) порядок.
    resolved.sort(key=lambda rc: order[id(rc.match)])
    return resolved


def select_best_of(resolved: list[ResolvedCut], target_sec: float, padding_sec: float) -> list[ResolvedCut]:
    """Жадно берёт совпадения с лучшей оценкой (изолированные, затем короче — лучше), пока не
    наберётся ~target_sec суммарной длительности (включает клип, который пересекает порог, —
    поэтому 'около', не строго). Итог — в хронологическом порядке для финальной сборки."""
    def clip_duration(rc: ResolvedCut) -> float:
        return (rc.cut_end - rc.cut_start) + 2 * padding_sec

    def score(rc: ResolvedCut) -> float:
        return (100.0 if rc.isolated else 0.0) - clip_duration(rc)

    ranked = sorted(resolved, key=score, reverse=True)
    selected: list[ResolvedCut] = []
    total = 0.0
    for rc in ranked:
        if total >= target_sec:
            break
        selected.append(rc)
        total += clip_duration(rc)
    selected.sort(key=lambda rc: (str(rc.match.video_path), rc.match.start_sec))
    return selected


def cut_clip(video_path: Path, start_sec: float, end_sec: float, padding_sec: float, out_path: Path) -> bool:
    """Вырезает и перекодирует один клип из исходного видео."""
    start = max(0.0, start_sec - padding_sec)
    duration = (end_sec - start_sec) + 2 * padding_sec
    cmd = [
        "ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", str(video_path), "-t", f"{duration:.3f}",
        # Единый fps/CFR на каждом клипе: у части серий (замечено на 4 сезоне) fps в контейнере
        # записан как 2997/125 вместо 24000/1001 у остальных — формально то же значение (23.976),
        # но разное рациональное представление ломает склейку между сериями с разным fps.
        "-r", CLIP_TARGET_FPS, "-fps_mode", "cfr",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-ar", "48000", "-b:a", "160k",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
        logger.error(f"Не удалось вырезать клип из {video_path.name} [{start:.2f}с]: {result.stderr[-500:]}")
        return False
    return True


def concat_clips(clip_paths: list[Path], out_path: Path) -> bool:
    """Склеивает список клипов в один файл. Перекодирует (не -c copy): при stream copy у клипов,
    независимо перекодированных cut_clip, на стыках рвутся временные метки — это даёт подвисание
    видео при непрерывном звуке в проигрывателях. Перекодирование даёт монотонные метки по всему
    файлу."""
    list_file = out_path.parent / f"{out_path.stem}.concat_list.txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for clip_path in clip_paths:
            f.write(f"file '{clip_path.resolve().as_posix()}'\n")
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-b:a", "160k",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    list_file.unlink(missing_ok=True)
    if result.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
        logger.error(f"Не удалось склеить {out_path.name}: {result.stderr[-500:]}")
        return False
    return True


def process_phrase(label: str, resolved: list[ResolvedCut], output_dir: Path, padding_sec: float) -> None:
    """Вырезает клипы по уже вычисленным окнам (см. resolve_all) и склеивает их в один файл."""
    phrase_dir = output_dir / safe_filename(label)
    clips_dir = phrase_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f'"{label}": {len(resolved)} совпадений в нарезке')
    clip_paths = []
    for i, rc in enumerate(resolved, 1):
        match = rc.match
        if rc.isolated:
            logger.debug(f"  Изолировано тишиной: [{seconds_to_timestamp(rc.cut_start)} - {seconds_to_timestamp(rc.cut_end)}]")

        clip_name = f"{match.video_path.stem}_{seconds_to_timestamp(rc.cut_start).replace(':', '-')}.mp4"
        clip_path = clips_dir / clip_name
        logger.info(f"  [{i}/{len(resolved)}] {match.video_path.name} [{seconds_to_timestamp(rc.cut_start)}] ({match.phrase}) {match.text[:50]}")
        if cut_clip(match.video_path, rc.cut_start, rc.cut_end, padding_sec, clip_path):
            clip_paths.append(clip_path)

    if not clip_paths:
        logger.warning(f'"{label}": ни одного клипа не вырезалось')
        return

    merged_path = phrase_dir / f"{safe_filename(label)}.mp4"
    if concat_clips(clip_paths, merged_path):
        logger.info(f'"{label}": готово -> {merged_path} ({len(clip_paths)} клипов)')


def main() -> int:
    segments_files = collect_segments_files(_args.folders, _args.recursive)
    if not segments_files:
        logger.error("Не найдено ни одного файла *.segments.txt — сначала прогони scan_transcribe.py")
        return 1
    logger.info(f"Найдено файлов с результатами распознавания: {len(segments_files)}")

    matches = find_matches(segments_files, _args.phrases, _args.edge_only)
    if not matches:
        logger.warning("Совпадений не найдено ни для одной из фраз")
        return 1

    output_dir = Path(_args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    padding_sec = _args.padding_ms / 1000

    # --best-of-sec без изоляции бессмысленен (нечем ранжировать) — включаем её автоматически.
    isolate_silence = _args.isolate_silence or _args.best_of_sec is not None
    resolved = resolve_all(matches, isolate_silence)

    if _args.best_of_sec is not None:
        before = len(resolved)
        resolved = select_best_of(resolved, _args.best_of_sec, padding_sec)
        logger.info(f"Лучшее из {before}: выбрано {len(resolved)} совпадений (цель ~{_args.best_of_sec:.0f} сек)")

    # Фразы трактуются как ИЛИ: один общий результат на все совпадения сразу, а не по папке на фразу.
    label = " или ".join(_args.phrases)
    process_phrase(label, resolved, output_dir, padding_sec)

    return 0


if __name__ == "__main__":
    sys.exit(main())
