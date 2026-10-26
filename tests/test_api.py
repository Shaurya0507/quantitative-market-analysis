from datetime import date
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.app import main as api_main
from backend.app.main import app

client = TestClient(app)
START_DATE = date(2026, 6, 3)
END_DATE = date(2026, 10, 23)


def test_list_stocks_returns_ingested_stocks():
    response = client.get("/stocks")

    assert response.status_code == 200
    stocks = response.json()
    by_ticker = {stock["ticker"]: stock for stock in stocks}
    assert {"AAPL", "MSFT", "NVDA"} <= by_ticker.keys()
    synthetic = [stock for stock in stocks if stock["ticker"].startswith("SYN")]
    assert not synthetic


def test_frontend_origin_is_allowed_by_cors():
    response = client.options(
        "/stocks",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"

    post_response = client.options(
        "/stocks/AAPL/models/reprocess",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert post_response.status_code == 200
    assert post_response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_dashboard_session_registers_the_supplied_session_id(monkeypatch):
    session_id = uuid4()
    registered = []
    monkeypatch.setattr(
        api_main,
        "register_dashboard_session",
        lambda engine, value: registered.append((engine, value)) or (True, 3),
    )

    response = client.post("/dashboard/session", json={"session_id": str(session_id)})

    assert response.status_code == 200
    assert response.json() == {
        "session_id": str(session_id),
        "cache_reset": True,
        "deleted_price_records": 3,
    }
    assert registered == [(api_main.engine, session_id)]


def test_health_check_confirms_database_connectivity():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_anomaly_summary_reports_the_latest_real_market_price_date():
    with api_main.engine.connect() as connection:
        latest_price_date = connection.execute(
            text(
                """
                SELECT MAX(p.price_date)
                FROM stock_prices p
                JOIN stocks s ON s.ticker = p.ticker
                WHERE s.is_synthetic = FALSE
                """
            )
        ).scalar_one()

    response = client.get("/anomalies/summary")

    assert response.status_code == 200
    assert response.json()["latest_data_date"] == latest_price_date.isoformat()


def test_get_stock_is_case_insensitive_and_unknown_stock_is_not_found():
    response = client.get("/stocks/aapl")

    assert response.status_code == 200
    assert response.json()["company_name"] == "Apple Inc."
    assert client.get("/stocks/UNKNOWN").status_code == 404
    assert client.get("/stocks/not%20valid").status_code == 422


def test_history_and_moving_average_return_ordered_daily_records():
    params = {"start_date": START_DATE.isoformat(), "end_date": END_DATE.isoformat()}

    history = client.get("/stocks/AAPL/history", params=params)
    averages = client.get("/stocks/AAPL/moving-average", params=params)

    assert history.status_code == 200
    assert len(history.json()) == 100
    assert history.json()[0]["price_date"] == START_DATE.isoformat()
    assert history.json()[-1]["price_date"] == END_DATE.isoformat()
    assert averages.status_code == 200
    assert len(averages.json()) == 100
    assert float(averages.json()[0]["moving_average_7"]) > 0


def test_analytics_validate_ranges_and_return_results():
    params = {"start_date": START_DATE.isoformat(), "end_date": END_DATE.isoformat()}

    volatility = client.get("/stocks/AAPL/volatility", params=params)
    performers = client.get("/market/top-performers", params=params)
    invalid_range = client.get(
        "/stocks/AAPL/history",
        params={
            "start_date": END_DATE.isoformat(),
            "end_date": START_DATE.isoformat(),
        },
    )

    assert volatility.status_code == 200
    assert volatility.json()["volatility_pct"] is not None
    ranked = performers.json()
    ranked_tickers = {item["ticker"] for item in ranked}
    assert {"AAPL", "MSFT", "NVDA"} <= ranked_tickers
    assert not any(ticker.startswith("SYN") for ticker in ranked_tickers)
    ranks = [item["performance_rank"] for item in ranked]
    assert ranks[0] == 1
    assert ranks == sorted(ranks)
    assert invalid_range.status_code == 422


def test_stock_search_includes_unloaded_supported_companies():
    response = client.get("/stocks/search", params={"q": "tesla"})

    assert response.status_code == 200
    assert response.json() == [
        {
            "ticker": "TSLA",
            "company_name": "Tesla, Inc.",
            "sector": "Consumer Discretionary",
            "exchange": "NASDAQ",
            "is_loaded": False,
            "latest_available_date": None,
            "ingestion_status": "NOT_LOADED",
            "data_source": "alphavantage",
            "historical_data_available": False,
        }
    ]


def test_load_accepts_valid_tickers_outside_the_directory(monkeypatch):
    job = {
        "job_id": str(uuid4()),
        "ticker": "NEWCO",
        "status": "QUEUED",
        "attempt_count": 0,
        "last_error": None,
        "records_upserted": None,
    }
    monkeypatch.setattr(api_main, "enqueue_ingestion", lambda _: (job, True))

    response = client.post("/stocks/load", json={"ticker": "NEWCO"})

    assert response.status_code == 202
    assert response.json()["ticker"] == "NEWCO"


def test_company_directory_has_exactly_100_unique_catalog_tickers():
    response = client.get("/stocks/directory", params={"limit": 100})

    assert response.status_code == 200
    directory = response.json()
    tickers = [company["ticker"] for company in directory["items"]]
    assert directory["total"] == 100
    assert len(tickers) == len(set(tickers)) == 100
    assert len(directory["sectors"]) >= 8
    assert all("data_source" in company for company in directory["items"])
    assert all("historical_data_available" in company for company in directory["items"])


def test_company_directory_supports_search_sector_filter_and_pagination():
    first_page = client.get("/stocks/directory", params={"limit": 5})
    second_page = client.get("/stocks/directory", params={"limit": 5, "offset": 5})
    filtered = client.get(
        "/stocks/directory",
        params={"q": "Home Depot", "sector": "Consumer Discretionary"},
    )

    first_tickers = [row["ticker"] for row in first_page.json()["items"]]
    second_tickers = [row["ticker"] for row in second_page.json()["items"]]
    assert first_page.status_code == second_page.status_code == 200
    assert set(first_tickers).isdisjoint(second_tickers)
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["ticker"] == "HD"
    assert filtered.json()["items"][0]["historical_data_available"] is True


def test_synthetic_benchmark_tickers_are_not_public_stock_data():
    assert client.get("/stocks/SYN000").status_code == 404
    assert client.get(
        "/stocks/SYN000/history",
        params={"start_date": START_DATE, "end_date": END_DATE},
    ).status_code == 404
    assert client.get(
        "/market/top-performers",
        params={"start_date": START_DATE, "end_date": END_DATE},
    ).status_code == 200


def test_anomaly_endpoints_validate_filters_and_exclude_synthetic_data():
    processed = client.post("/anomalies/AAPL/reprocess")
    assert processed.status_code == 200
    anomalies = client.get("/anomalies", params={"limit": 100})
    high_severity = client.get("/anomalies", params={"severity": "HIGH"})
    indicators = client.get(
        "/stocks/AAPL/indicators",
        params={"start_date": START_DATE, "end_date": END_DATE},
    )
    invalid_severity = client.get("/anomalies", params={"severity": "CRITICAL"})

    assert anomalies.status_code == 200
    assert all(item["model_version"] == api_main.ANOMALY_VERSION for item in anomalies.json())
    assert all(not item["ticker"].startswith("SYN") for item in anomalies.json())
    assert high_severity.status_code == 200
    assert all(item["severity"] == "HIGH" for item in high_severity.json())
    assert invalid_severity.status_code == 422
    assert indicators.status_code == 200
    assert len(indicators.json()) == 99
    assert indicators.json()[0]["price_date"] > START_DATE.isoformat()
    assert indicators.json()[0]["return_z_score"] is None
    assert all(item["ticker"] == "AAPL" for item in indicators.json())
    assert {"volume", "historical_volume_mean", "historical_volatility_mean"} <= (
        indicators.json()[0].keys()
    )
    assert all(
        item["model_version"] in (None, api_main.ANOMALY_VERSION)
        for item in indicators.json()
    )


def test_reprocessing_anomalies_is_idempotent():
    before = client.get("/anomalies", params={"ticker": "AAPL", "limit": 100}).json()

    result = client.post("/anomalies/AAPL/reprocess")

    after = client.get("/anomalies", params={"ticker": "AAPL", "limit": 100}).json()
    assert result.status_code == 200
    assert result.json()["anomalies_stored"] == len(after)
    assert len(after) == len(before)


def test_volatility_expansion_alone_does_not_trigger_a_crm_anomaly():
    processed = client.post("/anomalies/CRM/reprocess")
    response = client.get(
        "/stocks/CRM/indicators",
        params={"start_date": "2026-09-01", "end_date": "2026-09-02"},
    )
    anomalies = client.get("/anomalies/CRM", params={"limit": 100})

    assert processed.status_code == 200
    assert response.status_code == 200
    assert len(response.json()) == 2
    assert all(not row["is_anomaly"] for row in response.json())
    assert all(float(row["volatility_expansion"]) > 2 for row in response.json())
    assert len(anomalies.json()) == processed.json()["anomalies_stored"]
    assert all(row["model_version"] == api_main.ANOMALY_VERSION for row in anomalies.json())
    assert all(
        detail["method"] in {"rolling_z_score", "relative_volume"}
        for anomaly in anomalies.json()
        for detail in anomaly["detection_details"]
    )


def test_model_reprocessing_forces_a_fresh_analysis(monkeypatch):
    calls = []
    monkeypatch.setattr(
        api_main,
        "process_ticker_models",
        lambda ticker, **kwargs: calls.append((ticker, kwargs)) or {"status": "PROCESSED"},
    )

    response = client.post("/stocks/AAPL/models/reprocess")

    assert response.status_code == 200
    assert calls == [("AAPL", {"force": True})]


def test_model_results_include_active_methods_and_history_limits():
    reprocessed = client.post("/stocks/AAPL/models/reprocess")
    response = client.get("/stocks/AAPL/models")

    assert reprocessed.status_code == 200
    assert response.status_code == 200
    methods = {item["method"]: item for item in response.json()["methods"]}
    assert set(methods) == {
        "rolling_z_score",
        "relative_volume",
        "local_outlier_factor",
    }
    assert methods["rolling_z_score"]["status"] == "SUCCEEDED"
    assert methods["rolling_z_score"]["results"]
    assert methods["relative_volume"]["status"] == "SUCCEEDED"
    assert methods["relative_volume"]["results"]
    assert methods["local_outlier_factor"]["status"] == "SUCCEEDED"
    assert methods["local_outlier_factor"]["run"]["training_observations"] == 50
    assert methods["local_outlier_factor"]["run"]["evaluation_observations"] >= 10


def test_hd_ingestion_status_includes_the_latest_job():
    response = client.get("/stocks/HD/status")

    assert response.status_code == 200
    status_data = response.json()
    assert status_data["ticker"] == "HD"
    assert status_data["job"] is None or status_data["job"]["ticker"] == "HD"


def test_ingestion_job_status_returns_404_for_a_missing_job():
    response = client.get(f"/ingestion/jobs/{uuid4()}")

    assert response.status_code == 404


def test_load_stock_returns_created_or_reused_job(monkeypatch):
    job = {
        "job_id": str(uuid4()),
        "ticker": "HD",
        "status": "QUEUED",
        "attempt_count": 0,
        "last_error": None,
        "records_upserted": None,
    }
    responses = [(job, True), (job, False)]
    monkeypatch.setattr(api_main, "enqueue_ingestion", lambda _: responses.pop(0))

    first = client.post("/stocks/load", json={"ticker": "HD"})
    second = client.post("/stocks/load", json={"ticker": "HD"})

    assert first.status_code == second.status_code == 202
    assert first.json()["job_id"] == second.json()["job_id"]
    assert "already has an active ingestion job" in second.json()["message"]


def test_load_stock_reports_cached_postgresql_data(monkeypatch):
    cached_job = {
        "job_id": None,
        "ticker": "AAPL",
        "status": "CACHED",
        "attempt_count": 0,
        "last_error": None,
        "records_upserted": None,
    }
    monkeypatch.setattr(api_main, "enqueue_ingestion", lambda _: (cached_job, False))

    response = client.post("/stocks/load", json={"ticker": "AAPL"})

    assert response.status_code == 202
    assert response.json()["status"] == "CACHED"
    assert response.json()["job_id"] is None


def test_load_loaded_stock_uses_postgresql_cache():
    response = client.post("/stocks/load", json={"ticker": "AAPL"})

    assert response.status_code == 202
    assert response.json()["status"] == "CACHED"
    assert response.json()["job_id"] is None
