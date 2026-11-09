-- Aislamiento: ninguna FK de resultados del piloto escribe en revisiones habituales.
ALTER TABLE crawl_jobs DROP CONSTRAINT IF EXISTS crawl_jobs_mode_check;
ALTER TABLE crawl_jobs ADD CONSTRAINT crawl_jobs_mode_check CHECK(mode IN ('download','full','process','pilot'));

CREATE TABLE IF NOT EXISTS analysis_pilots (
 id BIGSERIAL PRIMARY KEY,
 job_id BIGINT NOT NULL UNIQUE REFERENCES crawl_jobs(id),
 crawl_run_id BIGINT NOT NULL REFERENCES crawl_runs(id),
 model TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'preparing',
 config JSONB NOT NULL DEFAULT '{}',
 coverage JSONB NOT NULL DEFAULT '{}',
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 reference_locked_at TIMESTAMPTZ,
 phase TEXT NOT NULL DEFAULT 'prepare'
);
CREATE TABLE IF NOT EXISTS analysis_pilot_items (
 id BIGSERIAL PRIMARY KEY,
 pilot_id BIGINT NOT NULL REFERENCES analysis_pilots(id),
 norm_id BIGINT NOT NULL REFERENCES norms(id),
 snapshot JSONB NOT NULL,
 reference JSONB NOT NULL DEFAULT '{}',
 split TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending',
 result JSONB NOT NULL DEFAULT '{}',
 laya JSONB NOT NULL DEFAULT '{}',
 metadata JSONB NOT NULL DEFAULT '{}',
 error TEXT NOT NULL DEFAULT '',
 fingerprint TEXT NOT NULL,
 UNIQUE(pilot_id,norm_id)
);
CREATE TABLE IF NOT EXISTS analysis_pilot_evaluations (
 id BIGSERIAL PRIMARY KEY,
 item_id BIGINT NOT NULL REFERENCES analysis_pilot_items(id),
 phase TEXT NOT NULL CHECK(phase IN ('reference','result')),
 block TEXT NOT NULL,
 user_id BIGINT NOT NULL,
 username TEXT NOT NULL,
 decision TEXT NOT NULL CHECK(decision IN ('confirmed','corrected','legal','pending')),
 value JSONB NOT NULL,
 notes TEXT NOT NULL DEFAULT '',
 revision INTEGER NOT NULL,
 seconds DOUBLE PRECISION NOT NULL DEFAULT 0,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(item_id,phase,block,user_id,revision)
);
CREATE INDEX IF NOT EXISTS idx_pilot_evaluations_latest ON analysis_pilot_evaluations(item_id,phase,block,user_id,id DESC);
ALTER TABLE analysis_pilot_evaluations ADD COLUMN IF NOT EXISTS seconds DOUBLE PRECISION NOT NULL DEFAULT 0;
