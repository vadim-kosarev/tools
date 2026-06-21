# Immich

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
