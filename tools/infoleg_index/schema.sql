-- JurisAI · Infoleg core schema (PostgreSQL)

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
  status TEXT NOT NULL DEFAULT 'running', -- running|ok|error
  start_id INTEGER,
  end_id INTEGER,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS norms (
  id BIGSERIAL PRIMARY KEY,
  infoleg_id INTEGER NOT NULL UNIQUE,
  source_id BIGINT NOT NULL REFERENCES sources(id),
  url TEXT NOT NULL,
  tipo TEXT,
  numero TEXT,
  anio INTEGER,
  titulo TEXT,
  sumario TEXT,
  organismo TEXT,
  fecha_publicacion DATE,
  estado TEXT,
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_norms_numero ON norms(numero);
CREATE INDEX IF NOT EXISTS idx_norms_tipo ON norms(tipo);
CREATE INDEX IF NOT EXISTS idx_norms_fecha ON norms(fecha_publicacion);

CREATE TABLE IF NOT EXISTS raw_documents (
  id BIGSERIAL PRIMARY KEY,
  norm_id BIGINT NOT NULL REFERENCES norms(id) ON DELETE CASCADE,
  crawl_run_id BIGINT REFERENCES crawl_runs(id),
  http_status INTEGER,
  html_raw TEXT,
  sha256 TEXT,
  fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  error TEXT
);

CREATE INDEX IF NOT EXISTS idx_raw_documents_norm ON raw_documents(norm_id);

CREATE TABLE IF NOT EXISTS norm_articles (
  id BIGSERIAL PRIMARY KEY,
  norm_id BIGINT NOT NULL REFERENCES norms(id) ON DELETE CASCADE,
  article_number TEXT NOT NULL,
  article_text TEXT NOT NULL,
  ordinal INTEGER,
  UNIQUE(norm_id, article_number)
);

CREATE TABLE IF NOT EXISTS norm_relations (
  id BIGSERIAL PRIMARY KEY,
  source_norm_id BIGINT NOT NULL REFERENCES norms(id) ON DELETE CASCADE,
  target_norm_id BIGINT REFERENCES norms(id) ON DELETE SET NULL,
  target_infoleg_id INTEGER,
  relation_type TEXT NOT NULL,
  evidence_text TEXT,
  UNIQUE(source_norm_id, target_infoleg_id, relation_type)
);
