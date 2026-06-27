# Face Finder

Веб-приложение для управления лицами в базе Immich.

## Функциональность

- **Persons** — список всех именованных и анонимных персон с thumbnails и счётчиком лиц
- **Assets** — список файлов, в которых обнаружены лица; thumbnail файла + счётчик лиц
- **Asset detail** — превью файла со всеми найденными лицами и ссылками на персон
- **Person detail** — все face-crops персоны, кнопка объединения
- **Unassigned** — лица без назначенной персоны
- **Merge** — объединение двух персон через Immich API (POST /api/people/{id}/merge)
- **Merge log** — история объединений (хранится в schema face_finder)

## Архитектура

```
rich client (Vue 3 SPA) <──REST──> FastAPI backend <──psycopg2──> PostgreSQL (Immich DB)
                                        │
                                        └──httpx──> Immich API (thumbnails, merge)
```

## Выбор фреймворков

### Рассматривались варианты

| Вариант | За | Против |
|---|---|---|
| **Vue 3 CDN + FastAPI** | Нет build pipeline, одна единица деплоя, консистентно с face_search | Нет типизации шаблонов |
| React (Vite) + FastAPI | Типизация, большая экосистема | Нужен build step, усложняет Dockerfile |
| HTMX + Alpine.js + FastAPI | Простота, server-driven | Беднее интерактивность для grid/modal/поиска |

### Выбор: Vue 3 CDN + FastAPI

- Нет build pipeline → Dockerfile остаётся простым (python:slim)
- Vue 3 CDN даёт полноценный rich client: реактивность, router, компоненты
- Консистентно с существующим face_search_web.py
- Один контейнер

## DB Schema

Все данные о лицах/персонах читаются из схемы `public` (Immich).  
Схема `face_finder` хранит только дополнительные данные этого инструмента.

```sql
CREATE SCHEMA IF NOT EXISTS face_finder;

CREATE TABLE IF NOT EXISTS face_finder.merge_log (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_person_id   UUID NOT NULL,
    target_person_id   UUID NOT NULL,
    source_person_name TEXT,
    target_person_name TEXT,
    face_count_moved   INT DEFAULT 0,
    merged_at          TIMESTAMPTZ DEFAULT NOW()
);
```

## REST API

| Метод | URL | Описание |
|---|---|---|
| GET | /api/stats | Сводная статистика |
| GET | /api/persons | Список персон (page, limit, q) |
| GET | /api/persons/{id} | Персона + все её face-crops |
| GET | /api/persons/{id}/thumbnail | Proxy thumbnail из Immich |
| POST | /api/persons/merge | Объединить две персоны |
| GET | /api/assets/with-faces | Файлы с лицами (page, limit) |
| GET | /api/assets/{id}/thumbnail | Proxy thumbnail из Immich |
| GET | /api/assets/{id}/faces | Лица в конкретном файле |
| GET | /api/faces/{id}/crop | Кроп лица из превью (Pillow) |
| GET | /api/faces/unassigned | Лица без персоны (page, limit) |
| GET | /api/merge-log | История объединений |

## Запуск

```powershell
docker compose -f docker-compose.prod.yml build face-finder
docker compose -f docker-compose.prod.yml up -d face-finder
```

Веб-интерфейс: `http://brightsky:8767`

## Env vars

Берутся из общего `.env`:
- `IMMICH_API_KEY` — для вызовов Immich API (thumbnails, merge)
- `IMMICH_URL` — базовый URL Immich (default: `http://immich-server:2283`)
- `IMMICH_DB_*` — параметры подключения к PostgreSQL

## Plan реализации (выполнено)

1. ✅ DB schema `face_finder` (auto-создаётся при старте)
2. ✅ FastAPI backend (`face_finder_api.py`) — все REST endpoints
3. ✅ Face crop endpoint (Pillow: normalize bbox → crop preview → resize 200×200)
4. ✅ Vue 3 SPA (`static/index.html` + `static/app.js`)
   - Persons grid с поиском и пагинацией
   - Person detail с face-crops grid
   - Assets grid
   - Asset detail с лицами
   - Unassigned faces
   - Merge modal (поиск target, confirm)
   - Merge log таблица
5. ✅ Dockerfile
6. ✅ Интеграция в docker-compose.prod.yml (port 8767)
7. ✅ frpc туннель (8767)
