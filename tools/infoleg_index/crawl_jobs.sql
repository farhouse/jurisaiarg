CREATE TABLE IF NOT EXISTS crawl_jobs (
  id BIGSERIAL PRIMARY KEY,
  submission_id UUID NOT NULL UNIQUE,
  start_id INTEGER NOT NULL CHECK (start_id > 0),
  end_id INTEGER NOT NULL,
  requested_by TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued'
    CHECK (status IN ('queued','running','done','partial','failed')),
  stage TEXT NOT NULL DEFAULT 'queued',
  crawl_run_id BIGINT REFERENCES crawl_runs(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  error TEXT NOT NULL DEFAULT '',
  CHECK (end_id >= start_id AND end_id::bigint - start_id < 1000)
);
CREATE INDEX IF NOT EXISTS idx_crawl_jobs_queue ON crawl_jobs(id) WHERE status='queued';
-- El valor por defecto conserva el comportamiento de solicitudes históricas.
ALTER TABLE crawl_jobs ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'download'
  CHECK (mode IN ('download','full','process'));
ALTER TABLE crawl_jobs ADD COLUMN IF NOT EXISTS progress JSONB NOT NULL DEFAULT '{}';

ALTER TABLE crawl_jobs ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ;
ALTER TABLE crawl_jobs ADD COLUMN IF NOT EXISTS last_activity_at TIMESTAMPTZ;
