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

-- Dumping structure for function public.armor
DELIMITER //
CREATE FUNCTION "armor"() RETURNS TEXT AS $$ pg_armor $$//
DELIMITER ;

-- Dumping structure for function public.armor
DELIMITER //
CREATE FUNCTION "armor"() RETURNS TEXT AS $$ pg_armor $$//
DELIMITER ;

-- Dumping structure for function public.crypt
DELIMITER //
CREATE FUNCTION "crypt"() RETURNS TEXT AS $$ pg_crypt $$//
DELIMITER ;

-- Dumping structure for function public.dearmor
DELIMITER //
CREATE FUNCTION "dearmor"() RETURNS BYTEA AS $$ pg_dearmor $$//
DELIMITER ;

-- Dumping structure for function public.decrypt
DELIMITER //
CREATE FUNCTION "decrypt"() RETURNS BYTEA AS $$ pg_decrypt $$//
DELIMITER ;

-- Dumping structure for function public.decrypt_iv
DELIMITER //
CREATE FUNCTION "decrypt_iv"() RETURNS BYTEA AS $$ pg_decrypt_iv $$//
DELIMITER ;

-- Dumping structure for function public.digest
DELIMITER //
CREATE FUNCTION "digest"() RETURNS BYTEA AS $$ pg_digest $$//
DELIMITER ;

-- Dumping structure for function public.digest
DELIMITER //
CREATE FUNCTION "digest"() RETURNS BYTEA AS $$ pg_digest $$//
DELIMITER ;

-- Dumping structure for function public.encrypt
DELIMITER //
CREATE FUNCTION "encrypt"() RETURNS BYTEA AS $$ pg_encrypt $$//
DELIMITER ;

-- Dumping structure for function public.encrypt_iv
DELIMITER //
CREATE FUNCTION "encrypt_iv"() RETURNS BYTEA AS $$ pg_encrypt_iv $$//
DELIMITER ;

-- Dumping structure for function public.gen_random_bytes
DELIMITER //
CREATE FUNCTION "gen_random_bytes"() RETURNS BYTEA AS $$ pg_random_bytes $$//
DELIMITER ;

-- Dumping structure for function public.gen_random_uuid
DELIMITER //
CREATE FUNCTION "gen_random_uuid"() RETURNS UUID AS $$ pg_random_uuid $$//
DELIMITER ;

-- Dumping structure for function public.gen_salt
DELIMITER //
CREATE FUNCTION "gen_salt"() RETURNS TEXT AS $$ pg_gen_salt $$//
DELIMITER ;

-- Dumping structure for function public.gen_salt
DELIMITER //
CREATE FUNCTION "gen_salt"() RETURNS TEXT AS $$ pg_gen_salt_rounds $$//
DELIMITER ;

-- Dumping structure for function public.hmac
DELIMITER //
CREATE FUNCTION "hmac"() RETURNS BYTEA AS $$ pg_hmac $$//
DELIMITER ;

-- Dumping structure for function public.hmac
DELIMITER //
CREATE FUNCTION "hmac"() RETURNS BYTEA AS $$ pg_hmac $$//
DELIMITER ;

-- Dumping structure for function public.pgp_armor_headers
DELIMITER //
CREATE FUNCTION "pgp_armor_headers"("" TEXT, key , value ) RETURNS UNKNOWN AS $$ pgp_armor_headers $$//
DELIMITER ;

-- Dumping structure for function public.pgp_key_id
DELIMITER //
CREATE FUNCTION "pgp_key_id"() RETURNS TEXT AS $$ pgp_key_id_w $$//
DELIMITER ;

-- Dumping structure for function public.pgp_pub_decrypt
DELIMITER //
CREATE FUNCTION "pgp_pub_decrypt"() RETURNS TEXT AS $$ pgp_pub_decrypt_text $$//
DELIMITER ;

-- Dumping structure for function public.pgp_pub_decrypt
DELIMITER //
CREATE FUNCTION "pgp_pub_decrypt"() RETURNS TEXT AS $$ pgp_pub_decrypt_text $$//
DELIMITER ;

-- Dumping structure for function public.pgp_pub_decrypt
DELIMITER //
CREATE FUNCTION "pgp_pub_decrypt"() RETURNS TEXT AS $$ pgp_pub_decrypt_text $$//
DELIMITER ;

-- Dumping structure for function public.pgp_pub_decrypt_bytea
DELIMITER //
CREATE FUNCTION "pgp_pub_decrypt_bytea"() RETURNS BYTEA AS $$ pgp_pub_decrypt_bytea $$//
DELIMITER ;

-- Dumping structure for function public.pgp_pub_decrypt_bytea
DELIMITER //
CREATE FUNCTION "pgp_pub_decrypt_bytea"() RETURNS BYTEA AS $$ pgp_pub_decrypt_bytea $$//
DELIMITER ;

-- Dumping structure for function public.pgp_pub_decrypt_bytea
DELIMITER //
CREATE FUNCTION "pgp_pub_decrypt_bytea"() RETURNS BYTEA AS $$ pgp_pub_decrypt_bytea $$//
DELIMITER ;

