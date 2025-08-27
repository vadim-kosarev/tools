import os
import subprocess
import sys
import argparse
from pathlib import Path

def get_codecs(file_path):
    """Определяет кодеки видео и аудио через ffprobe"""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=codec_name",
                "-of", "default=nokey=1:noprint_wrappers=1",
                file_path
            ],
            capture_output=True, text=True, check=True
        )
        vcodec = result.stdout.strip()

        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "a:0",
                "-show_entries", "stream=codec_name",
                "-of", "default=nokey=1:noprint_wrappers=1",
                file_path
            ],
            capture_output=True, text=True, check=True
        )
        acodec = result.stdout.strip()
        return vcodec, acodec
    except subprocess.CalledProcessError:
        return None, None


def convert_file(src, dst, cut_first=None):
    """Конвертирует один файл и ресайзит до 480p"""
    vcodec, acodec = get_codecs(src)
    os.makedirs(os.path.dirname(dst), exist_ok=True)

    # Базовые опции ffmpeg
    base_options = ["-y"]

    # Добавляем опцию для обрезки начала видео
    if cut_first is not None and cut_first > 0:
        base_options.extend(["-ss", str(cut_first)])

    base_options.extend(["-i", src])

    # Опции для ресайза видео до 480p с принудительно четными размерами
    # scale=w:h:force_original_aspect_ratio=decrease,pad=ceil(iw/2)*2:ceil(ih/2)*2
    scale_options = ["-vf", "scale='ceil(oh*a/2)*2:480'"]

    if vcodec == "h264" and acodec in ("aac", "mp3"):
        # Даже если формат подходит, нам всё равно нужно перекодировать для ресайза
        cmd = ["ffmpeg"] + base_options + scale_options + [
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            dst
        ]
        mode = "ресайз и перекодирование"
    else:
        # полное перекодирование
        cmd = ["ffmpeg"] + base_options + scale_options + [
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            dst
        ]
        mode = "перекодирование и ресайз"

    cut_info = f" (обрезка {cut_first} сек)" if cut_first else ""
    print(f">>> {mode}{cut_info}: {src} -> {dst}")
    subprocess.run(cmd)


def convert_recursive(src_dir, dst_dir, cut_first=None, extensions=None):
    """Рекурсивно обходит каталог и конвертирует видео"""
    if extensions is None:
        extensions = (".mkv", ".avi", ".mov", ".flv", ".wmv", ".mpg", ".ts", ".mp4")

    src_dir = Path(src_dir).resolve()
    dst_dir = Path(dst_dir).resolve()

    for root, _, files in os.walk(src_dir):
        for f in files:
            if f.lower().endswith(extensions):
                src = Path(root) / f
                rel = src.relative_to(src_dir)
                dst = dst_dir / rel.with_suffix(".mp4")
                convert_file(str(src), str(dst), cut_first)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Конвертирование видеофайлов в формат MP4')
    parser.add_argument('src_dir', help='Исходная директория с видеофайлами')
    parser.add_argument('dst_dir', help='Директория назначения для конвертированных файлов')
    parser.add_argument('--cut-first', type=float, help='Удалить первые N секунд из видео', default=None)

    args = parser.parse_args()

    convert_recursive(args.src_dir, args.dst_dir, args.cut_first)
