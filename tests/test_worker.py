from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from requests import ConnectionError
from sqlalchemy import create_engine, text

from backend.app.providers import DailyHistory, ProviderRateLimitError, UnsupportedTickerError
from backend.app.settings import get_database_url
from backend.app import worker


class FakeProvider:
    def __init__(self, result=None, error=None):
        self.result = result or DailyHistory(provider="alphavantage", prices=[])
        self.error = error
        self.closed = False

    def get_daily_history(self, ticker):
        if self.error:
            raise self.error
        return self.result

    def close(self):
        self.closed = True


def arrange_claimed_job(monkeypatch, *, attempt_count=1):
    job = {"job_id": "job-id", "ticker": "HD", "attempt_count": attempt_count}
    monkeypatch.setattr(worker, "_claim_job", lambda _: job)
    monkeypatch.setattr(worker, "_record_request", lambda *_: 10)
    return job


def test_record_request_handles_decimal_elapsed_time(monkeypatch):
    class Result:
        def __init__(self, value):
            self.value = value

        def scalar_one(self):
            return self.value

        def scalar_one_or_none(self):
            return self.value

    class Connection:
        def execute(self, statement, parameters=None):
            sql = str(statement)
            if "SELECT requested_at" in sql:
                return Result(datetime.now(timezone.utc) - timedelta(seconds=2))
            if "EXTRACT(EPOCH" in sql:
                return Result(Decimal("2.0"))
            if "INSERT INTO provider_requests" in sql:
                return Result(42)
            raise AssertionError(f"Unexpected SQL: {sql}")

    class Transaction:
        def __enter__(self):
            return Connection()

        def __exit__(self, *_):
            return False

    class Engine:
        def begin(self):
            return Transaction()

    monkeypatch.setattr(worker.time, "sleep", lambda _: pytest.fail("unexpected throttle wait"))

    assert worker._record_request(Engine(), "HD") == 42


def test_worker_stores_provider_prices_and_completes_job(monkeypatch):
    job = arrange_claimed_job(monkeypatch)
    provider = FakeProvider(DailyHistory(provider="alphavantage", prices=[{"close_price": 1}]))
    stored = []
    finished = []
    class Connection:
        def execute(self, *_args, **_kwargs):
            return None

    class Engine:
        def begin(self):
            return Transaction()

    class Transaction:
        def __enter__(self):
            return Connection()

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(
        worker,
        "store_daily_history",
        lambda ticker, prices, provider_name, **kwargs: stored.append(
            (ticker, prices, provider_name)
        ) or 1,
    )
    monkeypatch.setattr(worker, "process_ticker_anomalies", lambda *_, **__: 0)
    modeled = []
    monkeypatch.setattr(
        worker,
        "process_ticker_models",
        lambda ticker, **kwargs: modeled.append((ticker, kwargs)) or {},
    )
    monkeypatch.setattr(worker, "_finish_job", lambda _engine, item, **kwargs: finished.append((item, kwargs)))
    engine = Engine()

    assert worker.process_next_job(engine, lambda: provider)

    assert stored == [("HD", [{"close_price": 1}], "alphavantage")]
    assert finished[0][0] == job
    assert finished[0][1]["status"] == "COMPLETED"
    assert finished[0][1]["records_upserted"] == 1
    assert modeled[0][0] == "HD"
    assert modeled[0][1]["engine"] is engine
    assert provider.closed


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_retry_seconds"),
    [
        (ProviderRateLimitError("provider quota reached"), "RATE_LIMITED", 60),
        (ProviderRateLimitError("25 requests per day exceeded"), "RATE_LIMITED", 24 * 60 * 60),
        (UnsupportedTickerError("no data available"), "FAILED", None),
        (ConnectionError("provider unavailable"), "QUEUED", 30),
    ],
)
def test_worker_records_provider_errors_without_crashing(
    monkeypatch,
    error,
    expected_status,
    expected_retry_seconds,
):
    arrange_claimed_job(monkeypatch)
    provider = FakeProvider(error=error)
    finished = []
    monkeypatch.setattr(
        worker,
        "store_daily_history",
        lambda *_args, **_kwargs: pytest.fail("failed provider responses must not modify cached prices"),
    )
    monkeypatch.setattr(worker, "_finish_job", lambda _engine, _job, **kwargs: finished.append(kwargs))

    assert worker.process_next_job(object(), lambda: provider)

    assert finished[0]["status"] == expected_status
    assert provider.closed
    assert finished[0].get("retry_seconds") == expected_retry_seconds
    if expected_status == "RATE_LIMITED":
        if expected_retry_seconds == 24 * 60 * 60:
            assert "request limit has been reached" in finished[0]["error"]
        else:
            assert "temporarily rate-limited" in finished[0]["error"]


def test_worker_marks_invalid_provider_data_failed(monkeypatch):
    arrange_claimed_job(monkeypatch)
    provider = FakeProvider(error=ValueError("invalid provider response"))
    finished = []
    monkeypatch.setattr(worker, "_finish_job", lambda _engine, _job, **kwargs: finished.append(kwargs))

    assert worker.process_next_job(object(), lambda: provider)

    assert finished[0]["status"] == "FAILED"
    assert "invalid" in finished[0]["error"].lower()


def test_finish_job_supports_null_and_numeric_retry_values():
    engine = create_engine(get_database_url())
    connection = engine.connect()
    transaction = connection.begin()
    job_id = str(uuid4())

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

    try:
        connection.execute(
            text(
                """
                INSERT INTO ingestion_jobs (job_id, ticker, status)
                VALUES (:job_id, 'TESTW', 'PROCESSING')
                """
            ),
            {"job_id": job_id},
        )
        job = {"job_id": job_id, "ticker": "TESTW"}

        worker._finish_job(EngineFacade(), job, status="COMPLETED", records_upserted=3)
        completed = connection.execute(
            text("SELECT status, records_upserted FROM ingestion_jobs WHERE job_id = :job_id"),
            {"job_id": job_id},
        ).mappings().one()
        assert completed["status"] == "COMPLETED"
        assert completed["records_upserted"] == 3

        worker._finish_job(
            EngineFacade(),
            job,
            status="RATE_LIMITED",
            retry_seconds=60,
            error="Provider limit reached",
        )
        retry = connection.execute(
            text("SELECT status, next_attempt_at > NOW() AS retry_scheduled FROM ingestion_jobs WHERE job_id = :job_id"),
            {"job_id": job_id},
        ).mappings().one()
        assert retry["status"] == "RATE_LIMITED"
        assert retry["retry_scheduled"]
    finally:
        transaction.rollback()
        connection.close()
        engine.dispose()
