import logging
import os
import time
from datetime import timedelta

from requests import RequestException
from sqlalchemy import create_engine, text

from backend.app.anomalies import process_ticker_anomalies
from backend.app.ingestion import store_daily_history
from backend.app.modeling import process_ticker_models
from backend.app.providers import (
    AlphaVantageProvider,
    ProviderRateLimitError,
    UnsupportedTickerError,
)
from backend.app.session_lifecycle import cleanup_expired_dashboard_sessions
from backend.app.settings import get_database_url

logger = logging.getLogger("marketlens.worker")
MAX_ATTEMPTS = 4
DAILY_REQUEST_LIMIT = 25
REQUEST_INTERVAL_SECONDS = 1.1
SESSION_CLEANUP_INTERVAL_SECONDS = 10
RATE_LIMIT_MESSAGE = (
    "The market data provider's request limit has been reached. "
    "New stock data cannot be retrieved right now. Please try again later."
)


def _claim_job(engine):
    with engine.begin() as connection:
        job = connection.execute(
            text(
                """
                SELECT job_id, ticker, attempt_count
                FROM ingestion_jobs
                WHERE status IN ('QUEUED', 'RATE_LIMITED')
                  AND next_attempt_at <= NOW()
                ORDER BY priority DESC, created_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
                """
            )
        ).mappings().one_or_none()
        if not job:
            return None

        quota = connection.execute(
            text(
                """
                SELECT COUNT(*) AS request_count, MIN(requested_at) AS oldest_request
                FROM provider_requests
                WHERE provider = 'alphavantage'
                  AND requested_at >= NOW() - INTERVAL '24 hours'
                """
            )
        ).mappings().one()
        if quota["request_count"] >= DAILY_REQUEST_LIMIT:
            next_attempt = quota["oldest_request"] + timedelta(hours=24, seconds=2)
            connection.execute(
                text(
                    """
                    UPDATE ingestion_jobs
                    SET status = 'RATE_LIMITED', next_attempt_at = :next_attempt,
                        last_error = :last_error
                    WHERE job_id = :job_id
                    """
                ),
                {
                    "job_id": job["job_id"],
                    "next_attempt": next_attempt,
                    "last_error": RATE_LIMIT_MESSAGE,
                },
            )
            connection.execute(
                text(
                    """
                    UPDATE stocks SET ingestion_status = 'RATE_LIMITED'
                    WHERE ticker = :ticker
                    """
                ),
                {"ticker": job["ticker"]},
            )
            return None

        connection.execute(
            text(
                """
                UPDATE ingestion_jobs
                SET status = 'PROCESSING',
                    attempt_count = attempt_count + 1,
                    started_at = COALESCE(started_at, NOW()),
                    last_error = NULL
                WHERE job_id = :job_id
                """
            ),
            {"job_id": job["job_id"]},
        )
        connection.execute(
            text("UPDATE stocks SET ingestion_status = 'PROCESSING' WHERE ticker = :ticker"),
            {"ticker": job["ticker"]},
        )
        return {
            "job_id": job["job_id"],
            "ticker": job["ticker"],
            "attempt_count": job["attempt_count"] + 1,
        }


def _record_request(engine, ticker: str) -> int:
    with engine.begin() as connection:
        previous = connection.execute(
            text(
                """
                SELECT requested_at
                FROM provider_requests
                WHERE provider = 'alphavantage'
                ORDER BY requested_at DESC
                LIMIT 1
                """
            )
        ).scalar_one_or_none()
        if previous is not None:
            elapsed_seconds = float(
                connection.execute(
                    text("SELECT EXTRACT(EPOCH FROM (NOW() - :previous))"),
                    {"previous": previous},
                ).scalar_one()
            )
            wait_seconds = REQUEST_INTERVAL_SECONDS - elapsed_seconds
            if wait_seconds > 0:
                time.sleep(wait_seconds)
        request_id = connection.execute(
            text(
                """
                INSERT INTO provider_requests (provider, request_kind, succeeded)
                VALUES ('alphavantage', 'TIME_SERIES_DAILY', FALSE)
                RETURNING request_id
                """
            )
        ).scalar_one()
        return request_id


