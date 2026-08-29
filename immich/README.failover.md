# Immich: спящий failover на starlight

Инструкция по эксплуатации резервного узла. Проектное обоснование, история проверок и
разбор граблей — в [`.ai/20260708.003_ha_failover_plan.md`](.ai/20260708.003_ha_failover_plan.md).

## Как это устроено

Схема — warm standby (active/passive), без кластера:

| Узел | Роль | Что запущено |
|------|------|--------------|
| **brightsky** | primary | полный стек Immich |
| **starlight** | спящий резерв | **только** контейнер БД `immich_postgres` — standby, стримит WAL с brightsky |

Внешний доступ идёт через `frps`: регистрацию имени прокси держит тот `frpc`, который жив.
Когда brightsky отваливается, `frpc` на starlight (он в retry-цикле) подхватывает её сам —
отдельно переключать ничего не нужно.

Реплика почти актуальна всегда (RPO — секунды), поэтому при аварии достаточно промоутить
локальный Postgres и поднять остальные контейнеры.

Все команды ниже выполняются **на starlight** из `C:\dev\github.com\vadim-kosarev\tools\immich\docker`,
если не указано иное.

## Главное про этот режим

Запущенный и «здоровый» контейнер **не означает**, что резерв рабочий. `immich_postgres`
может месяцами стоять в recovery, отвечать на healthcheck и при этом ничего не получать с
primary — снаружи это никак не проявляется, пока не понадобится failover. Проверять надо
**стриминг** (`pg_stat_wal_receiver`), а не факт запуска контейнера.

Это не гипотеза: 14.08.2026 репликация была сломана и об этом никто не знал —
подробности в разделе «Инцидент».

## Спящий режим: обычное состояние

Поднять standby-БД (после перезагрузки узла, например):

```powershell
cd C:\dev\github.com\vadim-kosarev\tools\immich\docker
docker compose -f docker-compose.prod.yml --env-file .env.starlight up -d database
```

Оверрайд `docker-compose.starlight.override.yml` здесь **не нужен** — он только для полного стека.

Проверить, что это настоящий standby и он стримит:

```powershell
docker exec immich_postgres psql -U postgres -d immich -c "SELECT pg_is_in_recovery();"
docker exec immich_postgres psql -U postgres -d immich -c "SELECT status, sender_host, latest_end_time FROM pg_stat_wal_receiver;"
```

- `pg_is_in_recovery()` = `t` — standby в порядке. `f` означает, что узел промоутнут и
  репликации нет: нужен пересидинг (см. ниже).
- `status` = `streaming` — WAL идёт. **Пустой результат (0 строк) — приёмник WAL не работает**,
  то есть узел в recovery, но с primary ничего не тянет. Причину смотреть в логе (он уходит в
  файл, а не в `docker logs`):

```powershell
docker exec immich_postgres sh -c 'ls -t /var/lib/postgresql/data/log/*.log | head -1 | xargs tail -30'
```

Отставание реплики:

```powershell
docker exec immich_postgres psql -U postgres -d immich -c "SELECT pg_last_wal_receive_lsn(), pg_last_wal_replay_lsn(), now() - pg_last_xact_replay_timestamp() AS lag;"
```

Со стороны primary — жив ли слот (запускается с любого хоста, где есть psql/контейнер):

```powershell
docker exec -e PGPASSWORD=postgres immich_postgres psql -h brightsky -U postgres -d immich -c "SELECT slot_name, active, wal_status, safe_wal_size FROM pg_replication_slots;"
```

`active = t`, `wal_status = reserved` — всё хорошо. `wal_status = lost` — слот уничтожен,
репликация не восстановится сама (см. грабли).

## Failover: brightsky упал

Признак: `https://vkosarev.name:7601` отдаёт `502`.

```powershell
cd C:\dev\github.com\vadim-kosarev\tools\immich\docker
.\failover-to-starlight.ps1
```

Скрипт (`-Force` пропускает подтверждение) делает четыре шага:

1. `SELECT pg_promote();` — standby становится read-write primary;
2. `docker compose ... pull immich-server immich-machine-learning` — `IMMICH_VERSION=release`
   это плавающий тег, и `up` сам по себе **не** перекачивает уже закэшированный образ;
