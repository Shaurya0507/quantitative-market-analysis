from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine, create_engine, text

from backend.app.settings import get_database_url

ENQUEUE_JOB = text(
    """
    INSERT INTO ingestion_jobs (job_id, ticker, status, priority)
    VALUES (:job_id, :ticker, 'QUEUED', :priority)
    ON CONFLICT DO NOTHING
    RETURNING job_id, status, ticker, created_at
    """
)


def _job_dict(row) -> dict:
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


def get_job(job_id: UUID, *, engine: Engine | None = None) -> dict | None:
    owns_engine = engine is None
    db_engine = engine or create_engine(get_database_url())
    try:
        with db_engine.connect() as connection:
            row = connection.execute(
                text("SELECT * FROM ingestion_jobs WHERE job_id = :job_id"),
                {"job_id": job_id},
            ).mappings().one_or_none()
            return _job_dict(row) if row else None
    finally:
        if owns_engine:
            db_engine.dispose()


def enqueue_ingestion(
    ticker: str,
    *,
    engine: Engine | None = None,
    connection: Connection | None = None,
    force_refresh: bool = False,
    priority: int = 0,
) -> tuple[dict | None, bool]:
    ticker = ticker.upper()
    owns_engine = engine is None and connection is None
    db_engine = engine or (create_engine(get_database_url()) if connection is None else None)

    def enqueue(connection_to_use: Connection) -> tuple[dict | None, bool]:
        connection_to_use.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:ticker))"),
            {"ticker": ticker},
        )
        stock = connection_to_use.execute(
            text(
                """
                SELECT latest_available_date, last_successful_ingestion
                FROM stocks
                WHERE ticker = :ticker AND is_synthetic = FALSE
                """
            ),
            {"ticker": ticker},
        ).mappings().one_or_none()
        if not force_refresh and stock and stock["latest_available_date"]:
            freshness = connection_to_use.execute(
                text(
                    """
                    SELECT
                        COALESCE(
                            :last_ingestion >= NOW() - INTERVAL '20 hours',
                            FALSE
                        )
                        OR :latest_date >= CURRENT_DATE - 4 AS is_fresh
                    """
                ),
                {
                    "last_ingestion": stock["last_successful_ingestion"],
                    "latest_date": stock["latest_available_date"],
                },
            ).scalar_one()
            if freshness:
                return {
                    "job_id": None,
                    "ticker": ticker,
                    "status": "CACHED",
                    "attempt_count": 0,
                    "last_error": None,
                    "records_upserted": None,
                }, False

        inserted = connection_to_use.execute(
            ENQUEUE_JOB,
            {"ticker": ticker, "job_id": uuid4(), "priority": priority},
        ).mappings().one_or_none()
        if inserted:
            connection_to_use.execute(
                text(
                    """
                    UPDATE stocks SET ingestion_status = 'QUEUED'
                    WHERE ticker = :ticker AND is_synthetic = FALSE
                    """
                ),
                {"ticker": ticker},
            )
            row = connection_to_use.execute(
                text("SELECT * FROM ingestion_jobs WHERE job_id = :job_id"),
                {"job_id": inserted["job_id"]},
            ).mappings().one()
            return _job_dict(row), True

        active = connection_to_use.execute(
            text(
                """
                SELECT *
                FROM ingestion_jobs
                WHERE ticker = :ticker
                  AND status IN ('QUEUED', 'PROCESSING', 'RATE_LIMITED')
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {"ticker": ticker},
        ).mappings().one_or_none()
        if active:
            if priority > active["priority"]:
                connection_to_use.execute(
                    text(
                        "UPDATE ingestion_jobs SET priority = :priority WHERE job_id = :job_id"
                    ),
                    {"priority": priority, "job_id": active["job_id"]},
                )
                active = {**active, "priority": priority}
            return _job_dict(active), False

        failed = connection_to_use.execute(
            text(
                """
                SELECT *
                FROM ingestion_jobs
                WHERE ticker = :ticker
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {"ticker": ticker},
        ).mappings().one_or_none()
        return (_job_dict(failed) if failed else None), False

    try:
        if connection is not None:
            return enqueue(connection)
        assert db_engine is not None
        with db_engine.begin() as transaction_connection:
            return enqueue(transaction_connection)
    finally:
        if owns_engine and db_engine is not None:
            db_engine.dispose()


def get_ticker_status(ticker: str, *, engine: Engine | None = None) -> dict | None:
    owns_engine = engine is None
    db_engine = engine or create_engine(get_database_url())
    try:
        with db_engine.connect() as connection:
            stock = connection.execute(
                text(
                    """
                    SELECT ticker, latest_available_date, last_successful_ingestion,
                           data_source, ingestion_status
                    FROM stocks
                    WHERE ticker = :ticker AND is_synthetic = FALSE
                    """
                ),
                {"ticker": ticker.upper()},
            ).mappings().one_or_none()
            job = connection.execute(
                text(
                    """
                    SELECT job_id, ticker, status, attempt_count, created_at, started_at,
                           completed_at, next_attempt_at, last_error, records_upserted
                    FROM ingestion_jobs
                    WHERE ticker = :ticker
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                ),
                {"ticker": ticker.upper()},
            ).mappings().one_or_none()
            return {
                "ticker": ticker.upper(),
                "stock": dict(stock) if stock else None,
                "job": _job_dict(job) if job else None,
            }
    finally:
        if owns_engine:
            db_engine.dispose()


def update_stock_job_status(
    ticker: str,
    status: str,
    *,
    engine: Engine | None = None,
) -> None:
    owns_engine = engine is None
    db_engine = engine or create_engine(get_database_url())
    try:
        with db_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE stocks
                    SET ingestion_status = :status
                    WHERE ticker = :ticker AND is_synthetic = FALSE
                    """
                ),
                {"ticker": ticker, "status": status},
            )
    finally:
        if owns_engine:
            db_engine.dispose()
