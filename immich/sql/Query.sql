WITH duplicates_to_delete AS (
    SELECT 
        id,
        "originalPath",
        "originalFileName",
        "thumbhash",
        ROW_NUMBER() OVER (PARTITION BY "thumbhash" ORDER BY "originalPath", id) AS row_num,
        ROW_NUMBER() OVER (PARTITION BY "originalPath" ORDER BY "originalPath", id) AS row_num1,
        "fileCreatedAt",
        "fileModifiedAt",
        "createdAt",
        "updatedAt",
        "duplicateId",
        "encodedVideoPath"
    FROM assets
    WHERE "thumbhash" IN (
        SELECT "thumbhash"
        FROM assets
        GROUP BY "thumbhash"
        HAVING COUNT(*) > 1
    ) 
    ORDER BY "originalPath"
)



SELECT * FROM duplicates_to_delete

--				 DELETE FROM assets            --#
--				 WHERE id IN (                 --#
--				     SELECT id      		       --#
--				     FROM duplicates_to_delete --#

WHERE row_num > 0 
AND "originalPath" LIKE '%%'
--AND row_num1 > 1 -- row_num1 >1 == samePath
AND "originalFileName" LIKE '%%'

--				 );  	                         --#

ORDER BY thumbhash, row_num, "originalPath";
