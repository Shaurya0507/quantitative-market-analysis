from uuid import uuid4

from backend.app.session_lifecycle import (
    cleanup_expired_dashboard_sessions,
    register_dashboard_session,
)


class FakeResult:
    def __init__(self, *, scalar=False, rowcount=0):
        self.scalar = scalar
        self.rowcount = rowcount

    def scalar_one(self):
        return self.scalar


class FakeConnection:
    def __init__(self, *, has_active_sessions, deleted_prices=0):
        self.has_active_sessions = has_active_sessions
        self.deleted_prices = deleted_prices
        self.statements = []

    def execute(self, statement, parameters=None):
        sql = str(statement)
        self.statements.append((sql, parameters or {}))
        if "SELECT EXISTS" in sql:
            return FakeResult(scalar=self.has_active_sessions)
        if "DELETE FROM stock_prices" in sql:
            return FakeResult(rowcount=self.deleted_prices)
        return FakeResult()


class FakeTransaction:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, *_):
        return False


class FakeEngine:
    def __init__(self, connection):
        self.connection = connection

    def begin(self):
        return FakeTransaction(self.connection)


def test_expired_session_cleanup_preserves_data_while_another_tab_is_active():
    connection = FakeConnection(has_active_sessions=True)

    deleted_prices = cleanup_expired_dashboard_sessions(FakeEngine(connection))

    assert deleted_prices == 0
    assert not any("DELETE FROM stock_prices" in sql for sql, _ in connection.statements)


def test_expired_session_cleanup_clears_only_real_stock_cache():
    connection = FakeConnection(has_active_sessions=False, deleted_prices=12)

    deleted_prices = cleanup_expired_dashboard_sessions(FakeEngine(connection))

    assert deleted_prices == 12
    cleanup_statements = [
        sql for sql, _ in connection.statements
        if (
            sql.strip().startswith("DELETE FROM")
            or sql.strip().startswith("UPDATE stocks")
        )
        and "dashboard_sessions" not in sql
    ]
    assert any("DELETE FROM stock_prices" in sql for sql in cleanup_statements)
    assert all("is_synthetic = FALSE" in sql for sql in cleanup_statements)


def test_new_session_clears_expired_cache_then_registers_session():
    connection = FakeConnection(has_active_sessions=False, deleted_prices=7)
    session_id = uuid4()

    cache_reset, deleted_prices = register_dashboard_session(FakeEngine(connection), session_id)

    assert cache_reset is True
    assert deleted_prices == 7
    insert = next(
        (sql, parameters)
        for sql, parameters in connection.statements
        if "INSERT INTO dashboard_sessions" in sql
    )
    assert insert[1]["session_id"] == session_id
    assert insert[1]["ttl_seconds"] == 60


def test_new_session_reports_reset_even_when_cache_is_already_empty():
    connection = FakeConnection(has_active_sessions=False, deleted_prices=0)

    cache_reset, deleted_prices = register_dashboard_session(
        FakeEngine(connection),
        uuid4(),
    )

    assert cache_reset is True
    assert deleted_prices == 0