-- Dumping structure for function public.pgp_pub_encrypt
DELIMITER //
CREATE FUNCTION "pgp_pub_encrypt"() RETURNS BYTEA AS $$ pgp_pub_encrypt_text $$//
DELIMITER ;

-- Dumping structure for function public.pgp_pub_encrypt
DELIMITER //
CREATE FUNCTION "pgp_pub_encrypt"() RETURNS BYTEA AS $$ pgp_pub_encrypt_text $$//
DELIMITER ;

-- Dumping structure for function public.pgp_pub_encrypt_bytea
DELIMITER //
CREATE FUNCTION "pgp_pub_encrypt_bytea"() RETURNS BYTEA AS $$ pgp_pub_encrypt_bytea $$//
DELIMITER ;

-- Dumping structure for function public.pgp_pub_encrypt_bytea
DELIMITER //
CREATE FUNCTION "pgp_pub_encrypt_bytea"() RETURNS BYTEA AS $$ pgp_pub_encrypt_bytea $$//
DELIMITER ;

-- Dumping structure for function public.pgp_sym_decrypt
DELIMITER //
CREATE FUNCTION "pgp_sym_decrypt"() RETURNS TEXT AS $$ pgp_sym_decrypt_text $$//
DELIMITER ;

-- Dumping structure for function public.pgp_sym_decrypt
DELIMITER //
CREATE FUNCTION "pgp_sym_decrypt"() RETURNS TEXT AS $$ pgp_sym_decrypt_text $$//
DELIMITER ;

-- Dumping structure for function public.pgp_sym_decrypt_bytea
DELIMITER //
CREATE FUNCTION "pgp_sym_decrypt_bytea"() RETURNS BYTEA AS $$ pgp_sym_decrypt_bytea $$//
DELIMITER ;

-- Dumping structure for function public.pgp_sym_decrypt_bytea
DELIMITER //
CREATE FUNCTION "pgp_sym_decrypt_bytea"() RETURNS BYTEA AS $$ pgp_sym_decrypt_bytea $$//
DELIMITER ;

-- Dumping structure for function public.pgp_sym_encrypt
DELIMITER //
CREATE FUNCTION "pgp_sym_encrypt"() RETURNS BYTEA AS $$ pgp_sym_encrypt_text $$//
DELIMITER ;

-- Dumping structure for function public.pgp_sym_encrypt
DELIMITER //
CREATE FUNCTION "pgp_sym_encrypt"() RETURNS BYTEA AS $$ pgp_sym_encrypt_text $$//
DELIMITER ;

-- Dumping structure for function public.pgp_sym_encrypt_bytea
DELIMITER //
CREATE FUNCTION "pgp_sym_encrypt_bytea"() RETURNS BYTEA AS $$ pgp_sym_encrypt_bytea $$//
DELIMITER ;

-- Dumping structure for function public.pgp_sym_encrypt_bytea
DELIMITER //
CREATE FUNCTION "pgp_sym_encrypt_bytea"() RETURNS BYTEA AS $$ pgp_sym_encrypt_bytea $$//
DELIMITER ;

-- Dumping structure for table public.data_anpr
CREATE TABLE IF NOT EXISTS "data_anpr" (
	"event_id" BIGINT NOT NULL,
	"status" VARCHAR(16) NULL DEFAULT 'PENDING',
	"result" JSONB NULL DEFAULT NULL,
	PRIMARY KEY ("event_id"),
	CONSTRAINT "id_fk" FOREIGN KEY ("event_id") REFERENCES "data_events" ("id") ON UPDATE NO ACTION ON DELETE CASCADE
);

-- Data exporting was unselected.

-- Dumping structure for table public.data_events
CREATE TABLE IF NOT EXISTS "data_events" (
	"id" SERIAL NOT NULL,
	"created_at" TIMESTAMPTZ NULL DEFAULT now(),
	"data" JSONB NULL DEFAULT NULL,
	"source" VARCHAR NULL DEFAULT NULL,
	"frigate_id" VARCHAR NULL DEFAULT NULL,
	"data_hash" TEXT NOT NULL,
	PRIMARY KEY ("id"),
	INDEX "idx_events_source" ("source"),
	UNIQUE INDEX "idx_frigate_id" ("frigate_id"),
	INDEX "idx_data_events_created_at" ("created_at"),
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
	"thumbnail_url" TEXT NULL,
	"start_time" TIMESTAMPTZ NULL,
	"start_date_date" DATE NULL,
	"start_date_month" TEXT NULL,
	"start_date_dayofweek" TEXT NULL,
	"plates" TEXT NULL,
	"status" VARCHAR(16) NULL,
	"result" JSONB NULL
) ENGINE=MyISAM;

