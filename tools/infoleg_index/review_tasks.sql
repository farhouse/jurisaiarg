-- Cola operativa independiente de la validación jurídica histórica.
ALTER TABLE crawl_runs ADD COLUMN IF NOT EXISTS review_prepared_at TIMESTAMPTZ;
CREATE TABLE IF NOT EXISTS review_tasks (
  id BIGSERIAL PRIMARY KEY,
  crawl_run_id BIGINT NOT NULL REFERENCES crawl_runs(id),
  norm_id BIGINT NOT NULL REFERENCES norms(id),
  article_version_id BIGINT REFERENCES article_versions(id),
  legal_change_id BIGINT REFERENCES legal_changes(id),
  kind TEXT NOT NULL CHECK (kind IN ('indice','pasaje')),
  source_text TEXT NOT NULL,
  decision TEXT NOT NULL CHECK (decision IN ('revision','aceptacion_automatica','sin_cambio_detectado')),
  prediction TEXT,
  probability DOUBLE PRECISION CHECK (probability BETWEEN 0 AND 1),
  reasons JSONB NOT NULL DEFAULT '[]',
  model_result JSONB NOT NULL DEFAULT '{}',
  fingerprint TEXT NOT NULL UNIQUE,
  review_status TEXT NOT NULL DEFAULT 'pendiente' CHECK (review_status IN ('pendiente','validada','rechazada')),
  review_notes TEXT NOT NULL DEFAULT '',
  reviewed_by TEXT,
  reviewed_at TIMESTAMPTZ,
  revision INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_review_tasks_queue ON review_tasks(crawl_run_id,review_status,decision,kind);
CREATE TABLE IF NOT EXISTS review_events (
  id BIGSERIAL PRIMARY KEY,
  task_id BIGINT NOT NULL REFERENCES review_tasks(id),
  username TEXT NOT NULL,
  previous_status TEXT NOT NULL,
  status TEXT NOT NULL,
  notes TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Un control de índice por norma y lote; los pasajes no son votos de la norma.
CREATE UNIQUE INDEX IF NOT EXISTS idx_review_index_per_batch
  ON review_tasks(crawl_run_id,norm_id) WHERE kind='indice';
CREATE TABLE IF NOT EXISTS norm_reviews (
  id BIGSERIAL PRIMARY KEY,
  task_id BIGINT NOT NULL REFERENCES review_tasks(id),
  -- ID estable de Django. El SQL de arranque precede a las tablas auth.
  user_id BIGINT NOT NULL,
  username TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pendiente','validada','rechazada')),
  notes TEXT NOT NULL DEFAULT '',
  revision INTEGER NOT NULL DEFAULT 1,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(task_id,user_id)
);

-- Controles de copia por persona, con evidencia del control en el historial.
ALTER TABLE norm_reviews ADD COLUMN IF NOT EXISTS checks JSONB NOT NULL DEFAULT '{}';
ALTER TABLE review_events ADD COLUMN IF NOT EXISTS details JSONB NOT NULL DEFAULT '{}';
ALTER TABLE review_tasks ADD COLUMN IF NOT EXISTS proposed_correction JSONB NOT NULL DEFAULT '{}';
