ALTER TABLE stocks
    ADD COLUMN IF NOT EXISTS is_synthetic BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS exchange VARCHAR(30),
    ADD COLUMN IF NOT EXISTS data_source VARCHAR(50),
    ADD COLUMN IF NOT EXISTS last_successful_ingestion TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS latest_available_date DATE,
    ADD COLUMN IF NOT EXISTS ingestion_status VARCHAR(20) NOT NULL DEFAULT 'NOT_LOADED';

UPDATE stocks
SET is_synthetic = TRUE,
    data_source = COALESCE(data_source, 'synthetic_benchmark'),
    ingestion_status = 'COMPLETED'
WHERE sector = 'Synthetic benchmark data'
   OR (ticker LIKE 'SYN%' AND company_name LIKE 'Synthetic Benchmark Company%');

UPDATE stocks
SET data_source = COALESCE(data_source, 'alphavantage'),
    ingestion_status = 'COMPLETED'
WHERE ticker IN ('AAPL', 'MSFT', 'NVDA')
  AND NOT is_synthetic;

UPDATE stocks AS s
SET latest_available_date = prices.latest_date
FROM (
    SELECT ticker, MAX(price_date) AS latest_date
    FROM stock_prices
    GROUP BY ticker
) AS prices
WHERE prices.ticker = s.ticker
  AND s.latest_available_date IS NULL;

CREATE INDEX IF NOT EXISTS stocks_real_ticker_idx
    ON stocks (ticker)
    WHERE is_synthetic = FALSE;

CREATE TABLE IF NOT EXISTS ingestion_jobs (
    job_id UUID PRIMARY KEY,
    ticker VARCHAR(10) NOT NULL,
    status VARCHAR(20) NOT NULL
        CHECK (status IN ('QUEUED', 'PROCESSING', 'COMPLETED', 'FAILED', 'RATE_LIMITED')),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_error TEXT,
    records_upserted INTEGER
);

CREATE UNIQUE INDEX IF NOT EXISTS ingestion_jobs_one_active_per_ticker_idx
    ON ingestion_jobs (ticker)
    WHERE status IN ('QUEUED', 'PROCESSING', 'RATE_LIMITED');

CREATE INDEX IF NOT EXISTS ingestion_jobs_queue_idx
    ON ingestion_jobs (next_attempt_at, created_at)
    WHERE status IN ('QUEUED', 'RATE_LIMITED');

CREATE TABLE IF NOT EXISTS provider_requests (
    request_id BIGSERIAL PRIMARY KEY,
    provider VARCHAR(50) NOT NULL,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    request_kind VARCHAR(40) NOT NULL,
    succeeded BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS provider_requests_daily_quota_idx
    ON provider_requests (provider, requested_at);

CREATE TABLE IF NOT EXISTS anomaly_results (
    id BIGSERIAL PRIMARY KEY,
    ticker VARCHAR(10) NOT NULL REFERENCES stocks(ticker) ON DELETE CASCADE,
    price_date DATE NOT NULL,
    daily_return DOUBLE PRECISION NOT NULL,
    return_z_score DOUBLE PRECISION,
    volume_ratio DOUBLE PRECISION,
    rolling_volatility DOUBLE PRECISION,
    intraday_range DOUBLE PRECISION,
    severity VARCHAR(10) NOT NULL CHECK (severity IN ('LOW', 'MEDIUM', 'HIGH')),
    explanation TEXT NOT NULL,
    triggered_indicators JSONB NOT NULL,
    detection_method VARCHAR(40) NOT NULL DEFAULT 'rolling_statistical',
    detection_version VARCHAR(20) NOT NULL DEFAULT 'v1',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (ticker, price_date, detection_version)
);

CREATE INDEX IF NOT EXISTS anomaly_results_date_ticker_idx
    ON anomaly_results (price_date DESC, ticker);

CREATE INDEX IF NOT EXISTS anomaly_results_ticker_date_idx
    ON anomaly_results (ticker, price_date DESC);

CREATE INDEX IF NOT EXISTS anomaly_results_severity_date_idx
    ON anomaly_results (severity, price_date DESC);
