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

## Face Finder

Веб-приложение для управления лицами в базе Immich (rich client SPA + REST API).

- Веб-интерфейс: `http://brightsky:8767` (локально) / `https://vkosarev.name:8767` (через frpc)
- **Persons** — список всех персон с thumbnails, поиском и счётчиком лиц; кнопка Merge на каждой карточке
- **Assets** — все файлы с найденными лицами; индикатор доли именованных лиц
- **Unassigned** — face-crops без назначенной персоны с бейджем в сайдбаре
- **Merge** — объединение двух персон (Immich API) с историей в face_finder.merge_log
- Face-crops генерируются на лету (Pillow: bbox → crop из preview → resize 200×200)
- DB: читает из Immich public schema, пишет только в face_finder schema

**Сборка и запуск:**
```powershell
docker compose -f docker-compose.prod.yml build face-finder
docker compose -f docker-compose.prod.yml up -d face-finder
```

## Face Search

Sidecar-контейнер для поиска людей по фото лица в базе Immich.

- Веб-интерфейс: `http://brightsky:8765` (локально) / `https://vkosarev.name:8766` (удалённо)
- Вставить фото (Ctrl+V / drag-drop) — получить список совпадений с thumbnails
- Результаты >= 50% match показываются сразу, слабые скрыты под выезжающую панель
- Ссылки на людей ведут в Immich (домен подбирается автоматически по hostname браузера)
- Использует Immich ML (`/predict`) для извлечения эмбеддингов — не тащит InsightFace/ONNX локально
- Ищет по pgvector в базе Immich (cosine distance)
- Проксирует thumbnails через `/api/thumb/{id}` (решает проблему аутентификации)
- Проброшен наружу через frpc (`8765 → 8765`, nginx на VPS слушает на `8766`)

**Сборка и запуск:**
```powershell
docker compose -f docker-compose.prod.yml build face-search
docker compose -f docker-compose.prod.yml up -d face-search
```

**Env vars** (берутся из общего `.env`, можно переопределить):
- `IMMICH_API_KEY` — нужен для загрузки thumbnails (создать в Immich UI: User Settings -> API Keys)

**Важно:** на VPS nginx для `vkosarev.name:8766` нужен `client_max_body_size 10m;` (base64-картинки больше дефолтного 1MB).

### TODO

- [ ] Бэкап медиафайлов
