-- Актуальная схема Immich: таблица переименована "assets" -> "asset",
-- добавлено мягкое удаление ("deletedAt", "status"), которое нужно учитывать в выборке.
WITH duplicates_to_delete AS (
    SELECT
        id,
        "originalPath",
        "thumbhash",
        ROW_NUMBER() OVER (PARTITION BY "thumbhash" ORDER BY "originalPath", id) AS row_num
    FROM asset
    WHERE "deletedAt" IS NULL
      AND status = 'active'
      AND "thumbhash" IN (
          SELECT "thumbhash"
          FROM asset
          WHERE "deletedAt" IS NULL
            AND status = 'active'
            AND "thumbhash" IS NOT NULL
          GROUP BY "thumbhash"
          HAVING COUNT(*) > 1
      )
    ORDER BY "originalPath"
)

--SELECT * FROM duplicates_to_delete
--WHERE row_num > 1 AND
--"originalPath" LIKE '/mnt/media/luigi-temp/faces%'
--ORDER BY thumbhash, row_num, "originalPath";

-- Мягкое удаление (в корзину), как штатная кнопка Delete в Immich UI:
-- status='trashed' + deletedAt=now(). "updatedAt"/"updateId" проставит триггер asset_updatedAt.
-- Immich сам физически удалит файлы и запись по истечении Trash retention (Admin -> Storage Template).
 UPDATE asset
 SET status = 'trashed',
     "deletedAt" = now()
 WHERE id IN (
     SELECT id
     FROM duplicates_to_delete
WHERE row_num > 1 AND
"originalPath" LIKE '/mnt/media/luigi-temp/faces%'
 );