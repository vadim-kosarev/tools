# Face Finder SPA — описание и архитектурные решения

Face Finder — веб-приложение для просмотра и управления лицами, обнаруженными в видеофайлах. Построено поверх базы данных Immich, не требует изменений в самом Immich.

---

## Что умеет (для пользователя)

### Страница Videos

Основной экран. Список видеофайлов, прошедших через face-трекер.

- **Алфавитный фильтр** — кнопки с буквами, которые реально присутствуют в именах файлов. Клик — мгновенная фильтрация. Повторный клик снимает фильтр.
- **Текстовый поиск** — по имени файла, с debounce 350ms.
- **Сортировка** — по дате, по имени, по количеству персон.
- **Infinite scroll** — следующая страница подгружается автоматически при приближении к концу списка (за 300px до конца).
- **Миниатюры персон** — для каждого видеофайла показаны лучшие кадры каждой персоны в хронологическом порядке.
- **Hover-tooltip** — наведи мышь на миниатюру и подержи 550ms: всплывает компактный попап с увеличенным кадром, именем файла, временем кадра и позицией на таймлайне. Прячется при уходе мыши.
- **Умное позиционирование tooltip** — автоматически открывается вверх если курсор у нижнего края экрана, и влево если курсор у правого края. Четыре комбинации.

### Модальное окно персонажа

Клик на персону → полноэкранная карточка с:

- Лучшими кадрами этой персоны из всех файлов.
- Списком файлов с миниатюрами кадров, сгруппированных по файлу.
- Клик на имя файла → переход в список Videos с фильтром по этому файлу.
- Hover на миниатюры → тот же tooltip что на странице Videos.

### Полноэкранный просмотр кадра

Клик на миниатюру → полноэкранный просмотр с:

- Именем файла над изображением.
- Временем кадра (HH:mm:ss) и абсолютной датой/временем под изображением.
- Визуальным таймлайном — маркер показывает где именно в видео находится этот кадр.
- Навигацией ← → между кадрами, включая переход между разными файлами.
- Клавишами `←` `→` `Esc`.
- Кнопки навигации фиксированы по краям экрана — не смещаются при смене изображения.

---

## Как устроено (для разработчика)

### Стек

```
Vue 3 (CDN, без build) + VueRouter (CDN)
         ↕ REST/JSON
FastAPI (Python 3.11)
         ↕ psycopg2
PostgreSQL (Immich DB, схема public + face_finder)
```

Один Docker-контейнер: `python:slim`, FastAPI раздаёт и API, и статику.

---

### Решение 1: PhotoModal — один компонент, два режима

Tooltip при наведении и полноэкранный просмотр по клику — это один и тот же компонент `PhotoModal` с флагом `mode: 'modal' | 'tooltip'`.

```js
const photoModal = reactive({
    show: false, url: null, items: [], index: 0,
    mode: 'modal',
    tooltipX: 0, tooltipY: 0, tooltipAbove: false, tooltipLeft: false
});
```

Вычисляемые свойства `currentItem`, `timeStr`, `timelinePct` — общие для обоих режимов. Изменение в одном месте автоматически работает везде. Ни строчки дублирования логики.

---

### Решение 2: Tooltip — четыре направления через CSS transform

Позиционирование tooltip у курсора:

```js
photoModal.tooltipAbove = (event.clientY + 380) > window.innerHeight;
photoModal.tooltipLeft  = (event.clientX + 290) > window.innerWidth;
```

Четыре CSS-класса покрывают все комбинации:

```css
.photo-tooltip            { transform: translate( 14px,              14px); }
.photo-tooltip.above      { transform: translate( 14px,  calc(-100% - 14px)); }
.photo-tooltip.left       { transform: translate(calc(-100% - 14px),  14px); }
.photo-tooltip.above.left { transform: translate(calc(-100% - 14px), calc(-100% - 14px)); }
```

Tooltip никогда не уходит за границы экрана — ни снизу, ни справа.

---

### Решение 3: Кастомный lazy loading через Vue-директиву

Браузерный `loading="lazy"` не реагирует достаточно быстро при резком прыжке клавишей End. Решение — Vue-директива `v-lazy-src` на базе `IntersectionObserver`:

```js
const _lazyObserver = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
        if (!entry.isIntersecting) return;
        entry.target.src = entry.target.dataset.lazySrc;
        _lazyObserver.unobserve(entry.target);
    });
}, { rootMargin: '200px' });

const vLazySrc = {
    mounted(el, binding) {
        el.dataset.lazySrc = binding.value;
        _lazyObserver.observe(el);
    },
    updated(el, binding) {
        if (binding.value !== binding.oldValue) {
            el.dataset.lazySrc = binding.value;
            el.src = '';
            _lazyObserver.observe(el);
        }
    },
    unmounted(el) { _lazyObserver.unobserve(el); },
};
```

