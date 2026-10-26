CREATE TABLE IF NOT EXISTS dashboard_sessions (
    session_id UUID PRIMARY KEY,
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS dashboard_sessions_expires_at_idx
    ON dashboard_sessions (expires_at);
