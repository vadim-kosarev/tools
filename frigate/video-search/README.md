# Video Search

Sidecar для быстрого текстового поиска по событиям Frigate.

## Зачем

Штатный семантический поиск Frigate (`/api/events/search`) занимает 40-65 секунд на один
текстовый запрос. Причина — jina-clip-v2 в Frigate экспортирован единым ONNX-графом text+vision
(`frigate/embeddings/onnx/jina_v2_embedding.py`): даже для текстового запроса в граф подаётся
пустая картинка 512×512 и прогоняется полная vision-башня (ViT). Проверено: перевод на CPU/small
модель не помогает (68 сек) — дело не в устройстве, а в архитектуре вызова. Плюс GPU и так
поделена между NVDEC, детектором, face recognition и LPR.

Этот сервис не трогает внутренности Frigate (хрупко, ломается при апдейтах), а держит отдельную
копию уже посчитанных thumbnail-эмбеддингов (Frigate сам считает их при индексации события) в
Postgres+pgvector и считает эмбеддинг **текстового** запроса напрямую через text-only путь модели
(`model.encode_text(...)`, без фиктивного прогона по картинке) — на порядок быстрее.

## Архитектура

```
frigate.db (sqlite, read-only mount) ──sync worker──> Postgres (video_search schema, pgvector)
                                                              │
video-search API (FastAPI) ──text-tower(jina-clip-v2)──> encode query → cosine search
```

- **Sync-воркер**: раз в `SYNC_INTERVAL_SEC` читает новые строки `event JOIN vec_thumbnails` из
  `frigate.db` (watermark по `start_time`), апсертит в `video_search.events`.
- **Поиск**: эмбеддинг запроса считается через `transformers` (`jinaai/jina-clip-v2`,
  `trust_remote_code=True`, CPU) — только текстовая башня, без vision. Cosine-поиск —
  `pgvector` (`<=>`, HNSW индекс).
- **UI**: одна страница, поле ввода + сетка результатов.
  - Thumbnail — через `GET /api/proxy/thumbnail/{id}`: backend ходит к Frigate по внутренней
    docker-сети (`frigate:5000`, без auth) и отдаёт байты сам. Нужно, потому что Frigate снаружи
    доступен только через `vkosarev.name:5001` с basic auth, а `<img>` не может передать креды
    для встроенного ресурса — без прокси превьюшки просто не грузились бы через интернет.
  - Клип — прямая ссылка на сам Frigate (`http://<host>:5000/...` локально /
    `https://vkosarev.name:5001/...` через frpc, см. `FRIGATE_HOSTS` в HTML): это top-level
    переход по ссылке (`target=_blank`), браузер нормально спросит basic auth и закэширует —
    в отличие от `<img>`. Через backend не проксируется специально: видео бывает 100+ МБ, гонять
    его вдвойне через video-search незачем.

## DB

Читает из Frigate `event`/`vec_thumbnails` (read-only), пишет только в свою схему
`video_search` (Postgres, тот же контейнер `postgres`, что и ANPR/metabase — не пересекается с их
таблицами). Схема и `CREATE EXTENSION vector` создаются сервисом автоматически при старте.

## Запуск

```powershell
cd C:\dev\github.com\vadim-kosarev\tools\frigate
docker compose build video-search
docker compose up -d postgres video-search
```

Веб-интерфейс: `http://brightsky:8768` (локально) / `https://vkosarev.name:8769` (через frpc)

Первый запуск: качается модель jina-clip-v2 (кэш в `./video-search/model_cache`, чтобы не качать
заново при рестарте) и происходит полный бэкфилл всех событий из `frigate.db` — смотреть прогресс
через `docker logs video-search -f` или `GET /api/stats`.
