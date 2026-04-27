"""
Навигация по логам - показывает последовательность запросов/ответов.

Использование:
    python show_logs.py              # последние 20 файлов
    python show_logs.py --all        # все файлы
    python show_logs.py --last 50    # последние 50
    python show_logs.py --tail       # автоматическое обновление (как tail -f)
"""
import argparse
from pathlib import Path
from datetime import datetime
import time
import sys

LOGS_DIR = Path(__file__).parent / "logs"


def list_log_files(all_files=False, last_n=20):
    """Список лог-файлов с нумерацией."""
    log_files = sorted(LOGS_DIR.glob("[0-9][0-9][0-9]_*.log"))

    if not log_files:
        print("Нет файлов логов")
        return []

    if not all_files:
        log_files = log_files[-last_n:]

    return log_files


def show_logs(all_files=False, last_n=20, show_content=False):
    """Показать список логов."""
    log_files = list_log_files(all_files, last_n)

    if not log_files:
        return

    print(f"\n{'='*80}")
    print(f"Лог-файлы в {LOGS_DIR}")
    print(f"Показано: {len(log_files)} файлов")
    print(f"{'='*80}\n")

    for log_file in log_files:
        # Парсим имя файла: 001_llm_plan_request.log
        name = log_file.name
        parts = name.replace(".log", "").split("_", 1)
        num = parts[0]
        rest = parts[1] if len(parts) > 1 else ""

        # Размер файла
        size = log_file.stat().st_size
        size_kb = size / 1024
        size_str = f"{size_kb:.1f}KB" if size_kb < 1024 else f"{size_kb/1024:.1f}MB"

        # Время модификации
        mtime = datetime.fromtimestamp(log_file.stat().st_mtime)
        time_str = mtime.strftime("%H:%M:%S")

        # Тип файла
        if "llm" in rest:
            icon = "🤖"
            type_str = "LLM"
        elif "tool" in rest:
            icon = "🔧"
            type_str = "TOOL"
        else:
            icon = "📝"
            type_str = "LOG"

        # Request или Response
        if "request" in rest:
            direction = "→"
        elif "response" in rest:
            direction = "←"
        else:
            direction = " "

        print(f"{num} {icon} {direction} {type_str:4} {rest:40} {time_str}  {size_str:>8}")

        if show_content:
            print(f"     {'-'*70}")
            # Показываем первые 5 строк
            with log_file.open("r", encoding="utf-8") as f:
                lines = f.readlines()[:5]
                for line in lines:
                    print(f"     {line.rstrip()}")
            if len(lines) > 5:
                print(f"     ...")
            print()


def tail_logs(interval=1):
    """Следить за новыми файлами (как tail -f)."""
    print(f"\n{'='*80}")
    print("🔍 Отслеживание новых логов (Ctrl+C для выхода)")
    print(f"{'='*80}\n")

    seen_files = set(LOGS_DIR.glob("[0-9][0-9][0-9]_*.log"))

    try:
        while True:
            current_files = set(LOGS_DIR.glob("[0-9][0-9][0-9]_*.log"))
            new_files = current_files - seen_files

            if new_files:
                for new_file in sorted(new_files):
                    # Парсим имя
                    name = new_file.name
                    parts = name.replace(".log", "").split("_", 1)
                    num = parts[0]
                    rest = parts[1] if len(parts) > 1 else ""

                    # Тип
                    if "llm" in rest:
                        icon = "🤖"
                    elif "tool" in rest:
                        icon = "🔧"
                    else:
                        icon = "📝"

                    # Direction
                    if "request" in rest:
                        direction = "→"
                    elif "response" in rest:
                        direction = "←"
                    else:
                        direction = " "

                    time_str = datetime.now().strftime("%H:%M:%S")
                    print(f"{time_str} {num} {icon} {direction} {rest}")

                seen_files = current_files

            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n\nОстановлено пользователем")


def show_file_content(file_num):
    """Показать содержимое конкретного файла."""
    pattern = f"{int(file_num):03d}_*.log"
    files = list(LOGS_DIR.glob(pattern))

    if not files:
        print(f"Файл {pattern} не найден")
        return

    for file in files:
        print(f"\n{'='*80}")
        print(f"Файл: {file.name}")
        print(f"{'='*80}\n")

        with file.open("r", encoding="utf-8") as f:
            print(f.read())


def main():
    parser = argparse.ArgumentParser(description="Навигация по логам RAG агента")
    parser.add_argument("--dir", type=str, help="Директория с логами (по умолчанию logs/)")
    parser.add_argument("--all", action="store_true", help="Показать все файлы")
    parser.add_argument("--last", type=int, default=20, help="Последние N файлов (по умолчанию 20)")
    parser.add_argument("--tail", action="store_true", help="Следить за новыми файлами")
    parser.add_argument("--content", action="store_true", help="Показать первые строки каждого файла")
    parser.add_argument("--show", type=int, help="Показать содержимое конкретного файла (номер)")
    
    args = parser.parse_args()
    
    # Устанавливаем директорию логов
    global LOGS_DIR
    if args.dir:
        LOGS_DIR = Path(args.dir)
    
    if args.show:
        show_file_content(args.show)
    elif args.tail:
        tail_logs()
    else:
        show_logs(all_files=args.all, last_n=args.last, show_content=args.content)


if __name__ == "__main__":
    main()

