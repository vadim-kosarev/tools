-- --------------------------------------------------------
-- Host:                         brightsky
-- Server version:               PostgreSQL 16.10 (Debian 16.10-1.pgdg13+1) on x86_64-pc-linux-gnu, compiled by gcc (Debian 14.2.0-19) 14.2.0, 64-bit
-- Server OS:                    
-- HeidiSQL Version:             12.6.0.6765
-- --------------------------------------------------------

/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET NAMES  */;
/*!40103 SET @OLD_TIME_ZONE=@@TIME_ZONE */;
/*!40103 SET TIME_ZONE='+00:00' */;
/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0 */;
/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;
/*!40111 SET @OLD_SQL_NOTES=@@SQL_NOTES, SQL_NOTES=0 */;

-- Dumping structure for table public.data_anpr
CREATE TABLE IF NOT EXISTS "data_anpr" (
	"event_id" BIGINT NOT NULL,
	"status" VARCHAR(16) NULL DEFAULT 'PENDING',
	"result" JSONB NULL DEFAULT NULL,
	PRIMARY KEY ("event_id"),
	CONSTRAINT "id_fk" FOREIGN KEY ("event_id") REFERENCES "data_events" ("id") ON UPDATE NO ACTION ON DELETE NO ACTION
);

-- Data exporting was unselected.

-- Dumping structure for table public.data_events
CREATE TABLE IF NOT EXISTS "data_events" (
	"id" SERIAL NOT NULL,
	"created_at" TIMESTAMPTZ NULL DEFAULT now(),
	"data" JSONB NULL DEFAULT NULL,
	"data_hash" TEXT NULL DEFAULT NULL,
	"source" VARCHAR NULL DEFAULT NULL,
	PRIMARY KEY ("id"),
	INDEX "idx_events_source" ("source"),
	UNIQUE INDEX "uq_data_events_data_hash" ("data_hash")
);

-- Data exporting was unselected.

-- Dumping structure for view public.view_events_media
-- Creating temporary table to overcome VIEW dependency errors
CREATE TABLE "view_events_media" (
	"id" BIGINT NULL,
	"type" TEXT NULL,
	"camera" TEXT NULL,
	"label" TEXT NULL,
	"event_id" TEXT NULL,
	"source" VARCHAR NULL,
	"has_clip" BOOLEAN NULL,
	"media" TEXT NULL,
	"start_time" TIMESTAMPTZ NULL,
	"PLATE_NO" TEXT NULL,
	"PLATE_NO_DET" TEXT NULL,
	"MESSAGE" TEXT NULL,
	"status" VARCHAR(16) NULL
) ENGINE=MyISAM;

-- Dumping structure for view public.view_plate_numbers
-- Creating temporary table to overcome VIEW dependency errors
CREATE TABLE "view_plate_numbers" (
	"plate_digits" TEXT NULL,
	"groups_count" BIGINT NULL
) ENGINE=MyISAM;