def _finish_job(
    engine,
    job: dict,
    *,
    status: str,
    error: str | None = None,
    records_upserted: int | None = None,
    retry_seconds: int | None = None,
) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE ingestion_jobs
                SET status = :status,
                    completed_at = CASE WHEN :terminal THEN NOW() ELSE NULL END,
                    next_attempt_at = CASE
                        WHEN CAST(:retry_seconds AS INTEGER) IS NOT NULL
                        THEN NOW() + (CAST(:retry_seconds AS INTEGER) * INTERVAL '1 second')
                        ELSE next_attempt_at
                    END,
                    last_error = :error,
                    records_upserted = COALESCE(:records_upserted, records_upserted)
                WHERE job_id = :job_id
                """
            ),
            {
                "job_id": job["job_id"],
                "status": status,
                "terminal": status in {"COMPLETED", "FAILED"},
                "retry_seconds": retry_seconds,
                "error": error,
                "records_upserted": records_upserted,
            },
        )
        connection.execute(
            text("UPDATE stocks SET ingestion_status = :status WHERE ticker = :ticker"),
            {"ticker": job["ticker"], "status": status},
        )
        connection.execute(
            text(
                """
                UPDATE market_refresh_state
                SET checked_at = NOW(), last_status = :status
                WHERE ticker = :ticker AND last_job_id = :job_id
                """
            ),
            {
                "ticker": job["ticker"],
                "job_id": job["job_id"],
                "status": status,
            },
        )


def process_next_job(engine, provider_factory=AlphaVantageProvider) -> bool:
    job = _claim_job(engine)
    if job is None:
        return False
    request_id = _record_request(engine, job["ticker"])
    provider = None
    try:
        provider = provider_factory()
        history = provider.get_daily_history(job["ticker"])
        records_upserted = store_daily_history(
            job["ticker"],
            history.prices,
            history.provider,
            engine=engine,
        )
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE provider_requests SET succeeded = TRUE WHERE request_id = :id"),
                {"id": request_id},
            )
        try:
            process_ticker_anomalies(job["ticker"], engine=engine)
        except Exception:
            logger.exception(
                "%s prices stored but anomaly processing failed",
                job["ticker"],
            )
            _finish_job(
                engine,
                job,
                status="FAILED",
                error="Prices were stored, but anomaly processing failed; retry is available.",
                records_upserted=records_upserted,
            )
            return True
        try:
            process_ticker_models(job["ticker"], engine=engine)
        except Exception:
            logger.exception(
                "%s prices stored but quantitative model processing failed",
                job["ticker"],
            )
        _finish_job(
            engine,
            job,
            status="COMPLETED",
            records_upserted=records_upserted,
        )
    except ProviderRateLimitError as exc:
        wait_seconds = (
            24 * 60 * 60
            if "25 requests per day" in str(exc).lower()
            else 60
        )
        _finish_job(
            engine,
            job,
            status="RATE_LIMITED",
            error=(
                RATE_LIMIT_MESSAGE
                if "25 requests per day" in str(exc).lower()
                else "Alpha Vantage temporarily rate-limited this request; retrying shortly."
            ),
            retry_seconds=wait_seconds,
        )
    except UnsupportedTickerError:
        _finish_job(
            engine,
            job,
            status="FAILED",
            error="Historical data is unavailable for this ticker from Alpha Vantage.",
        )
    except (RequestException, OSError, TimeoutError) as exc:
        if job["attempt_count"] >= MAX_ATTEMPTS:
            status = "FAILED"
            retry_seconds = None
            error = f"Provider request failed after {MAX_ATTEMPTS} attempts ({type(exc).__name__})."
        else:
            status = "QUEUED"
            retry_seconds = min(30 * (2 ** (job["attempt_count"] - 1)), 900)
            error = (
                f"Transient provider failure ({type(exc).__name__}); "
                f"retrying in {retry_seconds} seconds."
            )
        _finish_job(
            engine,
            job,
            status=status,
            error=error,
            retry_seconds=retry_seconds,
        )
    except (RuntimeError, ValueError):
        logger.exception("Permanent data-processing failure for %s", job["ticker"])
        _finish_job(
            engine,
            job,
            status="FAILED",
            error="Provider response was invalid or data processing failed.",
        )
    finally:
        close_provider = getattr(provider, "close", None) if provider else None
        if close_provider:
            close_provider()
    return True


def run_worker() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    engine = create_engine(get_database_url(), pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE ingestion_jobs
                    SET status = 'QUEUED', next_attempt_at = NOW(),
                        last_error = 'Worker restarted; job resumed'
                    WHERE status = 'PROCESSING'
                    """
                )
            )
        logger.info("Market data worker started")
        next_session_cleanup = 0.0
        while True:
            if time.monotonic() >= next_session_cleanup:
                deleted_prices = cleanup_expired_dashboard_sessions(engine)
                if deleted_prices:
                    logger.info(
                        "Cleared %s cached real-market price records after dashboard sessions expired",
                        deleted_prices,
                    )
                next_session_cleanup = time.monotonic() + SESSION_CLEANUP_INTERVAL_SECONDS
            if not process_next_job(engine):
                time.sleep(2)
    finally:
        engine.dispose()


if __name__ == "__main__":
    run_worker()
