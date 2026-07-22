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
 UPDATE asset
 SET status = 'trashed',
     "deletedAt" = now()
 WHERE id IN (
     SELECT id
     FROM duplicates_to_delete
WHERE row_num > 1 AND
"originalPath" LIKE '/mnt/media/luigi-temp/faces%'
 );
