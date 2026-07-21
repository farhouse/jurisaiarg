CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE search_chunks ADD COLUMN IF NOT EXISTS content_hash TEXT;
ALTER TABLE search_chunks ADD COLUMN IF NOT EXISTS embedding vector;
ALTER TABLE search_chunks ADD COLUMN IF NOT EXISTS embedding_model TEXT;
ALTER TABLE search_chunks ADD COLUMN IF NOT EXISTS embedding_version TEXT;
ALTER TABLE search_chunks ADD COLUMN IF NOT EXISTS embedded_at TIMESTAMPTZ;

UPDATE search_chunks
SET content_hash = encode(sha256(convert_to(content, 'UTF8')), 'hex')
WHERE content_hash IS NULL;

CREATE INDEX IF NOT EXISTS idx_legal_changes_search_filters
  ON legal_changes(change_type, affected_article_id, review_status);

COMMENT ON COLUMN search_chunks.embedding IS
  'Embedding sin dimensión fija; embedding_model y embedding_version identifican cómo regenerarlo.';