-- Removing temporary table and create final VIEW structure
DROP TABLE IF EXISTS "view_events_media";
CREATE VIEW "view_events_media" AS  SELECT data_events.id,
    (jsonb_path_query_first(data_events.data, '$."type"'::jsonpath) #>> '{}'::text[]) AS type,
    COALESCE((jsonb_path_query_first(data_events.data, '$."after"."camera"'::jsonpath) #>> '{}'::text[]), (jsonb_path_query_first(data_events.data, '$."before"."camera"'::jsonpath) #>> '{}'::text[])) AS camera,
    COALESCE((jsonb_path_query_first(data_events.data, '$."after"."label"'::jsonpath) #>> '{}'::text[]), (jsonb_path_query_first(data_events.data, '$."before"."label"'::jsonpath) #>> '{}'::text[])) AS label,
    COALESCE((jsonb_path_query_first(data_events.data, '$."after"."id"'::jsonpath) #>> '{}'::text[]), (jsonb_path_query_first(data_events.data, '$."before"."id"'::jsonpath) #>> '{}'::text[])) AS event_id,
    data_events.source,
    (COALESCE((jsonb_path_query_first(data_events.data, '$."after"."has_clip"'::jsonpath) #>> '{}'::text[]), (jsonb_path_query_first(data_events.data, '$."before"."has_clip"'::jsonpath) #>> '{}'::text[])))::boolean AS has_clip,
    concat_ws(', '::text,
        CASE
            WHEN (COALESCE((jsonb_path_query_first(data_events.data, '$."after"."has_clip"'::jsonpath) #>> '{}'::text[]), (jsonb_path_query_first(data_events.data, '$."before"."has_clip"'::jsonpath) #>> '{}'::text[])))::boolean THEN (((('http://'::text || (data_events.source)::text) || ':5000/api/events/'::text) || COALESCE((jsonb_path_query_first(data_events.data, '$."after"."id"'::jsonpath) #>> '{}'::text[]), (jsonb_path_query_first(data_events.data, '$."before"."id"'::jsonpath) #>> '{}'::text[]))) || '/clip.mp4'::text)
            ELSE NULL::text
        END,
        CASE
            WHEN (COALESCE((jsonb_path_query_first(data_events.data, '$."after"."has_snapshot"'::jsonpath) #>> '{}'::text[]), (jsonb_path_query_first(data_events.data, '$."before"."has_snapshot"'::jsonpath) #>> '{}'::text[])))::boolean THEN (((('http://'::text || (data_events.source)::text) || ':5000/api/events/'::text) || COALESCE((jsonb_path_query_first(data_events.data, '$."after"."id"'::jsonpath) #>> '{}'::text[]), (jsonb_path_query_first(data_events.data, '$."before"."id"'::jsonpath) #>> '{}'::text[]))) || '/snapshot.jpg'::text)
            ELSE NULL::text
        END) AS media,
    COALESCE(to_timestamp(((jsonb_path_query_first(data_events.data, '$."after"."start_time"'::jsonpath) #>> '{}'::text[]))::double precision), to_timestamp(((jsonb_path_query_first(data_events.data, '$."before"."start_time"'::jsonpath) #>> '{}'::text[]))::double precision)) AS start_time,
    (jsonb_path_query_first(anpr.result, '$."adjusted"'::jsonpath) #>> '{}'::text[]) AS "PLATE_NO",
    (jsonb_path_query_first(anpr.result, '$."detected"'::jsonpath) #>> '{}'::text[]) AS "PLATE_NO_DET",
    (jsonb_path_query_first(anpr.result, '$."message"'::jsonpath) #>> '{}'::text[]) AS "MESSAGE",
    anpr.status
   FROM (data_events
     LEFT JOIN data_anpr anpr ON ((data_events.id = anpr.event_id)));;

-- Removing temporary table and create final VIEW structure
DROP TABLE IF EXISTS "view_plate_numbers";
CREATE VIEW "view_plate_numbers" AS  WITH diffs AS (
         SELECT view_events_media.id,
            SUBSTRING(view_events_media."PLATE_NO" FROM 2 FOR 3) AS plate_digits,
            view_events_media.start_time,
                CASE
                    WHEN (((view_events_media.start_time - lag(view_events_media.start_time) OVER (PARTITION BY (SUBSTRING(view_events_media."PLATE_NO" FROM 2 FOR 3)) ORDER BY view_events_media.start_time)) > '00:01:00'::interval) OR (lag(view_events_media.start_time) OVER (PARTITION BY (SUBSTRING(view_events_media."PLATE_NO" FROM 2 FOR 3)) ORDER BY view_events_media.start_time) IS NULL)) THEN 1
                    ELSE 0
                END AS is_new_group
           FROM view_events_media
        ), grouped AS (
         SELECT diffs.id,
            diffs.plate_digits,
            diffs.start_time,
            diffs.is_new_group,
            sum(diffs.is_new_group) OVER (PARTITION BY diffs.plate_digits ORDER BY diffs.start_time ROWS UNBOUNDED PRECEDING) AS group_id
           FROM diffs
        )
 SELECT plate_digits,
    count(DISTINCT group_id) AS groups_count
   FROM grouped
  WHERE ((plate_digits IS NOT NULL) AND (plate_digits <> ''::text) AND (plate_digits <> '___'::text))
  GROUP BY plate_digits
  ORDER BY (count(DISTINCT group_id)) DESC, plate_digits;;

/*!40103 SET TIME_ZONE=IFNULL(@OLD_TIME_ZONE, 'system') */;
/*!40101 SET SQL_MODE=IFNULL(@OLD_SQL_MODE, '') */;
/*!40014 SET FOREIGN_KEY_CHECKS=IFNULL(@OLD_FOREIGN_KEY_CHECKS, 1) */;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40111 SET SQL_NOTES=IFNULL(@OLD_SQL_NOTES, 1) */;
