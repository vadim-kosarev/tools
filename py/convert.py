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
    """Конвертирует один файл"""
    vcodec, acodec = get_codecs(src)
    os.makedirs(os.path.dirname(dst), exist_ok=True)

    # Базовые опции ffmpeg
    base_options = ["-y"]

    # Добавляем опцию для обрезки начала видео
    if cut_first is not None and cut_first > 0:
        base_options.extend(["-ss", str(cut_first)])

    base_options.extend(["-i", src])

    # Проверяем, можно ли просто скопировать видео без перекодирования
    if vcodec == "h264" and acodec in ("aac", "mp3"):
        # контейнерный ремап без перекодирования
        cmd = ["ffmpeg"] + base_options + ["-c:v", "copy", "-c:a", "aac", "-b:a", "128k", dst]
        mode = "копирование"

        print(f">>> {mode}{' (обрезка ' + str(cut_first) + ' сек)' if cut_first else ''}: {src} -> {dst}")
        subprocess.run(cmd)
        return

    # Проверяем поддержку NVIDIA GPU для перекодирования
    use_gpu = False
    try:
        # Проверяем наличие кодека h264_nvenc
        check_gpu = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True
        )

        if "h264_nvenc" in check_gpu.stdout:
            # Попробуем закодировать маленький тестовый фрагмент для проверки совместимости
            test_cmd = [
                "ffmpeg", "-y", "-f", "lavfi", "-i", "color=black:s=256x256:d=1",
                "-c:v", "h264_nvenc", "-preset", "fast", "-profile:v", "main",
                "-f", "null", "-"
            ]
            test_process = subprocess.run(test_cmd, capture_output=True, text=True)
            use_gpu = test_process.returncode == 0

            if not use_gpu:
                print("ВНИМАНИЕ: NVIDIA GPU обнаружен, но тест кодирования не прошел. Используем CPU.")
    except Exception as e:
        print(f"ВНИМАНИЕ: Ошибка при проверке GPU: {e}. Используем CPU.")
        use_gpu = False

    # Перекодирование с GPU или CPU
    if use_gpu:
        # Используем более совместимые настройки для NVENC
        cmd = ["ffmpeg"] + base_options + [
            "-c:v", "h264_nvenc",
            "-preset", "fast",  # вместо p4, который может быть несовместим
            "-profile:v", "main",  # более совместимый профиль
            "-b:v", "5M",
            "-c:a", "aac", "-b:a", "128k",
            dst
        ]
        mode = "перекодирование (GPU NVIDIA)"
    else:
        # Используем CPU
        cmd = ["ffmpeg"] + base_options + [
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            dst
        ]
        mode = "перекодирование (CPU)"

    # Запускаем процесс конвертации
    print(f">>> {mode}{' (обрезка ' + str(cut_first) + ' сек)' if cut_first else ''}: {src} -> {dst}")
    result = subprocess.run(cmd, capture_output=True, text=True)

    # Если процесс с GPU завершился с ошибкой, повторяем на CPU
    if use_gpu and result.returncode != 0:
        print(f"ВНИМАНИЕ: Ошибка при использовании GPU. Код ошибки: {result.returncode}")
        print(f"Сообщение: {result.stderr}")
        print("Повторная попытка конвертации с использованием CPU...")

        cmd = ["ffmpeg"] + base_options + [
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            dst
        ]
        mode = "перекодирование (CPU)"
        print(f">>> {mode}{' (обрезка ' + str(cut_first) + ' сек)' if cut_first else ''}: {src} -> {dst}")
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
