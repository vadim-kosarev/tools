# Frigate

## Запуск / Обновление

Рабочая директория: `frigate`

```powershell
cd C:\dev\github.com\vadim-kosarev\tools\frigate
```

**Обновить и запустить:**
```powershell
docker compose pull
docker compose up -d
```

**Просто запустить (без обновления):**
```powershell
docker compose up -d
```

## Backup

### PostgreSQL (события, метаданные)

Настроен автоматический бэкап через контейнер `postgres-backup` (образ `postgres:18`,
скрипт `postgres-backup/backup.sh`):
- `pg_dumpall` стримится напрямую в `gzip -9` (без промежуточного `.sql` на диске)
- Расписание: раз в сутки (`while true; do sh /backup.sh; sleep 86400; done`)
- Хранение: 7 дней (`KEEP_DAYS`), старые архивы удаляются автоматически
- Архивы `frigate-YYYYMMDD.sql.gz` пишутся в `K:\frigate\backups\postgres`

Запуск: `docker compose up -d postgres-backup`

### SQLite (frigate.db)

Внутренняя БД Frigate (`./config/frigate.db`) — не бэкапится автоматически.
Frigate создаёт `backup.db` при обновлениях, но это не полноценный бэкап.

### Media (записи камер)

Хранятся в `K:\frigate` с встроенной ротацией:
- motion: 14 дней (per-camera) / 30 дней (global)
- alerts/detections: 180 дней (per-camera) / 30 дней (global)

Внешний бэкап медиа не настроен.

## Детектор

Используется дефолтный CPU-детектор (TFLite SSD MobileNet). GPU (RTX 3060 Ti) занята под
ffmpeg hwaccel (декодирование потоков), semantic search (jina embeddings) и face recognition.

ONNX-детектор с TensorRT **не использовать** — компиляция engine при старте зависает на 20+ минут
и не завершается. Модели лежат в `/config/onnx/`, конфиг закомментирован в `config.yaml`.

### TODO

- [ ] Бэкап `frigate.db` (cron-копия в `K:\frigate\backups\sqlite`)
- [ ] Вынос дампов на внешнее хранилище (NAS, S3)