3. `up -d redis immich-server immich-machine-learning frpc` с
   `docker-compose.starlight.override.yml` и `--env-file .env.starlight`;
4. печатает `docker compose ps`.

Проверка после запуска:

- локально — `http://localhost:2283` (должен открыться логин и библиотека);
- снаружи — `https://vkosarev.name:7601`, как только `frpc` на starlight перехватит
  регистрацию у brightsky.

**Промоушен необратим.** После него starlight — самостоятельный primary, разошедшийся с
brightsky; репликация прекращается навсегда. Вернуть warm standby можно только пересидингом
с нуля. Поэтому скрипт спрашивает подтверждение — запускать, только когда brightsky
действительно недоступен.

## Возврат на brightsky

Когда primary снова поднят и стал рабочим:

```powershell
docker compose -f docker-compose.prod.yml --env-file .env.starlight stop immich-server redis immich-machine-learning frpc
```

`immich_postgres` при этом остаётся промоутнутым — то есть standby пока нет. Дальше —
пересидинг.

## Пересидинг standby

Нужен при первичной настройке, после промоушена и после потери слота (`wal_status = lost`).

Если слот на primary в состоянии `lost`, его сначала надо пересоздать — иначе
`pg_basebackup -S starlight_standby` упрётся в мёртвый слот (на **brightsky**):

```powershell
docker exec immich_postgres psql -U postgres -d immich -c "SELECT pg_drop_replication_slot('starlight_standby');"
docker exec immich_postgres psql -U postgres -d immich -c "SELECT pg_create_physical_replication_slot('starlight_standby');"
```

Снести старые данные:

```powershell
docker compose -f docker-compose.prod.yml --env-file .env.starlight stop database
docker compose -f docker-compose.prod.yml --env-file .env.starlight rm -f database
Remove-Item -Recurse -Force H:\immich\upload\postgres
```

Забрать базовую копию с primary (`brightsky`):

```powershell
docker run --rm -e PGPASSWORD='пароль' -v H:/immich/upload/postgres:/var/lib/postgresql/data `
  ghcr.io/immich-app/postgres:14-vectorchord0.3.0-pgvectors0.2.0 `
  pg_basebackup -h brightsky -p 5432 -U replicator -D /var/lib/postgresql/data -Fp -Xs -P -R -S starlight_standby
```

Поднять standby поверх засеянных данных и убедиться, что стримит:

```powershell
docker compose -f docker-compose.prod.yml --env-file .env.starlight up -d database
docker exec immich_postgres psql -U postgres -d immich -c "SELECT status FROM pg_stat_wal_receiver;"
```

### Разовая настройка на brightsky

Делается один раз при первой настройке, при последующих пересидингах не повторяется:

```powershell
docker exec immich_postgres psql -U postgres -d immich -c "CREATE ROLE replicator WITH REPLICATION LOGIN PASSWORD 'пароль';"
docker exec immich_postgres psql -U postgres -d immich -c "SELECT pg_create_physical_replication_slot('starlight_standby');"
docker exec immich_postgres psql -U postgres -d immich -c "ALTER SYSTEM SET max_slot_wal_keep_size = '10GB';"
docker exec immich_postgres psql -U postgres -d immich -c "SELECT pg_reload_conf();"
```

В `K:\immich\upload\postgres\pg_hba.conf` должна быть строка (адрес именно `all`: из-за NAT
Docker Desktop/WSL2 на starlight конкретный IP не срабатывает), после правки — `pg_reload_conf()`:

```
host replication replicator all scram-sha-256
```

## Грабли

