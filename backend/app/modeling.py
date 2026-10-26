import json
import logging
import math
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
from arch import arch_model
from sklearn.neighbors import LocalOutlierFactor
from sklearn.preprocessing import StandardScaler
from sqlalchemy import Engine, create_engine, text

from backend.app.anomalies import INDICATOR_SQL, RULES
from backend.app.settings import get_database_url

logger = logging.getLogger("marketlens.models")
MODEL_RULES = json.loads(
    (Path(__file__).with_name("quantitative_model_rules.json")).read_text(
        encoding="utf-8"
    )
)
MODEL_VERSION = MODEL_RULES["version"]
LOF_FEATURE_NAMES = tuple(MODEL_RULES["local_outlier_factor"]["feature_names"])
FEATURE_NAMES = (
    "daily_return",
    "volume_ratio",
    "intraday_range",
)


def _finite_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _json_safe(value: Any) -> Any:
    if isinstance(value, (date,)):
        return value.isoformat()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _score_result(
    row: dict[str, Any],
    *,
    method: str,
    score: float,
    threshold: float,
    anomaly: bool,
    training_start: date | None,
    training_end: date | None,
    parameters: dict[str, Any],
    features: dict[str, Any],
) -> dict[str, Any]:
    return {
        "ticker": row["ticker"],
        "price_date": row["price_date"],
        "method": method,
        "model_version": MODEL_VERSION,
        "anomaly_score": float(score),
        "threshold": float(threshold),
        "classification": "ANOMALY" if anomaly else "NORMAL",
        "training_start": training_start,
        "training_end": training_end,
        "parameters": _json_safe(parameters),
        "features": _json_safe(features),
    }


def score_rolling_z(rows: list[dict[str, Any]]) -> dict[str, Any]:
    settings = MODEL_RULES["rolling_z_score"]
    eligible = []
    for index, row in enumerate(rows):
        score = _finite_number(row.get("return_z_score"))
        if score is None:
            continue
        previous = rows[index - 1] if index else None
        baseline_start = rows[
            max(0, index - settings["window"])
        ]["price_date"]
        baseline_end = previous["price_date"] if previous else None
        eligible.append(
            _score_result(
                row,
                method="rolling_z_score",
                score=score,
                threshold=settings["threshold"],
                anomaly=abs(score) >= settings["threshold"],
                training_start=baseline_start,
                training_end=baseline_end,
                parameters={
                    **settings,
                    "return_window": RULES["return_history_days"],
                    "standard_deviation": "sample",
                    "training_observations": row.get("return_observations"),
                    "model_version": MODEL_VERSION,
                },
                features={
                    "daily_return": _finite_number(row.get("daily_return")),
                    "historical_mean": _finite_number(row.get("historical_return_mean")),
                    "historical_stddev": _finite_number(
                        row.get("historical_return_stddev")
                    ),
                    "return_z_score": score,
                    "return_observations": row.get("return_observations"),
                },
            )
        )
    return {
        "method": "rolling_z_score",
        "status": "SUCCEEDED" if eligible else "INSUFFICIENT_DATA",
        "results": eligible,
        "training_observations": min(
            (row["features"]["return_observations"] or 0 for row in eligible),
            default=0,
        ),
        "evaluation_observations": len(eligible),
        "parameters": settings,
        "message": None if eligible else "At least 60 prior trading-day returns are required.",
    }


