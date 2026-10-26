from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from backend.app import main as api_main
from backend.app.ingestion import store_daily_history
from backend.app.jobs import enqueue_ingestion
from backend.app.refresh import (
    latest_completed_session,
    refresh_is_due,
    schedule_market_refreshes,
)
from backend.app import refresh as refresh_module
from backend.app.settings import get_database_url
from backend.app.main import app

NEW_YORK = ZoneInfo("America/New_York")


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        ("2026-10-25T12:00:00", date(2026, 10, 23)),
        ("2026-10-26T09:00:00", date(2026, 10, 23)),
        ("2026-10-26T16:01:00", date(2026, 10, 26)),
        ("2026-11-26T12:00:00", date(2026, 11, 25)),
        ("2026-11-27T12:00:00", date(2026, 11, 25)),
        ("2026-11-27T13:01:00", date(2026, 11, 27)),
    ],
)
def test_latest_completed_session_respects_market_hours_and_holidays(now, expected):
    local_time = datetime.fromisoformat(now).replace(tzinfo=NEW_YORK)

    assert latest_completed_session(local_time) == expected


def test_refresh_check_interval_only_allows_one_due_check():
    now = datetime(2026, 10, 26, 17, tzinfo=NEW_YORK)
    interval = timedelta(hours=6)

    assert refresh_is_due(date(2026, 10, 23), date(2026, 10, 26), None, now, interval)
    assert not refresh_is_due(
        date(2026, 10, 23),
        date(2026, 10, 26),
        now - timedelta(hours=1),
        now,
        interval,
        date(2026, 10, 26),
    )
    assert not refresh_is_due(
        date(2026, 10, 26),
        date(2026, 10, 26),
        None,
        now,
        interval,
    )
    assert refresh_is_due(
        date(2026, 10, 23),
        date(2026, 10, 26),
        now - timedelta(minutes=5),
        now,
        interval,
        date(2026, 10, 23),
    )


def test_market_refresh_endpoint_normalizes_and_forwards_selected_ticker(monkeypatch):
    result = {"expected_trading_date": "2026-10-23", "latest_data_date": "2026-10-23", "jobs": []}
    received = []

    def schedule(selected_ticker, *, engine):
        received.append((selected_ticker, engine))
        return result

    monkeypatch.setattr(api_main, "schedule_market_refreshes", schedule)

    client = TestClient(app)
    refresh_response = client.post("/market/refresh", json={"ticker": "aapl"})
    invalid_response = client.post("/market/refresh", json={"ticker": "bad ticker"})

    assert refresh_response.status_code == 200
    assert refresh_response.json() == result
    assert received == [("AAPL", api_main.engine)]
    assert invalid_response.status_code == 422


def test_current_weekend_data_does_not_enqueue_provider_requests():
    engine = create_engine(get_database_url())
    try:
        result = schedule_market_refreshes(
            "AAPL",
            engine=engine,
            now=datetime(2026, 10, 25, 12, tzinfo=NEW_YORK),
        )
        assert result["expected_trading_date"] == "2026-10-23"
        assert result["latest_data_date"] == "2026-10-23"
        assert result["jobs"] == []
    finally:
        engine.dispose()


def test_new_trading_session_queues_selected_stock_first_without_provider_calls(monkeypatch):
    engine = create_engine(get_database_url())
    connection = engine.connect()
    transaction = connection.begin()

    class NestedTransaction:
        def __enter__(self):
            self.transaction = connection.begin_nested()
            self.transaction.__enter__()
            return connection

        def __exit__(self, *args):
            return self.transaction.__exit__(*args)

    class EngineFacade:
        def connect(self):
            return NestedTransaction()

        def begin(self):
            return NestedTransaction()

    queued = []

    def enqueue(ticker, *, connection, force_refresh, priority, **_kwargs):
        queued.append((ticker, force_refresh, priority))
        return (
            {
                "job_id": str(uuid4()),
                "ticker": ticker,
                "status": "QUEUED",
                "attempt_count": 0,
                "created_at": None,
                "started_at": None,
                "completed_at": None,
                "next_attempt_at": None,
                "last_error": None,
                "records_upserted": None,
            },
            True,
        )

    monkeypatch.setattr(refresh_module, "enqueue_ingestion", enqueue)
    try:
        result = schedule_market_refreshes(
            "NVDA",
            engine=EngineFacade(),
            now=datetime(2026, 10, 26, 17, tzinfo=NEW_YORK),
        )

        assert result["expected_trading_date"] == "2026-10-26"
        assert queued[0] == ("NVDA", True, 100)
        assert all(priority == 0 for _, _, priority in queued[1:])
        assert len(queued) >= 5
        assert len({ticker for ticker, _, _ in queued}) == len(queued)
        assert result["jobs"][0]["ticker"] == "NVDA"
    finally:
        transaction.rollback()
        connection.close()
        engine.dispose()


def test_duplicate_refresh_jobs_reuse_one_job_and_provider_corrections_upsert():
    engine = create_engine(get_database_url())
    connection = engine.connect()
    transaction = connection.begin()

    class NestedTransaction:
        def __enter__(self):
            self.transaction = connection.begin_nested()
            self.transaction.__enter__()
            return connection

        def __exit__(self, *args):
            return self.transaction.__exit__(*args)

    class EngineFacade:
        def begin(self):
            return NestedTransaction()

    facade = EngineFacade()
    try:
        first_record = {
            "price_date": date(2026, 10, 22),
            "open_price": 100,
            "high_price": 105,
            "low_price": 99,
            "close_price": 104,
            "volume": 1000,
        }
        corrected_record = {**first_record, "close_price": 106, "high_price": 107}
        store_daily_history("RFTST", [first_record], "alphavantage", engine=facade)
        store_daily_history("RFTST", [corrected_record], "alphavantage", engine=facade)

        first_job, created = enqueue_ingestion(
            "RFTST",
            connection=connection,
            force_refresh=True,
        )
        second_job, created_again = enqueue_ingestion(
            "RFTST",
            connection=connection,
            force_refresh=True,
            priority=100,
        )
        saved = connection.execute(
            text(
                """
                SELECT COUNT(*) AS row_count, MAX(close_price) AS close_price
                FROM stock_prices WHERE ticker = 'RFTST'
                """
            )
        ).mappings().one()
        priority = connection.execute(
            text("SELECT priority FROM ingestion_jobs WHERE job_id = :job_id"),
            {"job_id": first_job["job_id"]},
        ).scalar_one()

        assert saved["row_count"] == 1
        assert float(saved["close_price"]) == 106
        assert created and not created_again
        assert first_job["job_id"] == second_job["job_id"]
        assert priority == 100
    finally:
        transaction.rollback()
        connection.close()
        engine.dispose()
