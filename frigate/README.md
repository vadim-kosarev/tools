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

Настроен автоматический pg_dump через контейнер `postgres-backup` (образ `prodrigestivill/postgres-backup-local:16`):
- Расписание: ежедневно (`@daily`)
- Хранение: 7 дней / 4 недели / 6 месяцев
- Дампы пишутся в `K:\frigate\backups\postgres`

Запуск: `docker compose up -d postgres-backup`

### SQLite (frigate.db)

Внутренняя БД Frigate (`./config/frigate.db`) — не бэкапится автоматически.
Frigate создаёт `backup.db` при обновлениях, но это не полноценный бэкап.

### Media (записи камер)

Хранятся в `K:\frigate` с встроенной ротацией:
- motion: 14 дней (per-camera) / 30 дней (global)
- alerts/detections: 180 дней (per-camera) / 30 дней (global)

Внешний бэкап медиа не настроен.

### TODO

- [ ] Бэкап `frigate.db` (cron-копия в `K:\frigate\backups\sqlite`)
- [ ] Вынос дампов на внешнее хранилище (NAS, S3)
