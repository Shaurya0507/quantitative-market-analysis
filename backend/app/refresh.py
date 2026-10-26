import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import exchange_calendars as exchange_calendars
from sqlalchemy import Engine, create_engine, text

from backend.app.jobs import enqueue_ingestion
from backend.app.settings import get_database_url

NEW_YORK = ZoneInfo("America/New_York")
DEFAULT_CHECK_INTERVAL_SECONDS = 6 * 60 * 60


def get_check_interval_seconds() -> int:
    value = int(
        os.getenv(
            "MARKET_REFRESH_CHECK_INTERVAL_SECONDS",
            str(DEFAULT_CHECK_INTERVAL_SECONDS),
        )
    )
    if value < 1:
        raise ValueError("MARKET_REFRESH_CHECK_INTERVAL_SECONDS must be positive")
    return value


def latest_completed_session(now: datetime | None = None) -> date:
    local_now = (now or datetime.now(NEW_YORK)).astimezone(NEW_YORK)
    calendar = exchange_calendars.get_calendar("XNYS")
    session = calendar.date_to_session(local_now.date().isoformat(), direction="previous")
    session_date = session.date()
    close = calendar.session_close(session).to_pydatetime().astimezone(NEW_YORK)
    if session_date == local_now.date() and local_now < close:
        session = calendar.previous_session(session)
    return session.date()


def refresh_is_due(
    latest_price_date: date,
    expected_session: date,
    checked_at: datetime | None,
    now: datetime,
    interval: timedelta,
    last_checked_session: date | None = None,
) -> bool:
    return latest_price_date < expected_session and (
        checked_at is None
        or last_checked_session is None
        or last_checked_session < expected_session
        or checked_at <= now - interval
    )


def schedule_market_refreshes(
    selected_ticker: str | None = None,
    *,
    engine: Engine | None = None,
    now: datetime | None = None,
) -> dict:
    owns_engine = engine is None
    db_engine = engine or create_engine(get_database_url(), pool_pre_ping=True)
    current_time = (now or datetime.now(NEW_YORK)).astimezone(NEW_YORK)
    expected_session = latest_completed_session(current_time)
    interval = timedelta(seconds=get_check_interval_seconds())
    selected = selected_ticker.upper() if selected_ticker else None
    jobs = []
    checked_tickers = []
    try:
        with db_engine.connect() as connection:
            tickers = connection.execute(
                text(
                    """
                    SELECT s.ticker, MAX(p.price_date) AS latest_price_date
                    FROM stocks s
                    JOIN stock_prices p ON p.ticker = s.ticker
                    WHERE s.is_synthetic = FALSE
                    GROUP BY s.ticker
                    ORDER BY CASE WHEN s.ticker = :selected THEN 0 ELSE 1 END, s.ticker
                    """
                ),
                {"selected": selected},
            ).mappings().all()

        for stock in tickers:
            ticker = stock["ticker"]
            checked_tickers.append(ticker)
            with db_engine.begin() as connection:
                connection.execute(
                    text("SELECT pg_advisory_xact_lock(hashtext(:ticker))"),
                    {"ticker": ticker},
                )
                state = connection.execute(
                    text(
                        """
                        SELECT checked_at, last_job_id, expected_session
                        FROM market_refresh_state
                        WHERE ticker = :ticker
                        """
                    ),
                    {"ticker": ticker},
                ).mappings().one_or_none()
                if stock["latest_price_date"] >= expected_session:
                    connection.execute(
                        text(
                            """
                            INSERT INTO market_refresh_state
                                (ticker, checked_at, last_job_id, last_status, expected_session)
                            VALUES (:ticker, :checked_at, NULL, 'CURRENT', :expected_session)
                            ON CONFLICT (ticker) DO UPDATE
                            SET checked_at = EXCLUDED.checked_at,
                                last_job_id = NULL,
                                last_status = 'CURRENT',
                                expected_session = EXCLUDED.expected_session
                            """
                        ),
                        {
                            "ticker": ticker,
                            "checked_at": current_time,
                            "expected_session": expected_session,
                        },
                    )
                    continue

                if state and not refresh_is_due(
                    stock["latest_price_date"],
                    expected_session,
                    state["checked_at"],
                    current_time,
                    interval,
                    state["expected_session"],
                ):
                    active_job = None
                    if state["last_job_id"]:
                        active_job = connection.execute(
                            text(
                                """
                                SELECT * FROM ingestion_jobs
                                WHERE job_id = :job_id
                                  AND status IN ('QUEUED', 'PROCESSING', 'RATE_LIMITED')
                                """
                            ),
                            {"job_id": state["last_job_id"]},
                        ).mappings().one_or_none()
                    if active_job:
                        jobs.append(_refresh_job_dict(active_job))
                    continue

                queued_job, _ = enqueue_ingestion(
                    ticker,
                    engine=db_engine,
                    connection=connection,
                    force_refresh=True,
                    priority=100 if ticker == selected else 0,
                )
                if queued_job is None:
                    continue
                job_id = queued_job.get("job_id")
                connection.execute(
                    text(
                        """
                        INSERT INTO market_refresh_state
                            (ticker, checked_at, last_job_id, last_status, expected_session)
                        VALUES (:ticker, :checked_at, :job_id, :status, :expected_session)
                        ON CONFLICT (ticker) DO UPDATE
                        SET checked_at = EXCLUDED.checked_at,
                            last_job_id = EXCLUDED.last_job_id,
                            last_status = EXCLUDED.last_status,
                            expected_session = EXCLUDED.expected_session
                        """
                    ),
                    {
                        "ticker": ticker,
                        "checked_at": current_time,
                        "job_id": job_id,
                        "status": queued_job["status"],
                        "expected_session": expected_session,
                    },
                )
                if job_id:
                    jobs.append(queued_job)

        with db_engine.connect() as connection:
            latest_market_date = connection.execute(
                text(
                    """
                    SELECT MAX(p.price_date)
                    FROM stock_prices p
                    JOIN stocks s ON s.ticker = p.ticker
                    WHERE s.is_synthetic = FALSE
                    """
                )
            ).scalar_one_or_none()
        return {
            "expected_trading_date": expected_session.isoformat(),
            "latest_data_date": (
                latest_market_date.isoformat() if latest_market_date else None
            ),
            "checked_tickers": checked_tickers,
            "jobs": jobs,
        }
    finally:
        if owns_engine:
            db_engine.dispose()


def _refresh_job_dict(row) -> dict:
    return {
        "job_id": str(row["job_id"]),
        "ticker": row["ticker"],
        "status": row["status"],
        "attempt_count": row["attempt_count"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
        "next_attempt_at": row["next_attempt_at"],
        "last_error": row["last_error"],
        "records_upserted": row["records_upserted"],
    }