def score_relative_volume(rows: list[dict[str, Any]]) -> dict[str, Any]:
    settings = MODEL_RULES["relative_volume"]
    eligible = []
    for index, row in enumerate(rows):
        ratio = _finite_number(row.get("volume_ratio"))
        if ratio is None:
            continue
        previous = rows[index - 1] if index else None
        eligible.append(
            _score_result(
                row,
                method="relative_volume",
                score=ratio,
                threshold=settings["threshold"],
                anomaly=ratio >= settings["threshold"],
                training_start=row.get("volume_history_start"),
                training_end=previous["price_date"] if previous else None,
                parameters={
                    **settings,
                    "training_observations": row.get("volume_observations"),
                    "comparison": settings["comparison"],
                },
                features={
                    "current_volume": _finite_number(row.get("volume")),
                    "historical_volume_mean": _finite_number(
                        row.get("historical_volume_mean")
                    ),
                    "volume_ratio": ratio,
                    "volume_observations": row.get("volume_observations"),
                },
            )
        )
    return {
        "method": "relative_volume",
        "status": "SUCCEEDED" if eligible else "INSUFFICIENT_DATA",
        "results": eligible,
        "training_observations": settings["window"] if eligible else 0,
        "evaluation_observations": len(eligible),
        "parameters": settings,
        "message": None if eligible else (
            f"At least {settings['window']} prior trading-day volumes are required."
        ),
    }


def _feature_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    eligible = []
    for row in rows:
        values = {name: _finite_number(row.get(name)) for name in LOF_FEATURE_NAMES}
        if all(value is not None for value in values.values()):
            eligible.append({**row, "model_features": values})
    return eligible


def _chronological_split(
    row_count: int,
    *,
    minimum_training_observations: int,
    minimum_evaluation_observations: int,
) -> int | None:
    if row_count < minimum_training_observations + minimum_evaluation_observations:
        return None
    return max(minimum_training_observations, int(row_count * 0.7))


def score_local_outlier_factor(rows: list[dict[str, Any]]) -> dict[str, Any]:
    settings = MODEL_RULES["local_outlier_factor"]
    eligible = _feature_rows(rows)
    minimum_total = (
        settings["minimum_training_observations"]
        + settings["minimum_evaluation_observations"]
    )
    split = (
        settings["minimum_training_observations"]
        if len(eligible) >= minimum_total
        else None
    )
    parameters = {
        **settings,
        "feature_names": list(LOF_FEATURE_NAMES),
        "scaler": "StandardScaler fitted on training rows only",
        "novelty": True,
        "training_strategy": "fixed chronological initial training window; later observations are scored without refitting",
        "sample_quality": "exploratory",
    }
    if split is None:
        return {
            "method": "local_outlier_factor",
            "status": "INSUFFICIENT_DATA",
            "results": [],
            "training_observations": min(len(eligible), settings["minimum_training_observations"]),
            "evaluation_observations": 0,
            "parameters": parameters,
            "message": (
                "Exploratory LOF requires at least 50 complete training observations "
                "and 10 later evaluation observations. No score was generated."
            ),
        }

    training = eligible[:split]
    evaluation = eligible[split:]
    scaler = StandardScaler()
    training_matrix = scaler.fit_transform(
        [[row["model_features"][name] for name in LOF_FEATURE_NAMES] for row in training]
    )
    evaluation_matrix = scaler.transform(
        [[row["model_features"][name] for name in LOF_FEATURE_NAMES] for row in evaluation]
    )
    neighbors = min(settings["neighbors"], len(training) - 1)
    model = LocalOutlierFactor(
        n_neighbors=neighbors,
        contamination=settings["contamination"],
        novelty=True,
    )
    model.fit(training_matrix)
    score_flags = [
        (-float(model.score_samples(observation.reshape(1, -1))[0]),
         bool(model.predict(observation.reshape(1, -1))[0] == -1))
        for observation in evaluation_matrix
    ]
    threshold = float(-model.offset_)
    train_start = training[0]["price_date"]
    train_end = training[-1]["price_date"]
    actual_parameters = {
        **parameters,
        "neighbors_used": neighbors,
        "training_observations": len(training),
        "training_start": train_start.isoformat(),
        "training_end": train_end.isoformat(),
        "scaler_mean": scaler.mean_.tolist(),
        "scaler_scale": scaler.scale_.tolist(),
        "anomaly_score_threshold": threshold,
    }
    results = [
        _score_result(
            row,
            method="local_outlier_factor",
            score=float(score),
            threshold=threshold,
            anomaly=bool(flag),
            training_start=train_start,
            training_end=train_end,
            parameters=actual_parameters,
            features=row["model_features"],
        )
        for row, (score, flag) in zip(evaluation, score_flags, strict=True)
    ]
    return {
        "method": "local_outlier_factor",
        "status": "SUCCEEDED",
        "results": results,
        "training_observations": len(training),
        "evaluation_observations": len(evaluation),
        "parameters": actual_parameters,
        "message": "Exploratory conditional-volatility estimates; not a predictive-accuracy claim.",
    }


