WITH duplicates_to_delete AS (
    SELECT 
        id,
        "originalPath",
        "thumbhash",
        ROW_NUMBER() OVER (PARTITION BY "thumbhash" ORDER BY "originalPath", id) AS row_num
    FROM assets
    WHERE "thumbhash" IN (
        SELECT "thumbhash"
        FROM assets
        GROUP BY "thumbhash"
        HAVING COUNT(*) > 1
    ) 
    ORDER BY "originalPath"
)

--SELECT * FROM duplicates_to_delete
--WHERE row_num > 0 AND
--"originalPath" LIKE '%Photos and videos from Yandex.Disk%'
--ORDER BY thumbhash, row_num, "originalPath";

 DELETE FROM assets
 WHERE id IN (
     SELECT id
     FROM duplicates_to_delete
WHERE row_num > 0 AND
"originalPath" LIKE '%Photos and videos from Yandex.Disk%'
 );
