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

### TODO

- [ ] Вынос дампов БД на внешнее хранилище (NAS, S3, другой диск)
- [ ] Бэкап медиафайлов
