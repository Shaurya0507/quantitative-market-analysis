CREATE TABLE IF NOT EXISTS anomaly_model_runs (
    run_id BIGSERIAL PRIMARY KEY,
    ticker VARCHAR(10) NOT NULL REFERENCES stocks(ticker) ON DELETE CASCADE,
    method VARCHAR(30) NOT NULL
        CHECK (method IN ('rolling_z_score', 'local_outlier_factor', 'garch_1_1')),
    model_version VARCHAR(30) NOT NULL,
    status VARCHAR(30) NOT NULL
        CHECK (status IN ('SUCCEEDED', 'INSUFFICIENT_DATA', 'FAILED')),
    latest_price_date DATE,
    training_start DATE,
    training_end DATE,
    scoring_start DATE,
    scoring_end DATE,
    training_observations INTEGER NOT NULL DEFAULT 0,
    evaluation_observations INTEGER NOT NULL DEFAULT 0,
    parameters JSONB NOT NULL DEFAULT '{}'::JSONB,
    message TEXT,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (ticker, method, model_version, latest_price_date)
);

CREATE INDEX IF NOT EXISTS anomaly_model_runs_latest_idx
    ON anomaly_model_runs (ticker, method, processed_at DESC);

CREATE TABLE IF NOT EXISTS anomaly_model_results (
    result_id BIGSERIAL PRIMARY KEY,
    ticker VARCHAR(10) NOT NULL REFERENCES stocks(ticker) ON DELETE CASCADE,
    price_date DATE NOT NULL,
    method VARCHAR(30) NOT NULL
        CHECK (method IN ('rolling_z_score', 'local_outlier_factor', 'garch_1_1')),
    model_version VARCHAR(30) NOT NULL,
    anomaly_score DOUBLE PRECISION,
    threshold DOUBLE PRECISION,
    classification VARCHAR(20) NOT NULL
        CHECK (classification IN ('ANOMALY', 'NORMAL')),
    training_start DATE,
    training_end DATE,
    parameters JSONB NOT NULL DEFAULT '{}'::JSONB,
    features JSONB NOT NULL DEFAULT '{}'::JSONB,
    scored_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (ticker, price_date, method, model_version)
);

CREATE INDEX IF NOT EXISTS anomaly_model_results_method_date_idx
    ON anomaly_model_results (method, price_date DESC, ticker);

CREATE INDEX IF NOT EXISTS anomaly_model_results_ticker_method_date_idx
    ON anomaly_model_results (ticker, method, price_date DESC);
