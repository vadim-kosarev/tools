# Проверка прогресса semantic_search reindex (frigate/config/config.yaml: semantic_search.reindex: true).
# vec_thumbnails полностью дропается при старте reindex и заполняется заново от новых событий к старым.
# См. frigate/README.md - раздел про откат на 0.16.4 из-за битых эмбеддингов в 0.17.x.

docker exec frigate python3 -c "
import sqlite3
c = sqlite3.connect('file:/config/frigate.db?mode=ro', uri=True)
cur = c.cursor()
cur.execute('SELECT COUNT(*) FROM event'); total = cur.fetchone()[0]
cur.execute('SELECT COUNT(*) FROM vec_thumbnails_rowids'); done = cur.fetchone()[0]
print(f'{done}/{total} ({100*done/total:.1f}%)')
"
