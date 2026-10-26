from pathlib import Path

from sqlalchemy import create_engine, text

from backend.app.settings import PROJECT_ROOT, get_database_url, get_supported_stocks

MIGRATIONS = PROJECT_ROOT / "database" / "migrations"


def run_migrations() -> None:
    engine = create_engine(get_database_url(), pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version VARCHAR(100) PRIMARY KEY,
                        applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
            )
            for migration_path in sorted(MIGRATIONS.glob("*.sql")):
                version = migration_path.name
                applied = connection.execute(
                    text("SELECT 1 FROM schema_migrations WHERE version = :version"),
                    {"version": version},
                ).scalar_one_or_none()
                if applied:
                    continue

                sql = migration_path.read_text(encoding="utf-8")
                statements = [
                    statement.strip()
                    for statement in sql.split(";")
                    if statement.strip()
                ]
                for statement in statements:
                    connection.execute(text(statement))
                connection.execute(
                    text("INSERT INTO schema_migrations (version) VALUES (:version)"),
                    {"version": version},
                )
                print(f"Applied database migration {version}")
            for stock in get_supported_stocks().values():
                connection.execute(
                    text(
                        """
                        UPDATE stocks
                        SET company_name = :company_name,
                            sector = :sector,
                            exchange = :exchange,
                            data_source = COALESCE(data_source, 'alphavantage')
                        WHERE ticker = :ticker AND is_synthetic = FALSE
                        """
                    ),
                    stock,
                )
    finally:
        engine.dispose()


if __name__ == "__main__":
    run_migrations()
