from backend.app.anomalies import classify_anomaly, detection_details


def test_classify_anomaly_marks_combined_price_and_volume_signals_high():
    severity, indicators = classify_anomaly(3.0, 3.0)

    assert severity == "HIGH"
    assert indicators == ["PRICE_RETURN", "RELATIVE_VOLUME"]


def test_classify_anomaly_marks_single_extreme_signal_medium():
    severity, indicators = classify_anomaly(-4.0, None)

    assert severity == "MEDIUM"
    assert indicators == ["PRICE_RETURN"]


def test_classify_anomaly_leaves_normal_or_unavailable_signals_unflagged():
    assert classify_anomaly(None, None) == (None, [])
    assert classify_anomaly(1.2, 1.4) == (None, [])


def test_classify_anomaly_marks_lower_threshold_signal_low():
    severity, indicators = classify_anomaly(2.6, None)

    assert severity == "LOW"
    assert indicators == ["PRICE_RETURN"]


def test_volatility_is_descriptive_and_never_triggers_an_anomaly():
    assert classify_anomaly(0.2, 1.4) == (None, [])


def test_detection_details_explain_the_trigger_and_prior_baseline():
    details = detection_details(
        {
            "return_z_score": -2.8,
            "historical_return_mean": 0.001,
            "historical_return_stddev": 0.01,
            "volume_ratio": 3.2,
            "volume": 3_200,
            "historical_volume_mean": 1_000,
        },
        ["PRICE_RETURN", "RELATIVE_VOLUME"],
    )

    assert [detail["method"] for detail in details] == [
        "rolling_z_score",
        "relative_volume",
    ]
    assert details[0]["value"] == -2.8
    assert details[0]["baseline_observations"] == 60
    assert details[1]["baseline_mean"] == 1_000
    assert details[1]["baseline_observations"] == 20
