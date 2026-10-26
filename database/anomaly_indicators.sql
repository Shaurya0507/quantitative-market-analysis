WITH base_prices AS (
    SELECT
        p.ticker,
        p.price_date,
        p.close_price,
        p.high_price,
        p.low_price,
        p.volume,
        LAG(p.close_price) OVER (
            PARTITION BY p.ticker ORDER BY p.price_date
        ) AS previous_close
    FROM stock_prices AS p
    JOIN stocks AS s ON s.ticker = p.ticker
    WHERE p.ticker = :ticker
      AND s.is_synthetic = FALSE
),
daily_returns AS (
    SELECT
        ticker,
        price_date,
        close_price,
        high_price,
        low_price,
        volume,
        previous_close,
        close_price / NULLIF(previous_close, 0) - 1 AS daily_return,
        (high_price - low_price) / NULLIF(previous_close, 0) AS intraday_range
    FROM base_prices
),
historical_indicators AS (
    SELECT
        *,
        AVG(daily_return) OVER return_history AS historical_return_mean,
        STDDEV_SAMP(daily_return) OVER return_history AS historical_return_stddev,
        COUNT(daily_return) OVER return_history AS return_observations,
        AVG(volume) OVER volume_history AS historical_volume_mean,
        COUNT(volume) OVER volume_history AS volume_observations,
        STDDEV_SAMP(daily_return) OVER volatility_window AS rolling_volatility,
        COUNT(daily_return) OVER volatility_window AS volatility_observations,
        FIRST_VALUE(price_date) OVER volume_history AS volume_history_start
    FROM daily_returns
    WINDOW
        return_history AS (
            PARTITION BY ticker ORDER BY price_date
            ROWS BETWEEN :return_history_days PRECEDING AND 1 PRECEDING
        ),
        volume_history AS (
            PARTITION BY ticker ORDER BY price_date
            ROWS BETWEEN :volume_history_days PRECEDING AND 1 PRECEDING
        ),
        volatility_window AS (
            PARTITION BY ticker ORDER BY price_date
            ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
        )
),
with_scores AS (
    SELECT
        *,
        CASE
            WHEN return_observations = :return_history_days
             AND historical_return_stddev > 0
            THEN (daily_return - historical_return_mean) / historical_return_stddev
        END AS return_z_score,
        CASE
            WHEN volume_observations = :volume_history_days
             AND historical_volume_mean > 0
            THEN volume / historical_volume_mean
        END AS volume_ratio
    FROM historical_indicators
),
with_volatility_baseline AS (
    SELECT
        *,
        AVG(rolling_volatility) OVER (
            PARTITION BY ticker ORDER BY price_date
            ROWS BETWEEN :volatility_baseline_days PRECEDING AND 1 PRECEDING
        ) AS historical_volatility_mean,
        COUNT(rolling_volatility) OVER (
            PARTITION BY ticker ORDER BY price_date
            ROWS BETWEEN :volatility_baseline_days PRECEDING AND 1 PRECEDING
        ) AS historical_volatility_observations
    FROM with_scores
)
SELECT
    ticker,
    price_date,
    daily_return,
    historical_return_mean,
    historical_return_stddev,
    return_z_score,
    volume,
    volume_ratio,
    historical_volume_mean,
    volume_observations,
    volume_history_start,
    CASE
        WHEN volatility_observations = :volatility_days
        THEN rolling_volatility
    END AS rolling_volatility,
    CASE
        WHEN historical_volatility_observations = :volatility_baseline_days
         AND historical_volatility_mean > 0
         AND volatility_observations = :volatility_days
        THEN rolling_volatility / historical_volatility_mean
    END AS volatility_expansion,
    historical_volatility_mean,
    historical_volatility_observations,
    intraday_range
FROM with_volatility_baseline
WHERE daily_return IS NOT NULL
ORDER BY price_date;
