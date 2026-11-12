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

CREATE TABLE IF NOT EXISTS review_cards (
 id BIGSERIAL PRIMARY KEY,
 item_id BIGINT NOT NULL REFERENCES analysis_pilot_items(id),
 kind TEXT NOT NULL CHECK(kind IN ('topic','summary')),
 fingerprint TEXT NOT NULL,
 snapshot JSONB NOT NULL,
 proposal JSONB NOT NULL,
 active BOOLEAN NOT NULL DEFAULT true,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(item_id,fingerprint)
);
CREATE TABLE IF NOT EXISTS card_responses (
 id BIGSERIAL PRIMARY KEY,
 card_id BIGINT NOT NULL REFERENCES review_cards(id),
 user_id BIGINT NOT NULL,
 username TEXT NOT NULL,
 action TEXT NOT NULL CHECK(action IN ('answer','undo','note')),
 decision TEXT NOT NULL DEFAULT '',
 answer_id BIGINT REFERENCES card_responses(id),
 notes TEXT NOT NULL DEFAULT '',
 request_id UUID NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(user_id,request_id),
 CHECK((action='answer' AND answer_id IS NULL AND decision IN ('correct','incorrect','skip'))
    OR (action IN ('undo','note') AND answer_id IS NOT NULL AND decision=''))
);
CREATE INDEX IF NOT EXISTS idx_cards_active ON review_cards(item_id) WHERE active;
CREATE INDEX IF NOT EXISTS idx_card_responses_user ON card_responses(user_id,id DESC);
CREATE INDEX IF NOT EXISTS idx_card_responses_card ON card_responses(card_id,id DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_card_undo_once ON card_responses(answer_id) WHERE action='undo';
