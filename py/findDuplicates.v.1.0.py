#!/usr/bin/env python3
import os
import hashlib
from collections import defaultdict
import argparse
import time
import sys
import csv
from tqdm import tqdm
import sqlite3
from PIL import Image, ExifTags
import math

def get_db_path():
    """Return path to SQLite DB file next to the script."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(script_dir, 'file_hashes.sqlite3')

def init_db():
    """Initialize SQLite DB and create table if not exists."""
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS file_hashes (
            path TEXT PRIMARY KEY,
            size INTEGER,
            mtime REAL,
            hash TEXT,
            hash_alg TEXT,
            exif TEXT
        )
    ''')
    conn.commit()
    return conn

def extract_exif_sorted(filepath):
    """Извлечь exif-данные, отсортированные по ключу, вернуть как строку."""
    try:
        image = Image.open(filepath)
        exif_data = image._getexif()
        if not exif_data:
            return ""
        exif = {}
        for tag, value in exif_data.items():
            tag_name = ExifTags.TAGS.get(tag, tag)
            exif[tag_name] = value
        # Сортируем по ключу и сериализуем в строку
        items = sorted(exif.items())
        return str(items)
    except Exception:
        return ""


def exif_has_more_than_date(exif_str):
    """Return True if exif is not empty, contains at least one date field, and has fields other than date-related ones."""
    if not exif_str:
        return False
    try:
        safe_globals = {"nan": math.nan}
        exif_data = eval(exif_str, safe_globals)
        if not exif_data or not isinstance(exif_data, list):
            return False
        # List of EXIF tags related to date/time
        date_tags = {
            "DateTimeOriginal", "DateTime", "DateTimeDigitized",
            "CreateDate", "ModifyDate", "DateCreated", "TimeCreated"
        }
        has_date = False
        has_non_date = False
        for tag, _ in exif_data:
            if tag in date_tags:
                has_date = True
            else:
                has_non_date = True
        return has_date and has_non_date
    except Exception as e:
        print(f"Error parsing EXIF: {e}")
        return False

def get_cached_hash(conn, filepath, size, mtime, hash_alg, exif_str=None):
    """Get cached hash and exif from DB if file unchanged."""
    c = conn.cursor()
    c.execute('''
        SELECT hash, exif FROM file_hashes
        WHERE path=? AND size=? AND mtime=? AND hash_alg=?
    ''', (filepath, size, mtime, hash_alg))
    row = c.fetchone()
    if row:
        return row[0], row[1]
    return None, None

def set_cached_hash(conn, filepath, size, mtime, hash_val, hash_alg, exif_str):
    """Store hash and exif in DB."""
    c = conn.cursor()
    c.execute('''
        INSERT OR REPLACE INTO file_hashes (path, size, mtime, hash, hash_alg, exif)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (filepath, size, mtime, hash_val, hash_alg, exif_str))
    conn.commit()

def calculate_file_hash(filepath, hash_algorithm='md5', buffer_size=65536, db_conn=None, tqdm_instance=None):
    """Calculate file hash and exif, with info about source (db/file)."""
    file_size = os.path.getsize(filepath)
    mtime = os.path.getmtime(filepath)
    if tqdm_instance:
        tqdm_instance.write(f"   {filepath}")
    if db_conn:
        cached, cached_exif = get_cached_hash(db_conn, filepath, file_size, mtime, hash_algorithm)
        if cached:
            # Если exif отсутствует в БД, вычислить и обновить
            if cached_exif is None:
                exif_str = extract_exif_sorted(filepath)
                set_cached_hash(db_conn, filepath, file_size, mtime, cached, hash_algorithm, exif_str)
                return cached, exif_str
            return cached, cached_exif
    # Только если нет кэша, извлекаем exif
    exif_str = extract_exif_sorted(filepath)
    hash_func = hashlib.new(hash_algorithm)
    bytes_to_read = min(buffer_size, file_size)
    with open(filepath, 'rb') as f:
        data = f.read(bytes_to_read)
        if data:
            hash_func.update(data)
    hash_val = hash_func.hexdigest()
    if db_conn:
        set_cached_hash(db_conn, filepath, file_size, mtime, hash_val, hash_algorithm, exif_str)
    return hash_val, exif_str

def is_media_file(filepath):
    """Return True if file is an image or video by extension."""
    media_exts = {
        # images
        '.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tiff', '.tif', '.webp', '.heic', '.heif', '.svg',
        # videos
        '.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.webm', '.mpg', '.mpeg', '.3gp', '.mts', '.m2ts', '.ts', '.vob', '.m4v'
    }
    ext = os.path.splitext(filepath)[1].lower()
    return ext in media_exts

def find_duplicate_files(directories, hash_alg='md5', min_size=0, max_size=0, skip_hidden=True, db_conn=None):
    """Find duplicate files across multiple directories with filtering options and SQLite caching."""
    hashes = defaultdict(list)
    exif_previews = {}  # filepath -> exif_preview
    exif_full_map = {}  # filepath -> exif_str
    total_files = 0

    # Count total files for progress bar
    for directory in directories:
        for root, dirs, files in os.walk(directory):
            if skip_hidden:
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                files = [f for f in files if not f.startswith('.')]
            total_files += sum(1 for f in files if is_media_file(os.path.join(root, f)))

    with tqdm(total=total_files, desc="Scanning files") as pbar:
        for directory in directories:
            for root, dirs, files in os.walk(directory):
                if skip_hidden:
                    dirs[:] = [d for d in dirs if not d.startswith('.')]
                    files = [f for f in files if not f.startswith('.')]

                for filename in files:
                    filepath = os.path.join(root, filename)
                    if not is_media_file(filepath):
                        continue
                    pbar.update(1)

                    try:
                        file_size = os.path.getsize(filepath)
                        if min_size > 0 and file_size < min_size:
                            continue
                        if max_size > 0 and file_size > max_size:
                            continue

                        file_hash, exif_str = calculate_file_hash(filepath, hash_alg, db_conn=db_conn, tqdm_instance=pbar)
                        exif_preview = exif_str[:60] + ("..." if exif_str and len(exif_str) > 60 else "")
                        exif_previews[filepath] = exif_preview
                        exif_full_map[filepath] = exif_str

                        hashes[file_hash].append(filepath)
                    except (IOError, OSError) as e:
                        print(f"\nWarning: Could not process {filepath} - {str(e)}", file=sys.stderr)
                        continue

    return {h: f for h, f in hashes.items() if len(f) > 1}, exif_previews

def export_to_csv(duplicates, output_file):
    """Export duplicate groups to CSV with one row per group and UTF-8 BOM header."""
    try:
        with open(output_file, 'w', newline='', encoding='utf-8-sig') as csvfile:
            writer = csv.writer(csvfile)
            # Write header
            writer.writerow([
                'Group ID',
                'Hash',
                'File Count',
                'Size (bytes)',
                'File Paths'
            ])

            for group_num, (hash_val, files) in enumerate(duplicates.items(), 1):
                file_size = os.path.getsize(files[0])
                file_paths = '|'.join(files)
                writer.writerow([
                    group_num,
                    hash_val,
                    len(files),
                    file_size,
                    file_paths
                ])
        print(f"\nResults exported to {output_file}")
    except IOError as e:
        print(f"\nError writing to {output_file}: {str(e)}", file=sys.stderr)

def print_duplicates(duplicates_and_exif):
    """Display duplicate files information in console."""
    if isinstance(duplicates_and_exif, tuple):
        duplicates, exif_previews = duplicates_and_exif
    else:
        duplicates = duplicates_and_exif
        exif_previews = {}

    if not duplicates:
        print("No duplicate files found.")
        return

    total_groups = len(duplicates)
    total_dupes = sum(len(files)-1 for files in duplicates.values())
    saved_space = sum(os.path.getsize(files[0])*(len(files)-1) for files in duplicates.values())

    print(f"\nFound {total_groups} duplicate groups ({total_dupes} redundant files)")
    print(f"Potential savings: {saved_space/1024/1024:.2f} MB")
    print("-" * 60)

    for group_num, (hash_val, files) in enumerate(duplicates.items(), 1):
        exif_preview = exif_previews.get(files[0], "")
        short_hash = hash_val[:64]
        file_size = os.path.getsize(files[0])
        print(f"\nGroup #{group_num} (hash: {short_hash}, exif: {exif_preview}, size: {file_size} bytes):")
        for idx, filepath in enumerate(files, 1):
            try:
                file_hash, _ = get_cached_hash(
                    init_db(), filepath, os.path.getsize(filepath), os.path.getmtime(filepath), 'md5'
                )
                if not file_hash:
                    file_hash, _ = calculate_file_hash(filepath, 'md5')
            except Exception:
                file_hash = "?"
            print(f"  {idx}.\t{filepath}")

def confirm_deletion(total_files, saved_space):
    """Prompt user for deletion confirmation."""
    print(f"\nAbout to delete {total_files} files")
    print(f"This will free {saved_space/1024/1024:.2f} MB")
    response = input("Are you sure? (y/N): ").strip().lower()
    return response == 'y'

def delete_duplicates(duplicates, keep_first=True, keep_last=False, keep_largest=False, dry_run=False, confirm=True, exif_previews=None):
    """Handle duplicate file deletion with safety checks."""
    files_to_delete = []
    saved_space = 0

    for files in duplicates.values():
        if keep_largest:
            sizes = [os.path.getsize(f) for f in files]
            largest_idx = sizes.index(max(sizes))
            targets = [f for i, f in enumerate(files) if i != largest_idx]
        elif keep_last:
            targets = files[:-1]
        else:  # keep_first
            targets = files[1:]
        files_to_delete.extend(targets)
        if files:
            saved_space += os.path.getsize(files[0]) * len(targets)

    if not files_to_delete:
        print("No files to delete")
        return 0, 0

    if dry_run:
        print("\nDry run: would delete these files:")
        for idx, filepath in enumerate(files_to_delete, 1):
            print(f"  {idx}. {filepath}")
        return len(files_to_delete), saved_space

    if confirm and not confirm_deletion(len(files_to_delete), saved_space):
        print("Deletion cancelled")
        return 0, 0

    deleted_count = 0
    errors = 0

    for filepath in tqdm(files_to_delete, desc="Deleting duplicates"):
        try:
            os.remove(filepath)
            deleted_count += 1
        except OSError as e:
            print(f"\nError deleting {filepath}: {str(e)}", file=sys.stderr)
            errors += 1

    return deleted_count, saved_space

def main():
    parser = argparse.ArgumentParser(
        description='Find duplicate files with CSV export capability across multiple directories',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        'directories',
        nargs='+',  # Accept one or more directories
        help='Directories to search for duplicates'
    )
    parser.add_argument(
        '--hash',
        default='md5',
        choices=['md5', 'sha1', 'sha256', 'sha512'],
        help='Hashing algorithm to use'
    )
    parser.add_argument(
        '--delete',
        action='store_true',
        help='Delete all duplicates (keeping first file in each group)'
    )
    parser.add_argument(
        '--keep-last',
        action='store_true',
        help='When deleting, keep last file instead of first'
    )
    parser.add_argument(
        '--keep-largest',
        action='store_true',
        help='When deleting, keep largest file instead of first'
    )
    parser.add_argument(
        '--min-size',
        type=int,
        default=0,
        help='Minimum file size to check (in bytes)'
    )
    parser.add_argument(
        '--max-size',
        type=int,
        default=0,
        help='Maximum file size to check (in bytes)'
    )
    parser.add_argument(
        '--skip-hidden',
        action='store_true',
        help='Skip hidden files and directories'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Simulate deletion without actually removing files'
    )
    parser.add_argument(
        '--no-confirm',
        action='store_true',
        help='Skip confirmation before deletion'
    )
    parser.add_argument(
        '--csv',
        metavar='FILE',
        help='Export results to CSV file'
    )
    # Удалён параметр --use-exif

    args = parser.parse_args()

    # Only one --keep-* flag allowed
    keep_flags = [args.keep_last, args.keep_largest]
    if sum(keep_flags) > 1:
        print("Error: Only one of --keep-last or --keep-largest can be specified.", file=sys.stderr)
        sys.exit(1)

    # Validate directories
    valid_directories = []
    for directory in args.directories:
        if not os.path.isdir(directory):
            print(f"Error: {directory} is not a valid directory", file=sys.stderr)
        else:
            valid_directories.append(directory)

    if not valid_directories:
        print("Error: No valid directories provided", file=sys.stderr)
        sys.exit(1)

    print(f"\nSearching for duplicates in: {', '.join(valid_directories)}")
    print(f"Using {args.hash.upper()} hashing algorithm")
    if args.min_size > 0:
        print(f"Ignoring files smaller than {args.min_size} bytes")
    if args.max_size > 0:
        print(f"Ignoring files larger than {args.max_size} bytes")
    if args.skip_hidden:
        print("Skipping hidden files/directories")
    print("-" * 60)

    db_conn = init_db()
    try:
        start_time = time.time()
        duplicates_and_exif = find_duplicate_files(
            valid_directories,
            args.hash,
            args.min_size,
            args.max_size,
            args.skip_hidden,
            db_conn=db_conn
        )
        if isinstance(duplicates_and_exif, tuple):
            duplicates, exif_previews = duplicates_and_exif
        else:
            duplicates = duplicates_and_exif
            exif_previews = {}

        duration = time.time() - start_time

        if args.csv:
            export_to_csv(duplicates, args.csv)

        print_duplicates((duplicates, exif_previews))
        print(f"\nScan completed in {duration:.2f} seconds.")

        if args.delete or args.dry_run:
            deleted_count, saved_space = delete_duplicates(
                duplicates,
                keep_first=not args.keep_last and not args.keep_largest,
                keep_last=args.keep_last,
                keep_largest=args.keep_largest,
                dry_run=args.dry_run,
                confirm=not args.no_confirm,
                exif_previews=exif_previews
            )

            if args.dry_run:
                print(f"\nDry run: would delete {deleted_count} files")
            else:
                print(f"\nDeleted {deleted_count} files")
                print(f"Freed {saved_space/1024/1024:.2f} MB")
    finally:
        db_conn.close()

if __name__ == '__main__':
    main()
