# Запускать ПОСЛЕ того, как semantic_search reindex на Frigate полностью завершится
# (см. ../check-reindex-progress.ps1 - дождаться 100%, либо строки в docker logs frigate:
# "Embedded N thumbnails and M descriptions in X seconds").
#
# video-search синкает по watermark вперёд и не замечает, что Frigate переписал те же id
# новыми (исправленными) векторами при reindex - поэтому старую копию нужно снести и
# пересинкать с нуля. HNSW-индекс дропаем заранее: инкрементальная вставка в него на
# бэкфилле в разы медленнее (см. историю в video_search_api.py/_ensure_vector_index) -
# сервис сам пересоздаст его одним махом, когда снова догонит watermark.

docker exec postgres psql -U rgzz -d frigate -c "
DROP INDEX IF EXISTS video_search.idx_video_search_events_embedding;
TRUNCATE video_search.events;
DELETE FROM video_search.sync_state;
"

docker compose -f "$PSScriptRoot\..\docker-compose.yml" restart video-search

Write-Host "Пересинк запущен. Прогресс: docker logs video-search -f  или  curl http://localhost:8768/api/stats"
