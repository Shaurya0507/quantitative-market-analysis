import argparse
import math
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import create_engine, text

from backend.app.settings import get_database_url

INSERT_STOCKS = text(
    """
    INSERT INTO stocks (ticker, company_name, sector)
    VALUES (:ticker, :company_name, 'Synthetic benchmark data')
    ON CONFLICT (ticker) DO UPDATE
    SET company_name = EXCLUDED.company_name,
        sector = EXCLUDED.sector
    """
)

INSERT_PRICE = text(
    """
    INSERT INTO stock_prices (
        ticker, price_date, open_price, high_price, low_price, close_price, volume
    )
    VALUES (
        :ticker, :price_date, :open_price, :high_price, :low_price, :close_price, :volume
    )
    ON CONFLICT (ticker, price_date) DO UPDATE
    SET open_price = EXCLUDED.open_price,
        high_price = EXCLUDED.high_price,
        low_price = EXCLUDED.low_price,
        close_price = EXCLUDED.close_price,
        volume = EXCLUDED.volume
    """
)


def trading_dates(observations: int, end_date: date) -> list[date]:
    dates = []
    current = end_date
    while len(dates) < observations:
        if current.weekday() < 5:
            dates.append(current)
        current -= timedelta(days=1)
    dates.reverse()
    return dates


def price_record(ticker_index: int, ticker: str, price_date: date, day_index: int) -> dict:
    base_price = 20 + ticker_index * 2
    close = base_price + day_index * 0.004 + math.sin(day_index / 19 + ticker_index) * 1.8
    open_price = close + math.sin(day_index / 7 + ticker_index) * 0.35
    high_price = max(open_price, close) + 0.25
    low_price = min(open_price, close) - 0.25
    return {
        "ticker": ticker,
        "price_date": price_date,
        "open_price": Decimal(f"{open_price:.4f}"),
        "high_price": Decimal(f"{high_price:.4f}"),
        "low_price": Decimal(f"{low_price:.4f}"),
        "close_price": Decimal(f"{close:.4f}"),
        "volume": 100_000 + ((ticker_index * 7_919 + day_index * 104_729) % 9_000_000),
    }


def generate_synthetic_data(
    ticker_count: int,
    observations: int,
    batch_size: int,
    end_date: date,
) -> int:
    if not 1 <= ticker_count <= 1_000:
        raise ValueError("ticker_count must be between 1 and 1000")
    if observations < 2:
        raise ValueError("observations must be at least 2")
    if batch_size < 1:
        raise ValueError("batch_size must be positive")

    dates = trading_dates(observations, end_date)
    stocks = [
        {
            "ticker": f"SYN{ticker_index:03d}",
            "company_name": f"Synthetic Benchmark Company {ticker_index:03d}",
        }
        for ticker_index in range(ticker_count)
    ]
    total = 0
    engine = create_engine(get_database_url())
    try:
        with engine.begin() as connection:
            connection.execute(INSERT_STOCKS, stocks)
            batch = []
            for ticker_index, stock in enumerate(stocks):
                for day_index, price_date in enumerate(dates):
                    batch.append(
                        price_record(
                            ticker_index,
                            stock["ticker"],
                            price_date,
                            day_index,
                        )
                    )
                    if len(batch) >= batch_size:
                        connection.execute(INSERT_PRICE, batch)
                        total += len(batch)
                        batch.clear()
            if batch:
                connection.execute(INSERT_PRICE, batch)
                total += len(batch)
    finally:
        engine.dispose()
    return total


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate deterministic, clearly labeled synthetic stock prices."
    )
    parser.add_argument("--tickers", type=int, default=200)
    parser.add_argument("--observations", type=int, default=5_000)
    parser.add_argument("--batch-size", type=int, default=20_000)
    parser.add_argument("--end-date", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()

    count = generate_synthetic_data(
        args.tickers,
        args.observations,
        args.batch_size,
        args.end_date,
    )
    print(
        f"Upserted {count:,} synthetic price rows for {args.tickers} tickers "
        f"({args.observations:,} trading-day observations each)."
    )


if __name__ == "__main__":
    main()
