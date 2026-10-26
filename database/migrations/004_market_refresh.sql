ALTER TABLE ingestion_jobs
    ADD COLUMN IF NOT EXISTS priority INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS ingestion_jobs_priority_queue_idx
    ON ingestion_jobs (priority DESC, next_attempt_at, created_at)
    WHERE status IN ('QUEUED', 'RATE_LIMITED');

CREATE TABLE IF NOT EXISTS market_refresh_state (
    ticker VARCHAR(10) PRIMARY KEY REFERENCES stocks(ticker) ON DELETE CASCADE,
    checked_at TIMESTAMPTZ NOT NULL,
    last_job_id UUID,
    last_status VARCHAR(20) NOT NULL
);