| Симптом | Причина и что делать |
|---------|----------------------|
| Поднялась старая версия Immich | `IMMICH_VERSION=release` — плавающий тег, `up` не тянет новый образ. Нужен явный `docker compose ... pull immich-server` (скрипт это делает) |
| `immich-server` не стартует, ошибка монтирования CIFS | Недоступен `luigi` с внешними библиотеками. Поднимать с `-f docker-compose.starlight.override.yml` — он убирает эти тома |
| Снаружи по-прежнему `502` | `frps` отдаёт имя прокси тому `frpc`, кто жив. Убедиться, что контейнер `frpc` на starlight запущен, и дать ему отработать retry |
| `pg_is_in_recovery()` = `f` в спящем режиме | Узел промоутнут, репликации нет — нужен пересидинг |
| Из контейнера `could not translate host name "brightsky"`, причём через раз | DNS отдаёт по имени `brightsky` три A-записи: `192.168.1.43` и два докеровских моста самого brightsky (`172.22.128.1`, `172.18.80.1`). Контейнер выбирает адрес произвольно. Лечится `extra_hosts` в `docker-compose.prod.yml` (уже добавлено для `face-finder`, `face-search`, `face-indexer`, `frpc`); при смене IP править там. По-хорошему — убрать лишние записи из DNS на brightsky |
| В логе `requested WAL segment ... has already been removed`, `pg_stat_wal_receiver` пуст, слот `wal_status = lost` | Standby отстал больше, чем `max_slot_wal_keep_size` (сейчас 10 ГБ), primary переработал нужные сегменты и уничтожил слот. Само не починится: пересоздать слот и пересидить. Чтобы повторялось реже — держать standby постоянно запущенным и/или поднять `max_slot_wal_keep_size` на brightsky (`ALTER SYSTEM SET max_slot_wal_keep_size = '50GB'; SELECT pg_reload_conf();`) |

## Инцидент 14.08.2026: молчаливая остановка репликации

**Что было.** При проверке standby выяснилось: контейнер `immich_postgres` запущен, healthcheck
зелёный, `pg_is_in_recovery()` возвращает `t` — формально всё в порядке. При этом
`pg_stat_wal_receiver` пуст, то есть WAL не принимается вообще.

В логе (в файле, не в `docker logs`) — цикл каждые 5 секунд:

```
LOG:   started streaming WAL from primary at 28/D3000000 on timeline 1
FATAL: could not receive data from WAL stream:
       requested WAL segment 0000000100000028000000D3 has already been removed
```

Слот на primary: `active = f`, `wal_status = lost`. Standby стоял на `28/D3000000`, primary
ушёл на `30/4103B870` — отставание около 30 ГБ при лимите удержания
`max_slot_wal_keep_size = 10GB`. Primary переработал нужные сегменты и уничтожил слот.

**Почему это опасно.** Реплика была мертва неизвестно сколько, и ни один внешний признак на
это не указывал. Запусти в тот момент `failover-to-starlight.ps1` — он бы честно отработал и
поднял Immich с базой месячной давности: потерялись бы все правки, лица, альбомы и загрузки за
период. Причём промоушен необратим, то есть откатиться было бы уже некуда.

**Что сделали.** Пересоздали слот, снесли данные standby и пересидили заново через
`pg_basebackup`.

**Выводы.**

1. Здоровье резерва определяется только `pg_stat_wal_receiver` и состоянием слота на primary.
   Ни `docker ps`, ни healthcheck, ни `pg_is_in_recovery()` признаком не являются.
2. Проверять регулярно, а не в момент аварии — иначе о поломке узнаёшь тогда, когда резерв уже
   нужен и уже бесполезен.
3. `max_slot_wal_keep_size = 10GB` мало для узла, который надолго выключают: столько WAL
   набегает быстрее, чем кажется. Либо держать standby постоянно включённым, либо поднимать
   лимит на brightsky.
4. Логи Postgres уходят в файл внутри контейнера, `docker logs` показывает только строку про
   logging collector — искать причину надо в `/var/lib/postgresql/data/log/`.

## Файлы

| Файл | Назначение |
|------|-----------|
| `docker/failover-to-starlight.ps1` | Промоушен + подъём полного стека |
| `docker/docker-compose.starlight.override.yml` | Оверрайд для starlight: без томов luigi, свой `frpc.ini` |
| `docker/.env.starlight` | Окружение резервного узла (`UPLOAD_LOCATION=H:/immich/upload`) |
| `docker/frpc.starlight.ini` | Конфиг frpc для резервного узла |
