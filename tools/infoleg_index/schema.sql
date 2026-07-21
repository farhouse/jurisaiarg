-- JurisAI · archivo normativo argentino (PostgreSQL)

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS sources (
  id BIGSERIAL PRIMARY KEY,
  code TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  base_url TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS crawl_runs (
  id BIGSERIAL PRIMARY KEY,
  source_id BIGINT NOT NULL REFERENCES sources(id),
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  status TEXT NOT NULL DEFAULT 'running'
    CHECK (status IN ('running', 'ok', 'partial', 'error')),
  requested_ids INTEGER[],
  processed_count INTEGER NOT NULL DEFAULT 0,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS norms (
  id BIGSERIAL PRIMARY KEY,
  jurisdiction TEXT NOT NULL DEFAULT 'nacional',
  province_code TEXT,
  norm_type TEXT,
  number TEXT,
  year INTEGER,
  canonical_title TEXT,
  popular_name TEXT,
  issuing_body TEXT,
  subject TEXT,
  summary TEXT,
  general_status TEXT NOT NULL DEFAULT 'desconocida'
    CHECK (general_status IN ('vigente', 'derogada', 'vigencia_parcial', 'desconocida')),
  index_status TEXT NOT NULL DEFAULT 'pendiente'
    CHECK (index_status IN ('pendiente', 'completa', 'solo_texto_original',
      'solo_ficha', 'vacia', 'error_descarga', 'error_parseo')),
  has_original_text BOOLEAN NOT NULL DEFAULT false,
  has_current_text BOOLEAN NOT NULL DEFAULT false,
  has_outgoing_relations BOOLEAN NOT NULL DEFAULT false,
  has_incoming_relations BOOLEAN NOT NULL DEFAULT false,
  article_count INTEGER NOT NULL DEFAULT 0 CHECK (article_count >= 0),
  infoleg_id INTEGER UNIQUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Compatibilidad con volúmenes creados por versiones anteriores del esquema.
ALTER TABLE norms ADD COLUMN IF NOT EXISTS index_status TEXT NOT NULL DEFAULT 'pendiente';
ALTER TABLE norms ADD COLUMN IF NOT EXISTS has_original_text BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE norms ADD COLUMN IF NOT EXISTS has_current_text BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE norms ADD COLUMN IF NOT EXISTS has_outgoing_relations BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE norms ADD COLUMN IF NOT EXISTS has_incoming_relations BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE norms ADD COLUMN IF NOT EXISTS article_count INTEGER NOT NULL DEFAULT 0;

UPDATE norms SET
  canonical_title = NULLIF(btrim(canonical_title), ''),
  popular_name = NULLIF(btrim(popular_name), ''),
  issuing_body = NULLIF(btrim(issuing_body), ''),
  subject = NULLIF(btrim(subject), ''),
  summary = NULLIF(btrim(summary), '');

CREATE INDEX IF NOT EXISTS idx_norms_identity
  ON norms(jurisdiction, norm_type, number, year);
CREATE INDEX IF NOT EXISTS idx_norms_number ON norms(number);
CREATE INDEX IF NOT EXISTS idx_norms_title_trgm
  ON norms USING gin(canonical_title gin_trgm_ops);

CREATE TABLE IF NOT EXISTS norm_dates (
  id BIGSERIAL PRIMARY KEY,
  norm_id BIGINT NOT NULL REFERENCES norms(id) ON DELETE CASCADE,
  date_type TEXT NOT NULL
    CHECK (date_type IN ('sancion', 'promulgacion', 'publicacion', 'entrada_vigencia', 'derogacion')),
  date_value DATE NOT NULL,
  precision TEXT NOT NULL DEFAULT 'exacta'
    CHECK (precision IN ('exacta', 'mes', 'anio', 'inferida')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(norm_id, date_type, date_value)
);

CREATE TABLE IF NOT EXISTS norm_sources (
  id BIGSERIAL PRIMARY KEY,
  norm_id BIGINT NOT NULL REFERENCES norms(id) ON DELETE CASCADE,
  crawl_run_id BIGINT REFERENCES crawl_runs(id),
  provider TEXT NOT NULL DEFAULT 'infoleg',
  external_id TEXT,
  url TEXT NOT NULL,
  document_kind TEXT NOT NULL
    CHECK (document_kind IN ('ficha', 'texto_original', 'texto_actualizado', 'vinculos_modifica', 'vinculos_modificada_por', 'pdf', 'html')),
  retrieved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  http_status INTEGER,
  content_hash TEXT,
  raw_content_bytes BYTEA,
  raw_content TEXT,
  parser_version TEXT,
  parse_status TEXT NOT NULL DEFAULT 'pendiente'
    CHECK (parse_status IN ('pendiente', 'ok', 'parcial', 'error')),
  error_detail TEXT
);

ALTER TABLE norm_sources ADD COLUMN IF NOT EXISTS raw_content_bytes BYTEA;

CREATE TABLE IF NOT EXISTS crawl_items (
  id BIGSERIAL PRIMARY KEY,
  crawl_run_id BIGINT NOT NULL REFERENCES crawl_runs(id) ON DELETE CASCADE,
  infoleg_id INTEGER NOT NULL,
  norm_id BIGINT REFERENCES norms(id) ON DELETE SET NULL,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  status TEXT NOT NULL DEFAULT 'pendiente'
    CHECK (status IN ('pendiente', 'procesando', 'completa',
      'solo_texto_original', 'solo_ficha', 'vacia', 'error_descarga',
      'error_parseo')),
  error_detail TEXT,
  UNIQUE(crawl_run_id, infoleg_id)
);

CREATE INDEX IF NOT EXISTS idx_crawl_items_run ON crawl_items(crawl_run_id);

CREATE INDEX IF NOT EXISTS idx_norm_sources_norm ON norm_sources(norm_id);
CREATE INDEX IF NOT EXISTS idx_norm_sources_hash ON norm_sources(content_hash);

CREATE TABLE IF NOT EXISTS norm_versions (
  id BIGSERIAL PRIMARY KEY,
  norm_id BIGINT NOT NULL REFERENCES norms(id) ON DELETE CASCADE,
  source_id BIGINT NOT NULL REFERENCES norm_sources(id) ON DELETE CASCADE,
  version_type TEXT NOT NULL
    CHECK (version_type IN ('original', 'actualizada', 'historica', 'desconocida')),
  valid_from DATE,
  valid_to DATE,
  full_text_original TEXT,
  full_text_normalized TEXT,
  text_hash TEXT NOT NULL,
  is_current BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(norm_id, version_type, text_hash)
);

CREATE TABLE IF NOT EXISTS articles (
  id BIGSERIAL PRIMARY KEY,
  norm_id BIGINT NOT NULL REFERENCES norms(id) ON DELETE CASCADE,
  article_label TEXT NOT NULL,
  article_key TEXT NOT NULL,
  ordinal INTEGER,
  heading TEXT,
  UNIQUE(norm_id, article_key)
);

CREATE TABLE IF NOT EXISTS article_versions (
  id BIGSERIAL PRIMARY KEY,
  article_id BIGINT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
  norm_version_id BIGINT NOT NULL REFERENCES norm_versions(id) ON DELETE CASCADE,
  text_original TEXT NOT NULL,
  text_normalized TEXT NOT NULL,
  valid_from DATE,
  valid_to DATE,
  status TEXT NOT NULL DEFAULT 'desconocido'
    CHECK (status IN ('vigente', 'sustituido', 'derogado', 'desconocido')),
  text_hash TEXT NOT NULL,
  UNIQUE(article_id, norm_version_id)
);

CREATE TABLE IF NOT EXISTS norm_relations (
  id BIGSERIAL PRIMARY KEY,
  source_norm_id BIGINT NOT NULL REFERENCES norms(id) ON DELETE CASCADE,
  target_norm_id BIGINT NOT NULL REFERENCES norms(id) ON DELETE CASCADE,
  source_article_id BIGINT REFERENCES articles(id) ON DELETE SET NULL,
  target_article_id BIGINT REFERENCES articles(id) ON DELETE SET NULL,
  relation_type TEXT NOT NULL,
  effect_date DATE,
  scope_text TEXT,
  evidence_text TEXT NOT NULL,
  extraction_method TEXT NOT NULL DEFAULT 'fuente'
    CHECK (extraction_method IN ('fuente', 'regla', 'llm', 'manual')),
  confidence NUMERIC(4,3) CHECK (confidence BETWEEN 0 AND 1),
  review_status TEXT NOT NULL DEFAULT 'pendiente'
    CHECK (review_status IN ('pendiente', 'validada', 'rechazada')),
  source_page_id BIGINT REFERENCES norm_sources(id) ON DELETE SET NULL,
  UNIQUE(source_norm_id, target_norm_id, relation_type)
);

CREATE TABLE IF NOT EXISTS search_chunks (
  id BIGSERIAL PRIMARY KEY,
  norm_id BIGINT NOT NULL REFERENCES norms(id) ON DELETE CASCADE,
  article_version_id BIGINT REFERENCES article_versions(id) ON DELETE CASCADE,
  chunk_type TEXT NOT NULL CHECK (chunk_type IN ('titulo', 'sumario', 'articulo', 'relacion')),
  content TEXT NOT NULL,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  content_hash TEXT,
  embedding vector,
  embedding_model TEXT,
  embedding_version TEXT,
  embedded_at TIMESTAMPTZ,
  search_vector TSVECTOR GENERATED ALWAYS AS
    (to_tsvector('spanish', coalesce(content, ''))) STORED,
  UNIQUE(norm_id, article_version_id, chunk_type)
);

ALTER TABLE search_chunks ADD COLUMN IF NOT EXISTS content_hash TEXT;
ALTER TABLE search_chunks ADD COLUMN IF NOT EXISTS embedding vector;
ALTER TABLE search_chunks ADD COLUMN IF NOT EXISTS embedding_model TEXT;
ALTER TABLE search_chunks ADD COLUMN IF NOT EXISTS embedding_version TEXT;
ALTER TABLE search_chunks ADD COLUMN IF NOT EXISTS embedded_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_search_chunks_fts
  ON search_chunks USING gin(search_vector);
CREATE UNIQUE INDEX IF NOT EXISTS idx_search_chunks_norm_level_unique
  ON search_chunks(norm_id, chunk_type)
  WHERE article_version_id IS NULL;

CREATE TABLE IF NOT EXISTS legal_parse_runs (
  id BIGSERIAL PRIMARY KEY,
  parser_version TEXT NOT NULL,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  status TEXT NOT NULL DEFAULT 'running'
    CHECK (status IN ('running', 'ok', 'error')),
  scanned_articles INTEGER NOT NULL DEFAULT 0,
  detected_changes INTEGER NOT NULL DEFAULT 0,
  resolved_norms INTEGER NOT NULL DEFAULT 0,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS legal_changes (
  id BIGSERIAL PRIMARY KEY,
  parse_run_id BIGINT REFERENCES legal_parse_runs(id) ON DELETE SET NULL,
  amending_norm_id BIGINT REFERENCES norms(id) ON DELETE SET NULL,
  affected_norm_id BIGINT NOT NULL REFERENCES norms(id) ON DELETE CASCADE,
  affected_article_id BIGINT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
  source_article_version_id BIGINT NOT NULL REFERENCES article_versions(id) ON DELETE CASCADE,
  amending_reference_raw TEXT NOT NULL,
  amending_article_reference TEXT,
  change_type TEXT NOT NULL
    CHECK (change_type IN ('sustituye', 'incorpora', 'deroga', 'modifica')),
  affected_scope TEXT NOT NULL
    CHECK (affected_scope IN ('articulo', 'inciso', 'parrafo', 'capitulo',
      'titulo', 'expresion', 'desconocido')),
  publication_date DATE,
  effect_date DATE,
  evidence_text TEXT NOT NULL,
  extraction_method TEXT NOT NULL DEFAULT 'regla'
    CHECK (extraction_method IN ('regla', 'llm', 'manual')),
  parser_version TEXT NOT NULL,
  confidence NUMERIC(4,3) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  review_status TEXT NOT NULL DEFAULT 'pendiente'
    CHECK (review_status IN ('pendiente', 'validada', 'rechazada')),
  reviewed_at TIMESTAMPTZ,
  reviewed_by TEXT,
  review_notes TEXT,
  fingerprint TEXT NOT NULL UNIQUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE legal_changes ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ;
ALTER TABLE legal_changes ADD COLUMN IF NOT EXISTS reviewed_by TEXT;
ALTER TABLE legal_changes ADD COLUMN IF NOT EXISTS review_notes TEXT;

CREATE INDEX IF NOT EXISTS idx_legal_changes_affected
  ON legal_changes(affected_norm_id, affected_article_id);
CREATE INDEX IF NOT EXISTS idx_legal_changes_amending
  ON legal_changes(amending_norm_id);
CREATE INDEX IF NOT EXISTS idx_legal_changes_review
  ON legal_changes(review_status, confidence);
CREATE INDEX IF NOT EXISTS idx_legal_changes_search_filters
  ON legal_changes(change_type, affected_article_id, review_status);

INSERT INTO sources(code, name, base_url)
VALUES ('infoleg', 'InfoLEG', 'https://servicios.infoleg.gob.ar/infolegInternet/')
ON CONFLICT(code) DO UPDATE SET
  name = EXCLUDED.name,
  base_url = EXCLUDED.base_url;
