ALTER TABLE anomaly_results
    ADD COLUMN IF NOT EXISTS detection_details JSONB NOT NULL DEFAULT '[]'::JSONB;

ALTER TABLE anomaly_model_runs
    DROP CONSTRAINT IF EXISTS anomaly_model_runs_method_check;

ALTER TABLE anomaly_model_runs
    ADD CONSTRAINT anomaly_model_runs_method_check
    CHECK (
        method IN (
            'rolling_z_score',
            'relative_volume',
            'local_outlier_factor',
            'garch_1_1'
        )
    );

ALTER TABLE anomaly_model_results
    DROP CONSTRAINT IF EXISTS anomaly_model_results_method_check;

ALTER TABLE anomaly_model_results
    ADD CONSTRAINT anomaly_model_results_method_check
    CHECK (
        method IN (
            'rolling_z_score',
            'relative_volume',
            'local_outlier_factor',
            'garch_1_1'
        )
    );

CREATE INDEX IF NOT EXISTS anomaly_results_current_version_idx
    ON anomaly_results (detection_version, price_date DESC, ticker);