Один глобальный observer на весь документ (не per-image). При монтировании страницы с 246 миниатюрами — только 10 получают `src` (те, что в вьюпорте ±200px), 236 ждут. При прыжке к концу страницы — нужные images получают `src` в следующем animation frame.

---

### Решение 4: sync def вместо async def для thumbnail-эндпоинтов

FastAPI + синхронный `psycopg2` внутри `async def` = блокировка event loop. Все запросы выполняются **последовательно**, даже если клиент посылает их параллельно.

Замер до исправления (5 параллельных запросов):

```
individual_ms: [99, 196, 295, 381, 479]   ← явная последовательность
total_ms: 479
```

Исправление элегантно простое — убрать `async`:

```python
# Было:
async def ff_face_track_segment_thumbnail(segment_id: int) -> Response:

# Стало:
def ff_face_track_segment_thumbnail(segment_id: int) -> Response:
```

FastAPI автоматически запускает синхронные `def`-эндпоинты в thread pool executor — настоящий параллелизм без лишнего кода.

---

### Решение 5: LRU-кэш в памяти для JPEG-блобов

Миниатюры — статичные JPEG-блобы в БД, никогда не меняются. `@lru_cache` кэширует их в памяти процесса:

```python
@lru_cache(maxsize=5000)
def _cached_segment_jpeg(segment_id: int) -> Optional[bytes]:
    with _pooled_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT jpeg FROM face_finder.face_track_segments WHERE id = %s", (segment_id,))
        row = cur.fetchone()
    return bytes(row["jpeg"]) if row and row["jpeg"] else None
```

Замер на одних и тех же 10 миниатюрах:

```
cold_cache_ms: 516   ← первый запрос, читает из БД
warm_cache_ms:  52   ← повторный, из памяти (10x быстрее)
```

Браузерный `Cache-Control: public, max-age=86400, immutable` дополняет: повторное открытие страницы — миниатюры из браузерного кэша, вообще без сетевых запросов.

---

### Решение 6: Два типа DB-соединений — прямые и пулируемые

Ловушка: если добавить `ThreadedConnectionPool` и все эндпоинты начнут брать из него соединения, но вернуть через `conn.close()` (а не `putconn()`) — пул молча исчерпается через ~20 запросов.

Решение: разделить ответственность.

```python
def _get_conn():
    """Прямое соединение — для async-эндпоинтов. conn.close() корректен."""
    return psycopg2.connect(...)

@contextmanager
def _pooled_conn():
    """Из пула — только для sync thumbnail-эндпоинтов. Возврат гарантирован."""
    conn = _get_pool().getconn()
    try:
        yield conn
    finally:
        _get_pool().putconn(conn)
```

Async-эндпоинты (одиночные тяжёлые запросы) — прямые соединения, закрываются сразу.  
Sync thumbnail-эндпоинты (много параллельных лёгких запросов) — пул с переиспользованием соединений.

---

### Решение 7: Временная шкала кадра

Каждый кадр в БД имеет `frame_index` (абсолютный номер кадра в видео). Из него вычисляется всё:

```js
// Время в формате HH:mm:ss
const totalSecs = Math.round(frame_index / fps);
// Позиция на таймлайне 0..100%
const timelinePct = (frame_index / total_frames * 100).toFixed(1);
// Абсолютная дата/время (если известно время начала записи)
const dt = new Date(start_time);
dt.setSeconds(dt.getSeconds() + totalSecs);
```

Один атрибут в БД → время кадра, дата события, визуальная позиция в видео.

---

### Решение 8: Алфавитный фильтр — только реальные буквы

Отдельный лёгкий endpoint:

```sql
SELECT DISTINCT UPPER(LEFT(filename, 1)) AS letter
FROM face_finder.video_files
ORDER BY letter
```

На фронте отображаются только буквы, под которыми реально есть файлы. Фильтрация на бэке:

```sql
WHERE UPPER(LEFT(vf.filename, 1)) = 'D'
```

Никаких пустых результатов при клике, никаких серых недоступных кнопок — только то, что есть.

---

### Итог по архитектуре

| Проблема | Решение |
|---|---|
| Tooltip + Modal — одинаковая логика | Один компонент, два режима |
| `loading="lazy"` медленно реагирует | Кастомная директива + IntersectionObserver |
| async + psycopg2 = sequential | `def` → FastAPI thread pool |
| Медленные повторные запросы thumbnail | `@lru_cache` + `Cache-Control: immutable` |
| Pool exhaustion при смешивании | Два вида соединений с чёткой ответственностью |