def score_garch(rows: list[dict[str, Any]]) -> dict[str, Any]:
    settings = MODEL_RULES["garch_1_1"]
    eligible = [
        {**row, "model_return": _finite_number(row.get("daily_return"))}
        for row in rows
        if _finite_number(row.get("daily_return")) is not None
    ]
    split = _chronological_split(
        len(eligible),
        minimum_training_observations=settings["minimum_training_observations"],
        minimum_evaluation_observations=settings["minimum_evaluation_observations"],
    )
    parameters = {
        **settings,
        "p": 1,
        "q": 1,
        "mean_specification": "zero conditional mean; no estimated intercept",
        "return_scale": "percent",
        "training_strategy": "chronological initial window; fixed parameters, one-step conditional variance recursion",
    }
    if split is None:
        return {
            "method": "garch_1_1",
            "status": "INSUFFICIENT_DATA",
            "results": [],
            "training_observations": min(len(eligible), settings["minimum_training_observations"]),
            "evaluation_observations": 0,
            "parameters": parameters,
            "message": (
                "GARCH(1,1) requires at least 252 historical daily returns and "
                "20 later evaluation returns. No estimate was generated."
            ),
        }

    training = eligible[:split]
    evaluation = eligible[split:]
    training_returns = np.asarray(
        [row["model_return"] * 100 for row in training],
        dtype=float,
    )
    fit = arch_model(
        training_returns,
        mean="Zero",
        vol="GARCH",
        p=1,
        q=1,
        dist="t",
        rescale=False,
    ).fit(disp="off", show_warning=False)
    if int(fit.convergence_flag) != 0:
        return {
            "method": "garch_1_1",
            "status": "FAILED",
            "results": [],
            "training_observations": len(training),
            "evaluation_observations": 0,
            "parameters": parameters,
            "message": f"GARCH optimizer did not converge (code {fit.convergence_flag}).",
        }

    omega = float(fit.params["omega"])
    alpha = float(fit.params["alpha[1]"])
    beta = float(fit.params["beta[1]"])
    last_variance = float(fit.conditional_volatility[-1] ** 2)
    last_shock = float(training_returns[-1])
    variance_parameters = {
        "omega_percent_squared": omega,
        "alpha_1": alpha,
        "beta_1": beta,
        "student_t_degrees_of_freedom": float(fit.params["nu"]),
        "convergence_flag": int(fit.convergence_flag),
    }
    actual_parameters = {
        **parameters,
        **variance_parameters,
        "training_observations": len(training),
        "training_start": training[0]["price_date"].isoformat(),
        "training_end": training[-1]["price_date"].isoformat(),
    }
    results = []
    for row in evaluation:
        variance = omega + alpha * (last_shock**2) + beta * last_variance
        if not math.isfinite(variance) or variance <= 0:
            return {
                "method": "garch_1_1",
                "status": "FAILED",
                "results": [],
                "training_observations": len(training),
                "evaluation_observations": 0,
                "parameters": actual_parameters,
                "message": "GARCH recursion produced an invalid conditional variance.",
            }
        sigma_percent = math.sqrt(variance)
        return_percent = row["model_return"] * 100
        standardized_residual = return_percent / sigma_percent
        results.append(
            _score_result(
                row,
                method="garch_1_1",
                score=standardized_residual,
                threshold=settings["shock_threshold"],
                anomaly=abs(standardized_residual) >= settings["shock_threshold"],
                training_start=training[0]["price_date"],
                training_end=training[-1]["price_date"],
                parameters=actual_parameters,
                features={
                    "daily_return": row["model_return"],
                    "estimated_conditional_volatility": sigma_percent / 100,
                    "standardized_residual": standardized_residual,
                },
            )
        )
        last_variance = variance
        last_shock = return_percent
    return {
        "method": "garch_1_1",
        "status": "SUCCEEDED",
        "results": results,
        "training_observations": len(training),
        "evaluation_observations": len(evaluation),
        "parameters": actual_parameters,
        "message": "Exploratory novelty scores; no labeled predictive-accuracy claim is made.",
    }


