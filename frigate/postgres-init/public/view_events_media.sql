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

-- Removing temporary table and create final VIEW structure
DROP TABLE IF EXISTS "view_events_media";
CREATE VIEW "view_events_media" AS  SELECT id,
    (jsonb_path_query_first(data, '$."type"'::jsonpath) #>> '{}'::text[]) AS type,
    COALESCE((jsonb_path_query_first(data, '$."after"."camera"'::jsonpath) #>> '{}'::text[]), (jsonb_path_query_first(data, '$."before"."camera"'::jsonpath) #>> '{}'::text[])) AS camera,
    COALESCE((jsonb_path_query_first(data, '$."after"."label"'::jsonpath) #>> '{}'::text[]), (jsonb_path_query_first(data, '$."before"."label"'::jsonpath) #>> '{}'::text[])) AS label,
    COALESCE((jsonb_path_query_first(data, '$."after"."id"'::jsonpath) #>> '{}'::text[]), (jsonb_path_query_first(data, '$."before"."id"'::jsonpath) #>> '{}'::text[])) AS event_id,
    source,
    (COALESCE((jsonb_path_query_first(data, '$."after"."has_clip"'::jsonpath) #>> '{}'::text[]), (jsonb_path_query_first(data, '$."before"."has_clip"'::jsonpath) #>> '{}'::text[])))::boolean AS has_clip,
    concat_ws(', '::text,
        CASE
            WHEN (COALESCE((jsonb_path_query_first(data, '$."after"."has_clip"'::jsonpath) #>> '{}'::text[]), (jsonb_path_query_first(data, '$."before"."has_clip"'::jsonpath) #>> '{}'::text[])))::boolean THEN (((('http://'::text || (source)::text) || ':5000/api/events/'::text) || COALESCE((jsonb_path_query_first(data, '$."after"."id"'::jsonpath) #>> '{}'::text[]), (jsonb_path_query_first(data, '$."before"."id"'::jsonpath) #>> '{}'::text[]))) || '/clip.mp4'::text)
            ELSE NULL::text
        END,
        CASE
            WHEN (COALESCE((jsonb_path_query_first(data, '$."after"."has_snapshot"'::jsonpath) #>> '{}'::text[]), (jsonb_path_query_first(data, '$."before"."has_snapshot"'::jsonpath) #>> '{}'::text[])))::boolean THEN (((('http://'::text || (source)::text) || ':5000/api/events/'::text) || COALESCE((jsonb_path_query_first(data, '$."after"."id"'::jsonpath) #>> '{}'::text[]), (jsonb_path_query_first(data, '$."before"."id"'::jsonpath) #>> '{}'::text[]))) || '/snapshot.jpg'::text)
            ELSE NULL::text
        END) AS media,
    COALESCE(to_timestamp(((jsonb_path_query_first(data, '$."after"."start_time"'::jsonpath) #>> '{}'::text[]))::double precision), to_timestamp(((jsonb_path_query_first(data, '$."before"."start_time"'::jsonpath) #>> '{}'::text[]))::double precision)) AS start_time,
    COALESCE(to_timestamp(((jsonb_path_query_first(data, '$."after"."end_time"'::jsonpath) #>> '{}'::text[]))::double precision), to_timestamp(((jsonb_path_query_first(data, '$."before"."end_time"'::jsonpath) #>> '{}'::text[]))::double precision)) AS end_time,
    created_at
   FROM data_events;;

/*!40103 SET TIME_ZONE=IFNULL(@OLD_TIME_ZONE, 'system') */;
/*!40101 SET SQL_MODE=IFNULL(@OLD_SQL_MODE, '') */;
/*!40014 SET FOREIGN_KEY_CHECKS=IFNULL(@OLD_FOREIGN_KEY_CHECKS, 1) */;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40111 SET SQL_NOTES=IFNULL(@OLD_SQL_NOTES, 1) */;
