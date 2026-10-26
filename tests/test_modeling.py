from datetime import date, timedelta

import numpy as np
import backend.app.modeling as modeling
from backend.app.modeling import (
    FEATURE_NAMES,
    analyze_rows,
    score_garch,
    score_local_outlier_factor,
    score_relative_volume,
    score_rolling_z,
)


def make_model_rows(count=420):
    rng = np.random.default_rng(20261024)
    returns = []
    volatility = 0.01
    for index in range(count):
        shock = float(rng.normal(0, volatility))
        returns.append(shock)
        volatility = 0.008 + 0.12 * abs(shock) + 0.75 * volatility

    start = date(2020, 1, 1)
    rows = []
    for index, daily_return in enumerate(returns):
        rows.append(
            {
                "ticker": "TEST",
                "price_date": start + timedelta(days=index),
                "daily_return": daily_return,
                "return_z_score": float(rng.normal()),
                "volume_ratio": float(max(0.1, rng.lognormal(0, 0.2))),
                "volume": float(rng.integers(100_000, 2_000_000)),
                "historical_volume_mean": 750_000.0,
                "volume_observations": 20,
                "volume_history_start": start + timedelta(days=max(index - 20, 0)),
                "rolling_volatility": float(abs(rng.normal(0.01, 0.002))),
                "intraday_range": float(abs(rng.normal(0.02, 0.004))),
                "historical_return_mean": 0.0002,
                "historical_return_stddev": 0.01,
                "return_observations": 60,
            }
        )
    return rows


def test_rolling_z_score_applies_threshold_and_uses_prior_baseline():
    rows = make_model_rows(4)
    rows[0]["return_z_score"] = None
    rows[1]["return_z_score"] = 2.5
    rows[2]["return_z_score"] = -2.4
    rows[3]["return_z_score"] = None

    result = score_rolling_z(rows)

    assert result["status"] == "SUCCEEDED"
    assert [row["classification"] for row in result["results"]] == ["ANOMALY", "NORMAL"]
    assert result["results"][0]["training_end"] < result["results"][0]["price_date"]
    assert result["results"][0]["features"]["historical_mean"] == 0.0002


def test_models_report_insufficient_history_without_unreliable_scores():
    rows = make_model_rows(59)

    lof = score_local_outlier_factor(rows)
    garch = score_garch(rows)

    assert lof["status"] == "INSUFFICIENT_DATA"
    assert lof["results"] == []
    assert "No score was generated" in lof["message"]
    assert garch["status"] == "INSUFFICIENT_DATA"
    assert garch["results"] == []
    assert "No estimate was generated" in garch["message"]


def test_lof_fits_scaler_on_training_rows_and_scores_later_rows_only():
    rows = make_model_rows()
    first = score_local_outlier_factor(rows)
    modified = [dict(row) for row in rows]
    for row in modified[-20:]:
        row["daily_return"] = 10000
        row["volume_ratio"] = 10000
        row["intraday_range"] = 10000
    second = score_local_outlier_factor(modified)

    assert first["status"] == second["status"] == "SUCCEEDED"
    assert first["training_observations"] == second["training_observations"] == 50
    assert first["evaluation_observations"] == len(rows) - 50
    assert first["results"][0]["training_end"] < first["results"][0]["price_date"]
    assert (
        first["parameters"]["scaler_mean"]
        == second["parameters"]["scaler_mean"]
    )
    assert all("training_observations" not in result["features"] for result in first["results"])
    assert set(FEATURE_NAMES) == set(first["results"][0]["features"])
    assert (
        first["results"][-1]["anomaly_score"]
        != second["results"][-1]["anomaly_score"]
    )


def test_relative_volume_uses_only_a_prior_twenty_observation_baseline():
    rows = make_model_rows(3)
    rows[0]["volume_ratio"] = None
    rows[1]["volume_ratio"] = 2.5
    rows[2]["volume_ratio"] = 1.4

    result = score_relative_volume(rows)

    assert result["status"] == "SUCCEEDED"
    assert [row["classification"] for row in result["results"]] == ["ANOMALY", "NORMAL"]
    assert result["results"][0]["training_end"] < result["results"][0]["price_date"]
    assert result["results"][0]["features"]["volume_observations"] == 20


def test_garch_fit_uses_only_chronological_training_returns():
    rows = make_model_rows()
    first = score_garch(rows)
    modified = [dict(row) for row in rows]
    modified[first["training_observations"]]["daily_return"] = 0.5
    second = score_garch(modified)

    assert first["status"] == second["status"] == "SUCCEEDED"
    assert first["training_observations"] == second["training_observations"]
    assert first["results"][0]["training_end"] < first["results"][0]["price_date"]
    assert first["parameters"] == second["parameters"]
    assert first["results"][0]["features"]["estimated_conditional_volatility"] > 0
    assert first["results"][0]["threshold"] == 2.5
    assert (
        first["results"][0]["features"]["standardized_residual"]
        != second["results"][0]["features"]["standardized_residual"]
    )


def test_zero_standard_deviation_is_not_scored_as_a_valid_z_score():
    rows = make_model_rows(3)
    for row in rows:
        row["return_z_score"] = None
        row["historical_return_stddev"] = 0

    result = score_rolling_z(rows)

    assert result["status"] == "INSUFFICIENT_DATA"
    assert result["results"] == []


def test_a_failed_model_does_not_suppress_other_model_results(monkeypatch):
    expected = {"method": "rolling_z_score", "status": "SUCCEEDED", "results": []}
    monkeypatch.setattr(modeling, "score_rolling_z", lambda _rows: expected)

    def fail_lof(_rows):
        raise ValueError("unexpected model input")

    monkeypatch.setattr(modeling, "score_relative_volume", fail_lof)

    analyses = analyze_rows([])

    assert analyses[0] is expected
    assert analyses[1]["method"] == "relative_volume"
    assert analyses[1]["status"] == "FAILED"
    assert "ValueError" in analyses[1]["message"]
    assert analyses[2]["method"] == "local_outlier_factor"
    assert analyses[2]["status"] == "INSUFFICIENT_DATA"
