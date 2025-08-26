import os
import sys
import subprocess
import random
from pathlib import Path

VIDEO_EXTS = (".mkv", ".mp4", ".avi", ".mov", ".flv", ".wmv", ".ts", ".mpg")

def get_duration(file_path):
    """Возвращает длительность видео в секундах"""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration", "-of",
         "default=noprint_wrappers=1:nokey=1", file_path],
        capture_output=True, text=True
    )
    try:
        return float(result.stdout.strip())
    except:
        return None

def create_thumbnail(src, dst):
    """Создаёт скриншот из видео"""
    duration = get_duration(src)
    if duration is None or duration <= 0:
        print(f"Пропускаем (не удалось определить длительность): {src}")
        return

    # Случайная точка в районе второй трети
    t_start = duration / 3
    t_end = duration * 2 / 3
    timestamp = random.uniform(t_start, t_end)

    os.makedirs(os.path.dirname(dst), exist_ok=True)

    cmd = [
        "ffmpeg", "-y", "-ss", str(timestamp),
        "-i", src,
        "-frames:v", "1",
        "-q:v", "2",  # качество JPEG (1-31, меньше = лучше)
        dst
    ]
    print(f">>> Создаём миниатюру: {src} -> {dst} @ {timestamp:.2f}s")
    subprocess.run(cmd)

def process_recursive(src_dir, dst_dir):
    src_dir = Path(src_dir).resolve()
    dst_dir = Path(dst_dir).resolve()

    for root, _, files in os.walk(src_dir):
        for f in files:
            if f.lower().endswith(VIDEO_EXTS):
                src = Path(root) / f
                rel = src.relative_to(src_dir)
                thumb_name = rel.with_suffix(".jpg")
                dst = dst_dir / thumb_name
                create_thumbnail(str(src), str(dst))

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Использование: python thumbnail_videos.py <src_dir> <dst_dir>")
        sys.exit(1)
    process_recursive(sys.argv[1], sys.argv[2])
