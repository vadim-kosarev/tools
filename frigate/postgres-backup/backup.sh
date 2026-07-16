#!/bin/sh
set -e

DATE=$(date +%Y%m%d)
BACKUP_DIR=/backups
ARCHIVE_NAME="frigate-$DATE.sql.gz"

echo "$(date -Iseconds) starting pg_dumpall backup -> $ARCHIVE_NAME"

pg_dumpall -h "$POSTGRES_HOST" -U "$POSTGRES_USER" | gzip -9 > "$BACKUP_DIR/$ARCHIVE_NAME"

find "$BACKUP_DIR" -name 'frigate-*.sql.gz' -mtime "+$KEEP_DAYS" -delete

echo "$(date -Iseconds) backup done: $(ls -la "$BACKUP_DIR/$ARCHIVE_NAME")"
