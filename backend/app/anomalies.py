import json
import math
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, text

from backend.app.settings import PROJECT_ROOT, get_database_url

RULES = json.loads(
    (Path(__file__).with_name("anomaly_rules.json")).read_text(encoding="utf-8")
)
ANOMALY_VERSION = RULES["version"]
INDICATOR_SQL = (PROJECT_ROOT / "database" / "anomaly_indicators.sql").read_text(
    encoding="utf-8"
)


def _finite(value: Any) -> float | None:
    if value is None:
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def classify_anomaly(
    return_z_score: float | None,
    volume_ratio: float | None,
) -> tuple[str | None, list[str]]:
    price_trigger = (
        return_z_score is not None
        and abs(return_z_score) >= RULES["price_z_score"]
    )
    volume_trigger = (
        volume_ratio is not None and volume_ratio >= RULES["volume_ratio"]
    )
    if not (price_trigger or volume_trigger):
        return None, []

    triggered = []
    if price_trigger:
        triggered.append("PRICE_RETURN")
    if volume_trigger:
        triggered.append("RELATIVE_VOLUME")
    if price_trigger and volume_trigger:
        severity = "HIGH"
    elif (
        (return_z_score is not None and abs(return_z_score) >= RULES["high_price_z_score"])
        or (volume_ratio is not None and volume_ratio >= RULES["high_volume_ratio"])
        or (
            return_z_score is not None
            and abs(return_z_score) >= 2.0
            and volume_ratio is not None
            and volume_ratio >= 2.0
        )
    ):
        severity = "MEDIUM"
    else:
        severity = "LOW"
    return severity, triggered


def detection_details(row: dict[str, Any], triggered: list[str]) -> list[dict[str, Any]]:
    details = []
    if "PRICE_RETURN" in triggered:
        details.append(
            {
                "method": "rolling_z_score",
                "indicator": "return_z_score",
                "value": _finite(row.get("return_z_score")),
                "threshold": RULES["price_z_score"],
                "condition": "abs(value) >= threshold",
                "baseline_observations": RULES["return_history_days"],
                "baseline_mean": _finite(row.get("historical_return_mean")),
                "baseline_stddev": _finite(row.get("historical_return_stddev")),
                "model_version": ANOMALY_VERSION,
            }
        )
    if "RELATIVE_VOLUME" in triggered:
        details.append(
            {
                "method": "relative_volume",
                "indicator": "volume_ratio",
                "value": _finite(row.get("volume_ratio")),
                "threshold": RULES["volume_ratio"],
                "condition": "value >= threshold",
                "current_volume": _finite(row.get("volume")),
                "baseline_mean": _finite(row.get("historical_volume_mean")),
                "baseline_observations": RULES["volume_history_days"],
                "model_version": ANOMALY_VERSION,
            }
        )
    return details


def explain_anomaly(row: dict[str, Any], triggered: list[str]) -> str:
    ticker = row["ticker"]
    parts = [f"{ticker} showed unusual historical behavior on {row['price_date']}."]
    if "PRICE_RETURN" in triggered:
        daily_return = float(row["daily_return"]) * 100
        z_score = float(row["return_z_score"])
        direction = "positive" if daily_return >= 0 else "negative"
        parts.append(
            f"Its {direction} daily return was {daily_return:.2f}%, "
            f"{abs(z_score):.2f} standard deviations "
            f"{'above' if z_score >= 0 else 'below'} its prior 60-trading-day mean."
        )
    if "RELATIVE_VOLUME" in triggered:
        parts.append(
            f"Trading volume was {float(row['volume_ratio']):.2f} times its "
            "prior 20-trading-day average."
        )
    return " ".join(parts)


def process_ticker_anomalies(
    ticker: str,
    *,
    engine: Engine | None = None,
) -> int:
    owns_engine = engine is None
    db_engine = engine or create_engine(get_database_url(), pool_pre_ping=True)
    ticker = ticker.upper()
    try:
        with db_engine.begin() as connection:
            stock_exists = connection.execute(
                text(
                    """
                    SELECT 1 FROM stocks
                    WHERE ticker = :ticker AND is_synthetic = FALSE
                    """
                ),
                {"ticker": ticker},
            ).scalar_one_or_none()
            if stock_exists is None:
                return 0

            indicator_rows = connection.execute(
                text(INDICATOR_SQL),
                {
                    "ticker": ticker,
                    "return_history_days": RULES["return_history_days"],
                    "volume_history_days": RULES["volume_history_days"],
                    "volatility_days": RULES["volatility_days"],
                    "volatility_baseline_days": RULES["volatility_baseline_days"],
                },
            ).mappings()
            anomalies = []
            for result in indicator_rows:
                row = dict(result)
                z_score = _finite(row["return_z_score"])
                volume_ratio = _finite(row["volume_ratio"])
                severity, triggered = classify_anomaly(
                    z_score,
                    volume_ratio,
                )
                if severity is None:
                    continue
                row["return_z_score"] = z_score
                row["volume_ratio"] = volume_ratio
                method_names = {
                    "PRICE_RETURN": "rolling_z_score",
                    "RELATIVE_VOLUME": "relative_volume",
                }
                methods = [method_names[indicator] for indicator in triggered]
                anomalies.append(
                    {
                        "ticker": ticker,
                        "price_date": row["price_date"],
                        "daily_return": row["daily_return"],
                        "return_z_score": z_score,
                        "volume_ratio": volume_ratio,
                        "rolling_volatility": _finite(row["rolling_volatility"]),
                        "intraday_range": _finite(row["intraday_range"]),
                        "severity": severity,
                        "explanation": explain_anomaly(row, triggered),
                        "triggered_indicators": json.dumps(triggered),
                        "detection_details": json.dumps(
                            detection_details(row, triggered)
                        ),
                        "detection_method": (
                            methods[0] if len(methods) == 1 else "combined_statistical"
                        ),
                        "detection_version": ANOMALY_VERSION,
                    }
                )

            connection.execute(
                text(
                    """
                    DELETE FROM anomaly_results
                    WHERE ticker = :ticker AND detection_version = :version
                    """
                ),
                {"ticker": ticker, "version": ANOMALY_VERSION},
            )
            if anomalies:
                connection.execute(
                    text(
                        """
                        INSERT INTO anomaly_results (
                            ticker, price_date, daily_return, return_z_score,
                            volume_ratio, rolling_volatility, intraday_range,
                            severity, explanation, triggered_indicators,
                            detection_method, detection_version, detection_details
                        )
                        VALUES (
                            :ticker, :price_date, :daily_return, :return_z_score,
                            :volume_ratio, :rolling_volatility, :intraday_range,
                            :severity, :explanation, CAST(:triggered_indicators AS JSONB),
                            :detection_method, :detection_version,
                            CAST(:detection_details AS JSONB)
                        )
                        ON CONFLICT (ticker, price_date, detection_version)
                        DO UPDATE SET
                            daily_return = EXCLUDED.daily_return,
                            return_z_score = EXCLUDED.return_z_score,
                            volume_ratio = EXCLUDED.volume_ratio,
                            rolling_volatility = EXCLUDED.rolling_volatility,
                            intraday_range = EXCLUDED.intraday_range,
                            severity = EXCLUDED.severity,
                            explanation = EXCLUDED.explanation,
                            triggered_indicators = EXCLUDED.triggered_indicators,
                            detection_method = EXCLUDED.detection_method,
                            detection_details = EXCLUDED.detection_details,
                            created_at = NOW()
                        """
                    ),
                    anomalies,
                )
            return len(anomalies)
    finally:
        if owns_engine:
            db_engine.dispose()
