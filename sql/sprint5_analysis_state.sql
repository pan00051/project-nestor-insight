-- Sprint 5 M5.1: persistent analysis state and cross-source content identity.
-- Safe to run more than once in the Supabase SQL Editor.

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA extensions;

ALTER TABLE articles
ADD COLUMN IF NOT EXISTS analysis_status TEXT,
ADD COLUMN IF NOT EXISTS relevance_score INTEGER,
ADD COLUMN IF NOT EXISTS skip_reason TEXT,
ADD COLUMN IF NOT EXISTS analysis_attempts INTEGER,
ADD COLUMN IF NOT EXISTS analysis_error TEXT,
ADD COLUMN IF NOT EXISTS analysis_attempted_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS content_hash TEXT;

UPDATE articles
SET analysis_status = CASE
    WHEN analyzed_at IS NOT NULL AND signal_type IS NOT NULL THEN 'analyzed'
    ELSE 'pending'
END
WHERE analysis_status IS NULL;

UPDATE articles
SET analysis_attempts = CASE
    WHEN analyzed_at IS NOT NULL THEN 1
    ELSE 0
END
WHERE analysis_attempts IS NULL;

ALTER TABLE articles
ALTER COLUMN analysis_status SET DEFAULT 'pending',
ALTER COLUMN analysis_status SET NOT NULL,
ALTER COLUMN analysis_attempts SET DEFAULT 0,
ALTER COLUMN analysis_attempts SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'articles_analysis_status_check'
          AND conrelid = 'articles'::regclass
    ) THEN
        ALTER TABLE articles
        ADD CONSTRAINT articles_analysis_status_check
        CHECK (analysis_status IN ('pending', 'analyzed', 'skipped', 'failed'));
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'articles_relevance_score_check'
          AND conrelid = 'articles'::regclass
    ) THEN
        ALTER TABLE articles
        ADD CONSTRAINT articles_relevance_score_check
        CHECK (relevance_score IS NULL OR relevance_score >= 0);
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'articles_analysis_attempts_check'
          AND conrelid = 'articles'::regclass
    ) THEN
        ALTER TABLE articles
        ADD CONSTRAINT articles_analysis_attempts_check
        CHECK (analysis_attempts >= 0);
    END IF;
END
$$;

-- Match the collector's SHA-256 input:
-- lowercase(normalized title) + "|" + UTC publication date.
-- Historical exact-title/date duplicates remain untouched except that only
-- the oldest row receives a hash, allowing the unique index to be created.
WITH hash_candidates AS (
    SELECT
        id,
        lower(regexp_replace(btrim(title), '\s+', ' ', 'g'))
            || '|'
            || COALESCE(
                (published_at AT TIME ZONE 'UTC')::date::text,
                ''
            ) AS hash_input
    FROM articles
    WHERE title IS NOT NULL
      AND btrim(title) <> ''
),
ranked_hashes AS (
    SELECT
        id,
        encode(
            extensions.digest(hash_input, 'sha256'),
            'hex'
        ) AS generated_hash,
        row_number() OVER (
            PARTITION BY hash_input
            ORDER BY id
        ) AS duplicate_rank
    FROM hash_candidates
)
UPDATE articles AS article
SET content_hash = ranked.generated_hash
FROM ranked_hashes AS ranked
WHERE article.id = ranked.id
  AND article.content_hash IS NULL
  AND ranked.duplicate_rank = 1;

CREATE UNIQUE INDEX IF NOT EXISTS idx_articles_content_hash
ON articles(content_hash)
WHERE content_hash IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_articles_analysis_queue
ON articles(analysis_status, id)
WHERE analysis_status IN ('pending', 'failed');

CREATE INDEX IF NOT EXISTS idx_articles_relevance_score
ON articles(relevance_score DESC);

COMMENT ON COLUMN articles.analysis_status IS
'Pipeline state: pending, analyzed, skipped, or failed.';

COMMENT ON COLUMN articles.relevance_score IS
'Local AI-industry relevance score computed before paid analysis.';

COMMENT ON COLUMN articles.content_hash IS
'SHA-256 of normalized title and UTC publication date for cross-source deduplication.';
