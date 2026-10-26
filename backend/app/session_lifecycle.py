from uuid import UUID

from sqlalchemy import Engine, text

SESSION_TTL_SECONDS = 60
SESSION_LOCK_KEY = "marketlens_dashboard_cache"


def _lock_dashboard_cache(connection) -> None:
    connection.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
        {"lock_key": SESSION_LOCK_KEY},
    )


def _clear_real_stock_data(connection) -> int:
    for table in (
        "anomaly_model_results",
        "anomaly_model_runs",
        "anomaly_results",
        "market_refresh_state",
    ):
        connection.execute(
            text(
                f"""
                DELETE FROM {table}
                WHERE ticker IN (
                    SELECT ticker FROM stocks WHERE is_synthetic = FALSE
                )
                """
            )
        )

    deleted_prices = connection.execute(
        text(
            """
            DELETE FROM stock_prices
            WHERE ticker IN (
                SELECT ticker FROM stocks WHERE is_synthetic = FALSE
            )
            """
        )
    ).rowcount
    connection.execute(
        text(
            """
            DELETE FROM ingestion_jobs
            WHERE status IN ('COMPLETED', 'FAILED')
              AND ticker IN (
                  SELECT ticker FROM stocks WHERE is_synthetic = FALSE
              )
            """
        )
    )
    connection.execute(
        text(
            """
            UPDATE stocks
            SET latest_available_date = NULL,
                last_successful_ingestion = NULL,
                ingestion_status = 'NOT_LOADED'
            WHERE is_synthetic = FALSE
            """
        )
    )
    return deleted_prices


def register_dashboard_session(engine: Engine, session_id: UUID) -> tuple[bool, int]:
    with engine.begin() as connection:
        _lock_dashboard_cache(connection)
        connection.execute(
            text("DELETE FROM dashboard_sessions WHERE expires_at <= NOW()")
        )
        has_active_sessions = connection.execute(
            text("SELECT EXISTS (SELECT 1 FROM dashboard_sessions WHERE expires_at > NOW())")
        ).scalar_one()
        cache_reset = not has_active_sessions
        deleted_prices = 0
        if cache_reset:
            deleted_prices = _clear_real_stock_data(connection)

        connection.execute(
            text(
                """
                INSERT INTO dashboard_sessions (session_id, expires_at)
                VALUES (:session_id, NOW() + make_interval(secs => :ttl_seconds))
                ON CONFLICT (session_id) DO UPDATE
                SET expires_at = EXCLUDED.expires_at
                """
            ),
            {"session_id": session_id, "ttl_seconds": SESSION_TTL_SECONDS},
        )
        return cache_reset, deleted_prices


def cleanup_expired_dashboard_sessions(engine: Engine) -> int:
    with engine.begin() as connection:
        _lock_dashboard_cache(connection)
        connection.execute(
            text("DELETE FROM dashboard_sessions WHERE expires_at <= NOW()")
        )
        has_active_sessions = connection.execute(
            text("SELECT EXISTS (SELECT 1 FROM dashboard_sessions WHERE expires_at > NOW())")
        ).scalar_one()
        if has_active_sessions:
            return 0
        return _clear_real_stock_data(connection)
