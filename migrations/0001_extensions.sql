-- 0001_extensions.sql
-- Extensions and shared helpers.
--
-- Supabase ships pgcrypto and vector; on a vanilla Postgres you need the
-- pgvector extension installed on the server before this runs.

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Every table with an updated_at column gets this trigger.
CREATE OR REPLACE FUNCTION pc_touch_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

-- Provenance of a piece of content. This is deliberately NOT a free-text
-- column: the difference between something the user said and something a
-- model inferred is a correctness boundary, not a label.
DO $$ BEGIN
    CREATE TYPE pc_provenance AS ENUM (
        'user_statement',
        'assistant_generated',
        'tool_result',
        'external_source',
        'imported_metadata',
        'system'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- Sensitivity drives whether content may leave the system (embeddings API,
-- external classifier, Obsidian promotion).
DO $$ BEGIN
    CREATE TYPE pc_sensitivity AS ENUM ('normal', 'private', 'highly_sensitive');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- Whether we actually hold the bytes. See README "artifact capture limits".
DO $$ BEGIN
    CREATE TYPE pc_storage_status AS ENUM (
        'captured',          -- bytes stored AND sha256 verified
        'referenced',        -- we know it existed; we do NOT have the bytes
        'pending_import',    -- expected to arrive via a provider export
        'failed'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE pc_classification_status AS ENUM (
        'unclassified',
        'pending',
        'classified',
        'needs_review',
        'failed'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