-- Removing temporary table and create final VIEW structure
DROP TABLE IF EXISTS "view_events_media";
CREATE VIEW "view_events_media" AS  SELECT de.id,
    (jsonb_path_query_first(de.data, '$."type"'::jsonpath) #>> '{}'::text[]) AS type,
    COALESCE((jsonb_path_query_first(de.data, '$."after"."camera"'::jsonpath) #>> '{}'::text[]), (jsonb_path_query_first(de.data, '$."before"."camera"'::jsonpath) #>> '{}'::text[])) AS camera,
    COALESCE((jsonb_path_query_first(de.data, '$."after"."label"'::jsonpath) #>> '{}'::text[]), (jsonb_path_query_first(de.data, '$."before"."label"'::jsonpath) #>> '{}'::text[])) AS label,
    COALESCE((jsonb_path_query_first(de.data, '$."after"."id"'::jsonpath) #>> '{}'::text[]), (jsonb_path_query_first(de.data, '$."before"."id"'::jsonpath) #>> '{}'::text[])) AS event_id,
    de.source,
    COALESCE(((jsonb_path_query_first(de.data, '$."after"."has_clip"'::jsonpath) #>> '{}'::text[]))::boolean, ((jsonb_path_query_first(de.data, '$."before"."has_clip"'::jsonpath) #>> '{}'::text[]))::boolean) AS has_clip,
    concat_ws(', '::text,
        CASE
            WHEN COALESCE(((jsonb_path_query_first(de.data, '$."after"."has_clip"'::jsonpath) #>> '{}'::text[]))::boolean, ((jsonb_path_query_first(de.data, '$."before"."has_clip"'::jsonpath) #>> '{}'::text[]))::boolean) THEN (((('http://'::text || (de.source)::text) || ':5000/api/events/'::text) || COALESCE((jsonb_path_query_first(de.data, '$."after"."id"'::jsonpath) #>> '{}'::text[]), (jsonb_path_query_first(de.data, '$."before"."id"'::jsonpath) #>> '{}'::text[]))) || '/clip.mp4'::text)
            ELSE NULL::text
        END,
        CASE
            WHEN COALESCE(((jsonb_path_query_first(de.data, '$."after"."has_snapshot"'::jsonpath) #>> '{}'::text[]))::boolean, ((jsonb_path_query_first(de.data, '$."before"."has_snapshot"'::jsonpath) #>> '{}'::text[]))::boolean) THEN (((('http://'::text || (de.source)::text) || ':5000/api/events/'::text) || COALESCE((jsonb_path_query_first(de.data, '$."after"."id"'::jsonpath) #>> '{}'::text[]), (jsonb_path_query_first(de.data, '$."before"."id"'::jsonpath) #>> '{}'::text[]))) || '/snapshot.jpg'::text)
            ELSE NULL::text
        END) AS media,
    (((('http://'::text || (de.source)::text) || ':5000/api/events/'::text) || ((de.data -> 'after'::text) ->> 'id'::text)) || '/thumbnail.jpg'::text) AS thumbnail_url,
    COALESCE(to_timestamp(((jsonb_path_query_first(de.data, '$."after"."start_time"'::jsonpath) #>> '{}'::text[]))::double precision), to_timestamp(((jsonb_path_query_first(de.data, '$."before"."start_time"'::jsonpath) #>> '{}'::text[]))::double precision)) AS start_time,
    (COALESCE(to_timestamp(((jsonb_path_query_first(de.data, '$."after"."start_time"'::jsonpath) #>> '{}'::text[]))::double precision), to_timestamp(((jsonb_path_query_first(de.data, '$."before"."start_time"'::jsonpath) #>> '{}'::text[]))::double precision)))::date AS start_date_date,
    to_char(COALESCE(to_timestamp(((jsonb_path_query_first(de.data, '$."after"."start_time"'::jsonpath) #>> '{}'::text[]))::double precision), to_timestamp(((jsonb_path_query_first(de.data, '$."before"."start_time"'::jsonpath) #>> '{}'::text[]))::double precision)), 'YYYY-MM'::text) AS start_date_month,
    to_char(COALESCE(to_timestamp(((jsonb_path_query_first(de.data, '$."after"."start_time"'::jsonpath) #>> '{}'::text[]))::double precision), to_timestamp(((jsonb_path_query_first(de.data, '$."before"."start_time"'::jsonpath) #>> '{}'::text[]))::double precision)), 'Dy'::text) AS start_date_dayofweek,
    ( SELECT string_agg(jsonb_array_elements_text.value, ', '::text) AS string_agg
           FROM jsonb_array_elements_text((anpr.result -> 'plates'::text)) jsonb_array_elements_text(value)) AS plates,
    anpr.status,
    anpr.result
   FROM (data_events de
     LEFT JOIN data_anpr anpr ON ((de.id = anpr.event_id)));;

/*!40103 SET TIME_ZONE=IFNULL(@OLD_TIME_ZONE, 'system') */;
/*!40101 SET SQL_MODE=IFNULL(@OLD_SQL_MODE, '') */;
/*!40014 SET FOREIGN_KEY_CHECKS=IFNULL(@OLD_FOREIGN_KEY_CHECKS, 1) */;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40111 SET SQL_NOTES=IFNULL(@OLD_SQL_NOTES, 1) */;
