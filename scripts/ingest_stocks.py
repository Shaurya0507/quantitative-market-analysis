import logging
import argparse
import sys
import time

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from backend.app.ingestion import ingest_ticker
from backend.app.jobs import enqueue_ingestion
from backend.app.settings import get_database_url, get_supported_stocks

logger = logging.getLogger(__name__)
INITIAL_TICKERS = ("AAPL", "MSFT", "NVDA")


def schedule_loaded_stocks() -> None:
    engine = create_engine(get_database_url(), pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT DISTINCT s.ticker
                    FROM stocks s
                    JOIN stock_prices p ON p.ticker = s.ticker
                    WHERE s.is_synthetic = FALSE
                    ORDER BY s.ticker
                    """
                )
            )
            tickers = [row.ticker for row in rows if row.ticker in get_supported_stocks()]
    finally:
        engine.dispose()
    for ticker in tickers:
        job, created = enqueue_ingestion(ticker)
        status = job["status"] if job else "UNAVAILABLE"
        print(f"{ticker}: {'queued' if created else status.lower()}")


def main(refresh_loaded: bool = False) -> None:
    if refresh_loaded:
        schedule_loaded_stocks()
        return
    failures = 0
    for index, ticker in enumerate(INITIAL_TICKERS):
        if index:
            time.sleep(1.1)
        try:
            inserted_or_updated = ingest_ticker(ticker)
        except (OSError, RuntimeError, SQLAlchemyError, ValueError):
            logger.exception("%s ingestion failed", ticker)
            failures += 1
            continue
        print(f"{ticker}: upserted {inserted_or_updated} daily price records")
    if failures:
        logger.error("%d of %d ticker ingestions failed", failures, len(INITIAL_TICKERS))
        sys.exit(1)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="Ingest starter tickers or queue cached stocks for refresh.")
    parser.add_argument(
        "--refresh-loaded",
        action="store_true",
        help="Queue refreshes for real supported stocks already stored in PostgreSQL.",
    )
    main(parser.parse_args().refresh_loaded)