def analyze_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    analyses = []
    for method, scorer in (
        ("rolling_z_score", score_rolling_z),
        ("relative_volume", score_relative_volume),
        ("local_outlier_factor", score_local_outlier_factor),
    ):
        try:
            analyses.append(scorer(rows))
        except Exception as exc:
            logger.exception("Quantitative model %s failed during analysis", method)
            analyses.append(
                {
                    "method": method,
                    "status": "FAILED",
                    "results": [],
                    "training_observations": 0,
                    "evaluation_observations": 0,
                    "parameters": MODEL_RULES[method],
                    "message": f"Model processing failed ({type(exc).__name__}).",
                }
            )
    return analyses


def process_ticker_models(
    ticker: str,
    *,
    engine: Engine | None = None,
    force: bool = False,
) -> dict[str, Any]:
    owns_engine = engine is None
    db_engine = engine or create_engine(get_database_url(), pool_pre_ping=True)
    normalized_ticker = ticker.upper()
    try:
        with db_engine.begin() as connection:
            stock = connection.execute(
                text(
                    """
                    SELECT MAX(price_date) AS latest_price_date
                    FROM stocks s
                    JOIN stock_prices p ON p.ticker = s.ticker
                    WHERE s.ticker = :ticker AND s.is_synthetic = FALSE
                    """
                ),
                {"ticker": normalized_ticker},
            ).mappings().one()
            latest_price_date = stock["latest_price_date"]
            if latest_price_date is None:
                return {"ticker": normalized_ticker, "status": "NO_DATA", "runs": []}

            prior_runs = connection.execute(
                text(
                    """
                    SELECT method, status
                    FROM anomaly_model_runs
                    WHERE ticker = :ticker
                      AND model_version = :model_version
                      AND latest_price_date = :latest_price_date
                    """
                ),
                {
                    "ticker": normalized_ticker,
                    "model_version": MODEL_VERSION,
                    "latest_price_date": latest_price_date,
                },
            ).mappings().all()
            if not force and {row["method"] for row in prior_runs} == {
                "rolling_z_score",
                "relative_volume",
                "local_outlier_factor",
            }:
                return {
                    "ticker": normalized_ticker,
                    "status": "CACHED",
                    "runs": [dict(row) for row in prior_runs],
                }

            rows = [
                dict(row)
                for row in connection.execute(
                    text(INDICATOR_SQL),
                    {
                        "ticker": normalized_ticker,
                        "return_history_days": RULES["return_history_days"],
                        "volume_history_days": RULES["volume_history_days"],
                        "volatility_days": RULES["volatility_days"],
                        "volatility_baseline_days": RULES["volatility_baseline_days"],
                    },
                ).mappings()
            ]
            if rows:
                first_date = connection.execute(
                    text(
                        """
                        SELECT MIN(price_date) FROM stock_prices
                        WHERE ticker = :ticker
                        """
                    ),
                    {"ticker": normalized_ticker},
                ).scalar_one()
                for row in rows:
                    row["historical_start_date"] = first_date
            analyses = analyze_rows(rows)
            run_summaries = []
            for analysis in analyses:
                results = analysis["results"]
                train_start = (
                    results[0]["training_start"] if results else None
                )
                train_end = results[0]["training_end"] if results else None
                scoring_start = results[0]["price_date"] if results else None
                scoring_end = results[-1]["price_date"] if results else None
                run = connection.execute(
                    text(
                        """
                        INSERT INTO anomaly_model_runs (
                            ticker, method, model_version, status, latest_price_date,
                            training_start, training_end, scoring_start, scoring_end,
                            training_observations, evaluation_observations,
                            parameters, message, processed_at
                        )
                        VALUES (
                            :ticker, :method, :model_version, :status, :latest_price_date,
                            :training_start, :training_end, :scoring_start, :scoring_end,
                            :training_observations, :evaluation_observations,
                            CAST(:parameters AS JSONB), :message, NOW()
                        )
                        ON CONFLICT (ticker, method, model_version, latest_price_date)
                        DO UPDATE SET
                            status = EXCLUDED.status,
                            training_start = EXCLUDED.training_start,
                            training_end = EXCLUDED.training_end,
                            scoring_start = EXCLUDED.scoring_start,
                            scoring_end = EXCLUDED.scoring_end,
                            training_observations = EXCLUDED.training_observations,
                            evaluation_observations = EXCLUDED.evaluation_observations,
                            parameters = EXCLUDED.parameters,
                            message = EXCLUDED.message,
                            processed_at = NOW()
                        RETURNING run_id
                        """
                    ),
                    {
                        "ticker": normalized_ticker,
                        "method": analysis["method"],
                        "model_version": MODEL_VERSION,
                        "status": analysis["status"],
                        "latest_price_date": latest_price_date,
                        "training_start": train_start,
                        "training_end": train_end,
                        "scoring_start": scoring_start,
                        "scoring_end": scoring_end,
                        "training_observations": analysis["training_observations"],
                        "evaluation_observations": analysis["evaluation_observations"],
                        "parameters": json.dumps(_json_safe(analysis["parameters"])),
                        "message": analysis["message"],
                    },
                ).mappings().one()
                connection.execute(
                    text(
                        """
                        DELETE FROM anomaly_model_results
                        WHERE ticker = :ticker AND method = :method
                          AND model_version = :model_version
                        """
                    ),
                    {
                        "ticker": normalized_ticker,
                        "method": analysis["method"],
                        "model_version": MODEL_VERSION,
                    },
                )
                if results:
                    connection.execute(
                        text(
                            """
                            INSERT INTO anomaly_model_results (
                                ticker, price_date, method, model_version,
                                anomaly_score, threshold, classification,
                                training_start, training_end, parameters, features
                            )
                            VALUES (
                                :ticker, :price_date, :method, :model_version,
                                :anomaly_score, :threshold, :classification,
                                :training_start, :training_end,
                                CAST(:parameters AS JSONB), CAST(:features AS JSONB)
                            )
                            ON CONFLICT (ticker, price_date, method, model_version)
                            DO UPDATE SET
                                anomaly_score = EXCLUDED.anomaly_score,
                                threshold = EXCLUDED.threshold,
                                classification = EXCLUDED.classification,
                                training_start = EXCLUDED.training_start,
                                training_end = EXCLUDED.training_end,
                                parameters = EXCLUDED.parameters,
                                features = EXCLUDED.features,
                                scored_at = NOW()
                            """
                        ),
                        [
                            {
                                **result,
                                "parameters": json.dumps(result["parameters"]),
                                "features": json.dumps(result["features"]),
                            }
                            for result in results
                        ],
                    )
                run_summaries.append(
                    {
                        "run_id": run["run_id"],
                        "method": analysis["method"],
                        "status": analysis["status"],
                        "evaluation_observations": analysis["evaluation_observations"],
                    }
                )
            return {"ticker": normalized_ticker, "status": "PROCESSED", "runs": run_summaries}
    finally:
        if owns_engine:
            db_engine.dispose()
