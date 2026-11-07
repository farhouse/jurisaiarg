-- Fragmentos conservados por contenido/modelo. Nunca sirven vectores de texto viejo.
CREATE TABLE IF NOT EXISTS semantic_passages (
  id BIGSERIAL PRIMARY KEY,
  chunk_id BIGINT NOT NULL REFERENCES search_chunks(id) ON DELETE CASCADE,
  source_hash TEXT NOT NULL,
  ordinal INTEGER NOT NULL,
  content TEXT NOT NULL,
  embedding vector(384) NOT NULL,
  model_revision TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(chunk_id,source_hash,ordinal,model_revision)
);
CREATE INDEX IF NOT EXISTS semantic_passages_chunk ON semantic_passages(chunk_id);
