ALTER TABLE market_refresh_state
    ADD COLUMN IF NOT EXISTS expected_session DATE;
