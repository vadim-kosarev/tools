-- ========================================
-- Migration: Add data_hash column to data_events
-- Purpose: Exclude duplicate data entries using hash-based uniqueness
-- ========================================

-- Step 0: Enable pgcrypto extension (required for digest function)
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Step 1: Add data_hash column if it doesn't exist
ALTER TABLE "data_events"
ADD COLUMN IF NOT EXISTS "data_hash" TEXT NULL DEFAULT NULL;

-- Step 2: Backfill existing rows with SHA256 hash of their data
-- Only update rows where data is not null and data_hash is not yet filled
UPDATE "data_events"
SET "data_hash" = encode(digest("data"::text, 'sha256'), 'hex')
WHERE "data" IS NOT NULL
  AND "data_hash" IS NULL;

-- Step 3: Create unique index on data_hash to prevent duplicates
-- This index allows NULL values (PostgreSQL treats multiple NULLs as distinct)
CREATE UNIQUE INDEX IF NOT EXISTS "uq_data_events_data_hash"
ON "data_events" ("data_hash");

-- Optional: Create index on created_at for query optimization
CREATE INDEX IF NOT EXISTS "idx_data_events_created_at"
ON "data_events" ("created_at" DESC);

-- ========================================
-- Verification queries (run these to verify the migration)
-- ========================================
-- SELECT COUNT(*) as total_rows FROM "data_events";
-- SELECT COUNT(*) as unique_data_hashes FROM (SELECT DISTINCT "data_hash" FROM "data_events" WHERE "data_hash" IS NOT NULL) as t;
-- SELECT COUNT(*) as null_hashes FROM "data_events" WHERE "data_hash" IS NULL;
-- SELECT COUNT(*) as duplicate_count FROM "data_events" WHERE "data_hash" IN (SELECT "data_hash" FROM "data_events" WHERE "data_hash" IS NOT NULL GROUP BY "data_hash" HAVING COUNT(*) > 1);

