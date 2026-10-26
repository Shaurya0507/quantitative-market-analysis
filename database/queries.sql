-- These statements use SQLAlchemy named bind parameters such as ticker and dates.

-- 1. Historical closing prices for a ticker and date range.
SELECT ticker, price_date, close_price
FROM stock_prices
WHERE ticker = :ticker
  AND price_date BETWEEN :start_date AND :end_date
ORDER BY price_date;

-- 2. Seven-trading-observation moving average. Fetch only six preceding
-- observations so the requested range has a complete rolling window without
-- scanning the ticker's full history.
WITH price_window AS (
    (
        SELECT ticker, price_date, close_price
        FROM stock_prices
        WHERE ticker = :ticker
          AND price_date < :start_date
        ORDER BY price_date DESC
        LIMIT 6
    )
    UNION ALL
    (
        SELECT ticker, price_date, close_price
        FROM stock_prices
        WHERE ticker = :ticker
          AND price_date BETWEEN :start_date AND :end_date
    )
),
moving_averages AS (
    SELECT
        ticker,
        price_date,
        close_price,
        AVG(close_price) OVER (
            PARTITION BY ticker
            ORDER BY price_date
            ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
        ) AS moving_average_7
    FROM price_window
)
SELECT ticker, price_date, close_price, moving_average_7
FROM moving_averages
WHERE price_date BETWEEN :start_date AND :end_date
ORDER BY price_date;

-- 3. Daily percentage returns, including the previous trading observation
-- before the requested range when one exists.
WITH price_window AS (
    (
        SELECT ticker, price_date, close_price
        FROM stock_prices
        WHERE ticker = :ticker
          AND price_date < :start_date
        ORDER BY price_date DESC
        LIMIT 1
    )
    UNION ALL
    (
        SELECT ticker, price_date, close_price
        FROM stock_prices
        WHERE ticker = :ticker
          AND price_date BETWEEN :start_date AND :end_date
    )
),
daily_returns AS (
    SELECT
        ticker,
        price_date,
        close_price,
        (
            close_price / NULLIF(
                LAG(close_price) OVER (
                    PARTITION BY ticker
                    ORDER BY price_date
                ),
                0
            ) - 1
        ) * 100 AS daily_return_pct
    FROM price_window
)
SELECT ticker, price_date, close_price, daily_return_pct
FROM daily_returns
WHERE price_date BETWEEN :start_date AND :end_date
ORDER BY price_date;

-- 4. Sample standard deviation of daily percentage returns for the selected
-- period. The anchor row provides the first in-range day's prior close.
WITH price_window AS (
    (
        SELECT ticker, price_date, close_price
        FROM stock_prices
        WHERE ticker = :ticker
          AND price_date < :start_date
        ORDER BY price_date DESC
        LIMIT 1
    )
    UNION ALL
    (
        SELECT ticker, price_date, close_price
        FROM stock_prices
        WHERE ticker = :ticker
          AND price_date BETWEEN :start_date AND :end_date
    )
),
daily_returns AS (
    SELECT
        ticker,
        price_date,
        (
            close_price / NULLIF(
                LAG(close_price) OVER (
                    PARTITION BY ticker
                    ORDER BY price_date
                ),
                0
            ) - 1
        ) * 100 AS daily_return_pct
    FROM price_window
)
SELECT ticker, STDDEV_SAMP(daily_return_pct) AS volatility_pct
FROM daily_returns
WHERE price_date BETWEEN :start_date AND :end_date
GROUP BY ticker;

-- 5. Rank each stock by its first-to-last close in the selected period. Two
-- bounded lookups use the (ticker, price_date) primary-key index, avoiding
-- windowing and sorting every matching daily observation across all stocks.
WITH stock_returns AS (
    SELECT
        s.ticker,
        s.company_name,
        (last_price.close_price / NULLIF(first_price.close_price, 0) - 1) * 100
            AS period_return_pct
    FROM stocks AS s
    CROSS JOIN LATERAL (
        SELECT close_price, price_date
        FROM stock_prices AS p
        WHERE p.ticker = s.ticker
          AND p.price_date BETWEEN :start_date AND :end_date
        ORDER BY p.price_date
        LIMIT 1
    ) AS first_price
    CROSS JOIN LATERAL (
        SELECT close_price, price_date
        FROM stock_prices AS p
        WHERE p.ticker = s.ticker
          AND p.price_date BETWEEN :start_date AND :end_date
        ORDER BY p.price_date DESC
        LIMIT 1
    ) AS last_price
    WHERE (:include_synthetic OR s.is_synthetic = FALSE)
      AND last_price.price_date > first_price.price_date
),
ranked_stocks AS (
    SELECT
        ticker,
        company_name,
        period_return_pct,
        RANK() OVER (ORDER BY period_return_pct DESC) AS performance_rank
    FROM stock_returns
)
SELECT
    performance_rank,
    ticker,
    company_name,
    period_return_pct
FROM ranked_stocks AS r
ORDER BY performance_rank, ticker
LIMIT 20;
