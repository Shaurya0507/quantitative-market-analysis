CREATE TABLE IF NOT EXISTS stocks (
    ticker VARCHAR(10) PRIMARY KEY
        CHECK (ticker ~ '^[A-Z][A-Z0-9.-]{0,9}$'),
    company_name VARCHAR(100) NOT NULL,
    sector VARCHAR(100),
    is_synthetic BOOLEAN NOT NULL DEFAULT FALSE,
    exchange VARCHAR(30),
    data_source VARCHAR(50),
    last_successful_ingestion TIMESTAMPTZ,
    latest_available_date DATE,
    ingestion_status VARCHAR(20) NOT NULL DEFAULT 'NOT_LOADED'
);

CREATE TABLE IF NOT EXISTS stock_prices (
    ticker VARCHAR(10) NOT NULL REFERENCES stocks(ticker),
    price_date DATE NOT NULL,
    open_price NUMERIC(12, 4) NOT NULL CHECK (open_price > 0),
    high_price NUMERIC(12, 4) NOT NULL CHECK (high_price > 0),
    low_price NUMERIC(12, 4) NOT NULL CHECK (low_price > 0),
    close_price NUMERIC(12, 4) NOT NULL CHECK (close_price > 0),
    volume BIGINT NOT NULL CHECK (volume >= 0),
    PRIMARY KEY (ticker, price_date),
    CHECK (high_price >= low_price),
    CHECK (high_price >= GREATEST(open_price, close_price)),
    CHECK (low_price <= LEAST(open_price, close_price))
);

CREATE INDEX IF NOT EXISTS stock_prices_date_ticker_idx
    ON stock_prices (price_date, ticker);

CREATE INDEX IF NOT EXISTS stocks_real_ticker_idx
    ON stocks (ticker)
    WHERE is_synthetic = FALSE;
