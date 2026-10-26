import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import requests
from sqlalchemy import Engine, create_engine, text

from backend.app.settings import get_database_url, get_market_api_key, get_supported_stocks

ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"
STOCK_METADATA = get_supported_stocks()
TICKER_PATTERN = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")

UPSERT_STOCK = text(
    """
    INSERT INTO stocks (
        ticker, company_name, sector, exchange, data_source,
        is_synthetic, latest_available_date, last_successful_ingestion,
        ingestion_status
    )
    VALUES (
        :ticker, :company_name, :sector, :exchange, :data_source,
        FALSE, :latest_available_date, NOW(), 'COMPLETED'
    )
    ON CONFLICT (ticker) DO UPDATE
    SET company_name = EXCLUDED.company_name,
        sector = EXCLUDED.sector,
        exchange = EXCLUDED.exchange,
        data_source = EXCLUDED.data_source,
        is_synthetic = FALSE,
        latest_available_date = EXCLUDED.latest_available_date,
        last_successful_ingestion = NOW(),
        ingestion_status = 'COMPLETED'
    """
)

UPSERT_PRICES = text(
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


def _decimal_field(record: dict[str, Any], field: str) -> Decimal:
    try:
        value = Decimal(record[field])
    except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid or missing price field: {field}") from exc
    if not value.is_finite() or value <= 0:
        raise ValueError(f"Price field {field} must be a finite positive number")
    return value


def _parse_daily_response(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ValueError("Alpha Vantage response must be a JSON object")

    for error_key in ("Error Message", "Note", "Information"):
        if error_key in payload:
            raise RuntimeError(f"Alpha Vantage error: {payload[error_key]}")

    daily_data = payload.get("Time Series (Daily)")
    if not isinstance(daily_data, dict) or not daily_data:
        raise ValueError("Alpha Vantage response contains no daily price data")

    records = []
    for date_text, values in daily_data.items():
        if not isinstance(values, dict):
            raise ValueError(f"Invalid daily price record for {date_text}")
        try:
            price_date = date.fromisoformat(date_text)
        except ValueError as exc:
            raise ValueError(f"Invalid price date: {date_text}") from exc

        open_price = _decimal_field(values, "1. open")
        high_price = _decimal_field(values, "2. high")
        low_price = _decimal_field(values, "3. low")
        close_price = _decimal_field(values, "4. close")
        try:
            volume = int(values["5. volume"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid or missing volume for {date_text}") from exc
        if volume < 0 or high_price < max(open_price, close_price, low_price):
            raise ValueError(f"Inconsistent OHLCV values for {date_text}")
        if low_price > min(open_price, close_price):
            raise ValueError(f"Inconsistent OHLCV values for {date_text}")

        records.append(
            {
                "price_date": price_date,
                "open_price": open_price,
                "high_price": high_price,
                "low_price": low_price,
                "close_price": close_price,
                "volume": volume,
            }
        )
    return records


def ingest_ticker(
    ticker: str,
    *,
    engine: Engine | None = None,
    http_session: requests.Session | None = None,
) -> int:
    ticker = ticker.strip().upper()
    if not TICKER_PATTERN.fullmatch(ticker):
        raise ValueError(f"Invalid ticker symbol: {ticker!r}")
    if ticker not in STOCK_METADATA:
        raise ValueError(f"No company metadata is configured for ticker {ticker}")

    api_key = get_market_api_key()
    owns_engine = engine is None
    db_engine = engine or create_engine(get_database_url())
    owns_session = http_session is None
    client = http_session or requests.Session()
    try:
        response = client.get(
            ALPHA_VANTAGE_URL,
            params={
                "function": "TIME_SERIES_DAILY",
                "symbol": ticker,
                "outputsize": "compact",
                "apikey": api_key,
            },
            timeout=30,
        )
        response.raise_for_status()
        records = _parse_daily_response(response.json())
        store_daily_history(ticker, records, "alphavantage", engine=db_engine)
        return len(records)
    finally:
        if owns_session:
            client.close()
        if owns_engine:
            db_engine.dispose()


def get_stock_metadata(ticker: str) -> dict[str, str]:
    return STOCK_METADATA.get(
        ticker,
        {
            "ticker": ticker,
            "company_name": ticker,
            "sector": "Unclassified",
            "exchange": "Unknown",
        },
    )


def store_daily_history(
    ticker: str,
    records: list[dict[str, Any]],
    data_source: str,
    *,
    engine: Engine | None = None,
) -> int:
    if not records:
        raise ValueError("Cannot store empty daily price history")
    metadata = get_stock_metadata(ticker)

    owns_engine = engine is None
    db_engine = engine or create_engine(get_database_url())
    latest_available_date = max(record["price_date"] for record in records)
    price_rows = [dict(record, ticker=ticker) for record in records]
    stock_parameters = {
        "ticker": ticker,
        "company_name": metadata["company_name"],
        "sector": metadata["sector"],
        "exchange": metadata["exchange"],
        "data_source": data_source,
        "latest_available_date": latest_available_date,
    }
    try:
        with db_engine.begin() as connection:
            connection.execute(UPSERT_STOCK, stock_parameters)
            connection.execute(UPSERT_PRICES, price_rows)
    finally:
        if owns_engine:
            db_engine.dispose()
    return len(records)
