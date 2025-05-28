		WITH d AS (
			SELECT a.thumbhash 
			FROM assets a 
			GROUP BY a.thumbhash 
			HAVING COUNT(a.thumbhash) > 1
		)
		
		SELECT a.*
		FROM assets a
		LEFT JOIN d ON a.thumbhash = d.thumbhash
		WHERE d.thumbhash IS NOT NULL
		ORDER BY a.thumbhash, a."originalPath"
