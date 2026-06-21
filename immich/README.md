# Immich

## Запуск / Обновление

Рабочая директория: `immich\docker`

```powershell
cd C:\dev\github.com\vadim-kosarev\tools\immich\docker
```

**Обновить и запустить:**
```powershell
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml build --pull
docker compose -f docker-compose.prod.yml up -d
```

- `pull` — скачивает свежие образы (redis, postgres, ML, etc.)
- `build --pull` — пересобирает кастомный immich-server (Dockerfile с UTF-8 патчем)
- `up -d` — пересоздаёт контейнеры с новыми образами

**Просто запустить (без обновления):**
```powershell
docker compose -f docker-compose.prod.yml up -d
```

## Backup

### Database (PostgreSQL)

Бэкап БД настроен и работает из коробки:
- Встроенный механизм Immich (`backupDatabase`) делает `pg_dump` по cron-расписанию
- Расписание настраивается в Admin UI: Administration -> Settings -> Backup Database -> `cronExpression`
- По умолчанию: раз в сутки в 02:00
- Дампы пишутся в контейнере в `/opt/backup`, на хосте маппится в `${UPLOAD_LOCATION}/photos/backups` (`K:\immich\upload\photos\backups`)

### Media files

Бэкап медиафайлов (`K:\immich\upload\photos`) - не настроен.

### Копирование дампов на внешний диск

Скрипт `backup-db.ps1` копирует последний дамп из `K:\immich\upload\photos\backups` в `X:\Data\backups\immich`.
- Расписание: еженедельно, воскресенье в 04:00 (через cron-win, см. `crontab.txt`)
- Пропускает копирование если файл уже скопирован (сравнение по размеру)

## Face Search

Sidecar-контейнер для поиска людей по фото лица в базе Immich.

- Веб-интерфейс: `http://host:8765` — вставить фото (Ctrl+V / drag-drop), получить список совпадений
- Использует Immich ML (`/predict`) для извлечения эмбеддингов — не тащит InsightFace/ONNX локально
- Ищет по pgvector в базе Immich (cosine distance)
- Проксирует thumbnails через `/api/thumb/{id}` (решает проблему аутентификации)

**Сборка и запуск:**
```powershell
docker compose -f docker-compose.prod.yml build face-search
docker compose -f docker-compose.prod.yml up -d face-search
```

**Env vars** (берутся из общего `.env`, можно переопределить):
- `IMMICH_API_KEY` — нужен для загрузки thumbnails (создать в Immich UI: User Settings -> API Keys)

### TODO

- [ ] Бэкап медиафайлов
